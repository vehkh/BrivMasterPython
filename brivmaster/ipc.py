"""Local IPC replacing the AHK COM active objects (ObjRegisterActive /
ComObjActive).

JSON-lines over a localhost TCP socket with a shared random token. The farm
process runs the server and exposes named objects ("scopes": the SharedData
and the RelaySharedData); the Home GUI and the relay helper connect as
clients. The endpoint (port + token) is written to a file next to the
settings - the equivalent of LastGUID_IBM_GemFarm.json, and what makes the
Home 'Reconnect' button work across restarts.

Operations:
    {"scope": s, "op": "get",  "name": attr}
    {"scope": s, "op": "set",  "name": attr, "value": v}
    {"scope": s, "op": "call", "name": method, "args": [...]}
    {"scope": s, "op": "snapshot"}          # all public JSON-able attributes

Note: 'call' executes on the server thread. The farm registers only methods
that are safe there (run-control toggles, relay release paths); anything
touching input goes through ctx.critical.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
import threading

ENDPOINT_FILE_NAME = "LastEndpoint_IBM_GemFarm.json"


def endpoint_file_path(base_dir=None):
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, ENDPOINT_FILE_NAME)


def _jsonable(value):
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


class IpcServer:
    def __init__(self, base_dir=None):
        self._scopes = {}
        self._allowed_calls = {}
        self.token = secrets.token_hex(16)
        self._endpoint_path = endpoint_file_path(base_dir)
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    try:
                        reply = outer._dispatch(json.loads(line))
                    except Exception as err:  # noqa: BLE001
                        reply = {"ok": False, "error": f"{type(err).__name__}: {err}"}
                    self.wfile.write(json.dumps(reply).encode("utf-8") + b"\n")
                    self.wfile.flush()

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._server = Server(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="IpcServer")

    def register(self, scope, obj, allowed_calls=()):
        """Expose an object under a scope name. Attribute get/set is open for
        public (non underscore-prefixed) names; calls are allowlisted."""
        self._scopes[scope] = obj
        self._allowed_calls[scope] = set(allowed_calls)

    def start(self):
        self._thread.start()
        with open(self._endpoint_path, "w", encoding="utf-8") as f:
            json.dump({"port": self.port, "token": self.token,
                       "pid": os.getpid()}, f)

    def close(self):
        try:
            self._server.shutdown()
            self._server.server_close()
        except OSError:
            pass
        try:
            os.unlink(self._endpoint_path)
        except OSError:
            pass

    def _dispatch(self, request):
        if request.get("token") != self.token:
            return {"ok": False, "error": "bad token"}
        obj = self._scopes.get(request.get("scope"))
        if obj is None:
            return {"ok": False, "error": f"no scope {request.get('scope')}"}
        op = request.get("op")
        name = request.get("name", "")
        if name.startswith("_"):
            return {"ok": False, "error": "private name"}
        if op == "get":
            value = getattr(obj, name, None)
            return {"ok": True, "value": value if _jsonable(value) else str(value)}
        if op == "set":
            setattr(obj, name, request.get("value"))
            return {"ok": True}
        if op == "call":
            if name not in self._allowed_calls.get(request.get("scope"), ()):
                return {"ok": False, "error": f"call not allowed: {name}"}
            result = getattr(obj, name)(*request.get("args", []))
            return {"ok": True,
                    "value": result if _jsonable(result) else str(result)}
        if op == "snapshot":
            snapshot = {}
            for attr in dir(obj):
                if attr.startswith("_"):
                    continue
                value = getattr(obj, attr)
                if callable(value):
                    continue
                snapshot[attr] = value if _jsonable(value) else str(value)
            return {"ok": True, "value": snapshot}
        return {"ok": False, "error": f"bad op {op}"}


class IpcError(Exception):
    pass


class IpcClient:
    def __init__(self, port=None, token=None, base_dir=None, timeout=5.0):
        if port is None:
            path = endpoint_file_path(base_dir)
            with open(path, "r", encoding="utf-8") as f:
                endpoint = json.load(f)
            port, token = endpoint["port"], endpoint["token"]
        self._address = ("127.0.0.1", port)
        self._token = token
        self._timeout = timeout
        self._sock = None
        self._file = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._sock is None:
            self._sock = socket.create_connection(self._address, self._timeout)
            self._sock.settimeout(self._timeout)
            self._file = self._sock.makefile("rwb")

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._file = None

    def _request(self, payload):
        payload["token"] = self._token
        with self._lock:
            try:
                self._connect()
                self._file.write(json.dumps(payload).encode("utf-8") + b"\n")
                self._file.flush()
                line = self._file.readline()
            except (OSError, ValueError) as err:
                self.close()
                raise IpcError(str(err)) from err
        if not line:
            self.close()
            raise IpcError("connection closed")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise IpcError(reply.get("error", "unknown error"))
        return reply.get("value")

    def get(self, scope, name):
        return self._request({"scope": scope, "op": "get", "name": name})

    def set(self, scope, name, value):
        return self._request({"scope": scope, "op": "set", "name": name,
                              "value": value})

    def call(self, scope, name, *args):
        return self._request({"scope": scope, "op": "call", "name": name,
                              "args": list(args)})

    def snapshot(self, scope):
        return self._request({"scope": scope, "op": "snapshot"})

    def ping(self):
        try:
            self._request({"scope": "control", "op": "get", "name": "alive"})
            return True
        except IpcError:
            return False
