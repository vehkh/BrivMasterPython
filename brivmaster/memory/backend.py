"""Process memory access backends.

Port of IC_BrivMaster_Memory_Reader.ahk (_IC_BrivMaster_Memory_Reader_Class).

The Linux backend is the primary target: Idle Champions runs under
Wine/Proton, so the game is still IdleDragons.exe and mono-2.0-bdwgc.dll is
still a module inside the process - we just read it through process_vm_readv
(or /proc/<pid>/mem) instead of ReadProcessMemory.

A Windows backend is kept so the port can be validated side-by-side against
the AHK original on a Windows machine.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys

# Read sizes per value type - matches aTypeSize in the AHK reader.
TYPE_FORMATS = {
    "UChar": "B", "Char": "b",
    "UShort": "H", "Short": "h",
    "UInt": "I", "Int": "i",
    "UFloat": "f", "Float": "f",
    "Int64": "q", "UInt64": "Q",
    "Double": "d",
}
TYPE_SIZES = {k: struct.calcsize(v) for k, v in TYPE_FORMATS.items()}


class ProcessMemory:
    """Common typed-read layer over a raw read_bytes primitive."""

    pid = 0
    attached = False

    def read_bytes(self, address, size):
        raise NotImplementedError

    def module_base(self, module_name):
        raise NotImplementedError

    def is_running(self):
        raise NotImplementedError

    def suspend(self):
        raise NotImplementedError

    def resume(self):
        raise NotImplementedError

    def close(self):
        pass

    def read(self, address, value_type="UInt"):
        fmt = TYPE_FORMATS.get(value_type)
        if fmt is None or address is None or address <= 0:
            return None
        raw = self.read_bytes(address, TYPE_SIZES[value_type])
        if raw is None:
            return None
        return struct.unpack("<" + fmt, raw)[0]

    def read_pointer(self, address):
        return self.read(address, "Int64")

    def resolve(self, base_address, offsets):
        """Walk a pointer chain: every offset is preceded by a dereference.

        Returns the final address holding the value (the AHK
        getAddressFromOffsets semantics), or None if any link is unreadable.
        """
        if base_address is None or base_address <= 0:
            return None
        addr = base_address
        for off in offsets:
            ptr = self.read(addr, "Int64")
            if ptr is None or ptr <= 0:
                return None
            addr = ptr + off
        return addr

    def read_chain(self, base_address, offsets, value_type="UInt"):
        if not offsets:
            return self.read(base_address, value_type)
        addr = self.resolve(base_address, offsets)
        if addr is None:
            return None
        return self.read(addr, value_type)

    def read_mono_string(self, string_object_address, max_chars=8192):
        """Read a .NET/mono string object: Int32 length at +0x10, UTF-16
        characters at +0x14 (64-bit mono layout, as the AHK reader assumes)."""
        if string_object_address is None or string_object_address <= 0:
            return None
        length = self.read(string_object_address + 0x10, "Int")
        if length is None or length < 0 or length > max_chars:
            return None
        if length == 0:
            return ""
        raw = self.read_bytes(string_object_address + 0x14, length * 2)
        if raw is None:
            return None
        return raw.decode("utf-16-le", errors="replace")


class LinuxProcessMemory(ProcessMemory):
    """Reads another process via process_vm_readv, falling back to
    /proc/<pid>/mem. Requires ptrace permission for the target
    (kernel.yama.ptrace_scope=0, CAP_SYS_PTRACE, or same-user with scope 1
    only for children - see the README)."""

    def __init__(self, pid):
        self.pid = int(pid)
        self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
        self._mem_fd = None
        self._use_pvr = hasattr(self._libc, "process_vm_readv")

        class IOVec(ctypes.Structure):
            _fields_ = [("iov_base", ctypes.c_void_p),
                        ("iov_len", ctypes.c_size_t)]

        self._IOVec = IOVec
        self.attached = self.is_running()

    @staticmethod
    def find_pids(exe_name):
        """Find PIDs whose executable name matches (case-insensitive).
        Handles Wine processes where cmdline[0] is a Windows path."""
        exe_lower = exe_name.lower()
        pids = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    cmdline = f.read().split(b"\0")
                name = ""
                if cmdline and cmdline[0]:
                    arg0 = cmdline[0].decode("utf-8", errors="replace")
                    # Wine passes Windows paths; strip both separator styles
                    name = arg0.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if name != exe_lower:
                    # comm is truncated to 15 chars ("IdleDragons.exe" fits exactly)
                    with open(f"/proc/{entry}/comm", "r") as f:
                        comm = f.read().strip().lower()
                    if comm != exe_lower and not (len(comm) == 15 and exe_lower.startswith(comm)):
                        continue
                pids.append(int(entry))
            except (OSError, PermissionError):
                continue
        return pids

    def is_running(self):
        return os.path.isdir(f"/proc/{self.pid}")

    def module_base(self, module_name):
        """Lowest mapping address of the named module. Wine maps PE DLLs from
        their on-disk file, so mono-2.0-bdwgc.dll appears in /proc/pid/maps."""
        target = module_name.lower()
        base = None
        try:
            with open(f"/proc/{self.pid}/maps", "r") as f:
                for line in f:
                    parts = line.split(None, 5)
                    if len(parts) < 6:
                        continue
                    path = parts[5].strip()
                    if path.rsplit("/", 1)[-1].lower() == target:
                        start = int(parts[0].split("-", 1)[0], 16)
                        if base is None or start < base:
                            base = start
        except OSError:
            return -1
        return base if base is not None else -1

    def read_bytes(self, address, size):
        if address is None or address <= 0 or size <= 0:
            return None
        if self._use_pvr:
            buf = ctypes.create_string_buffer(size)
            local = self._IOVec(ctypes.cast(buf, ctypes.c_void_p), size)
            remote = self._IOVec(ctypes.c_void_p(address), size)
            nread = self._libc.process_vm_readv(
                self.pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
            if nread == size:
                return buf.raw
            err = ctypes.get_errno()
            if err in (1, 3):  # EPERM / ESRCH - don't retry via /proc, same rules
                if err == 1:
                    raise PermissionError(
                        "process_vm_readv denied (EPERM). Set "
                        "kernel.yama.ptrace_scope=0 or grant CAP_SYS_PTRACE "
                        "- see PyBrivMaster README.")
                self.attached = self.is_running()
                return None
            return None
        return self._read_proc_mem(address, size)

    def _read_proc_mem(self, address, size):
        try:
            if self._mem_fd is None:
                self._mem_fd = os.open(f"/proc/{self.pid}/mem", os.O_RDONLY)
            raw = os.pread(self._mem_fd, size, address)
            return raw if len(raw) == size else None
        except PermissionError:
            raise PermissionError(
                "/proc/{}/mem denied. Set kernel.yama.ptrace_scope=0 or grant "
                "CAP_SYS_PTRACE - see PyBrivMaster README.".format(self.pid))
        except OSError:
            return None

    def suspend(self):
        # Replaces NtSuspendProcess; SIGSTOP halts the whole Wine process.
        try:
            os.kill(self.pid, 19)  # SIGSTOP
            return True
        except OSError:
            return False

    def resume(self):
        try:
            os.kill(self.pid, 18)  # SIGCONT
            return True
        except OSError:
            return False

    def close(self):
        if self._mem_fd is not None:
            try:
                os.close(self._mem_fd)
            except OSError:
                pass
            self._mem_fd = None
        self.attached = False


class WindowsProcessMemory(ProcessMemory):
    """ReadProcessMemory-based backend, used to validate the port against the
    AHK original on Windows. Mirrors the access rights the AHK reader requests."""

    _ACCESS = (0x0400  # PROCESS_QUERY_INFORMATION
               | 0x0010  # PROCESS_VM_READ
               | 0x0008  # PROCESS_VM_OPERATION
               | 0x0800  # PROCESS_SUSPEND_RESUME
               | 0x00100000)  # SYNCHRONIZE

    def __init__(self, pid):
        if struct.calcsize("P") != 8:
            raise RuntimeError("64-bit Python is required (the game is 64-bit)")
        self.pid = int(pid)
        self._k32 = k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._ntdll = ctypes.WinDLL("ntdll")
        # 64-bit handles/addresses require explicit prototypes; the ctypes
        # default of c_int would truncate them.
        HANDLE, PVOID = ctypes.c_void_p, ctypes.c_void_p
        k32.OpenProcess.restype = HANDLE
        k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k32.ReadProcessMemory.argtypes = [HANDLE, PVOID, PVOID,
                                          ctypes.c_size_t,
                                          ctypes.POINTER(ctypes.c_size_t)]
        k32.K32EnumProcessModulesEx.argtypes = [HANDLE, PVOID, ctypes.c_uint32,
                                                ctypes.POINTER(ctypes.c_uint32),
                                                ctypes.c_uint32]
        k32.K32GetModuleFileNameExW.argtypes = [HANDLE, PVOID, ctypes.c_wchar_p,
                                                ctypes.c_uint32]
        k32.WaitForSingleObject.argtypes = [HANDLE, ctypes.c_uint32]
        k32.CloseHandle.argtypes = [HANDLE]
        self._ntdll.NtSuspendProcess.argtypes = [HANDLE]
        self._ntdll.NtResumeProcess.argtypes = [HANDLE]
        self._handle = k32.OpenProcess(self._ACCESS, False, self.pid)
        self.attached = bool(self._handle)

    @staticmethod
    def find_pids(exe_name):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_uint32),
                        ("cntUsage", ctypes.c_uint32),
                        ("th32ProcessID", ctypes.c_uint32),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", ctypes.c_uint32),
                        ("cntThreads", ctypes.c_uint32),
                        ("th32ParentProcessID", ctypes.c_uint32),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.c_uint32),
                        ("szExeFile", ctypes.c_wchar * 260)]

        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.Process32FirstW.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [ctypes.c_void_p,
                                       ctypes.POINTER(PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        snapshot = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return []
        pids = []
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        exe_lower = exe_name.lower()
        if k32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.lower() == exe_lower:
                    pids.append(entry.th32ProcessID)
                if not k32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        k32.CloseHandle(snapshot)
        return pids

    def is_running(self):
        if not self._handle:
            return False
        # WAIT_TIMEOUT (0x102) means still running - same check as isHandleValid()
        return self._k32.WaitForSingleObject(self._handle, 0) == 0x102

    def module_base(self, module_name):
        if not self._handle:
            return -1
        needed = ctypes.c_uint32(0)
        # LIST_MODULES_ALL = 0x03
        self._k32.K32EnumProcessModulesEx(self._handle, None, 0,
                                          ctypes.byref(needed), 0x03)
        count = needed.value // ctypes.sizeof(ctypes.c_void_p)
        if count == 0:
            return -1
        modules = (ctypes.c_void_p * count)()
        if not self._k32.K32EnumProcessModulesEx(
                self._handle, modules, ctypes.sizeof(modules),
                ctypes.byref(needed), 0x03):
            return -1
        name_buf = ctypes.create_unicode_buffer(2048)
        target = module_name.lower()
        for module in modules:
            if not module:
                continue
            self._k32.K32GetModuleFileNameExW(self._handle, module,
                                              name_buf, 2048)
            if name_buf.value.replace("\\", "/").rsplit("/", 1)[-1].lower() == target:
                return module
        return -1

    def read_bytes(self, address, size):
        if not self._handle or address is None or address <= 0 or size <= 0:
            return None
        buf = ctypes.create_string_buffer(size)
        nread = ctypes.c_size_t(0)
        ok = self._k32.ReadProcessMemory(
            self._handle, ctypes.c_void_p(address), buf, size,
            ctypes.byref(nread))
        if not ok or nread.value != size:
            return None
        return buf.raw

    def suspend(self):
        return self._ntdll.NtSuspendProcess(self._handle) == 0

    def resume(self):
        return self._ntdll.NtResumeProcess(self._handle) == 0

    def close(self):
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None
        self.attached = False


def native_backend():
    """The backend class for the current platform."""
    return WindowsProcessMemory if sys.platform == "win32" else LinuxProcessMemory


def attach_to(exe_name, pid=None):
    """Find the game and return an attached backend, or None."""
    cls = native_backend()
    if pid is None:
        pids = cls.find_pids(exe_name)
        if not pids:
            return None
        pid = pids[0]
    backend = cls(pid)
    return backend if backend.attached else None
