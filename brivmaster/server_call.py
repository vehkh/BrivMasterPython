"""Port of IBM_ServerCall_Class / IC_BrivMaster_ServerCall_Class
(IC_BrivMaster_SharedFunctions.ahk) and of the Lib zlib/JSON helpers.

The AHK 'Budget Zlib' hand-rolls an RFC1950/1951 fixed-Huffman stream and
base64s it; Python's zlib produces a compatible stream, so Deflate/Inflate
collapse to two lines. WinHttp COM becomes urllib. Failed calls return None
(the AHK "").
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zlib

PLAY_SERVER_REGEX = re.compile(r"^https?://ps(?:lt)?\d+\.idlechampions\.com/~idledragons/")
MASTER_SERVER = "http://master.idlechampions.com/~idledragons/"
# Hard-coded fallback pool, current as of 2026-06-27 (from the AHK original)
CURRENT_PLAY_SERVERS = ["27", "28", "29", "30", "lt1", "lt2", "lt3"]
MD5_SALT = "somethingpoliticallycorrect"


def deflate_b64(text):
    """g_zlib.Deflate port: base64 of a zlib stream of the text."""
    return base64.b64encode(zlib.compress(text.encode("utf-8"), 9)).decode("ascii")


def inflate_b64(data):
    """g_zlib.Inflate port."""
    try:
        return zlib.decompress(base64.b64decode(data)).decode("utf-8")
    except (ValueError, zlib.error):
        return None


class ServerCall:
    def __init__(self, memory=None, logger=None):
        self.memory = memory          # MemoryFunctions; can be set later
        self.logger = logger
        self.userID = 0
        self.userHash = ""
        self.instanceID = 0
        self.networkID = 11
        self.clientVersion = 999
        self.activeModronID = 1
        self.activePatronID = 0
        self.webRoot = ""
        self.dummyData = ""
        self.sprint = None            # Briv haste stacks ('BrivSprintStacks' in game)
        self.steelbones = None
        # Hooks the farm layer sets up later:
        self.stack_reader = None      # zero-arg callable -> (haste, steelbones)
        self.stack_conversion_rate = 1.0  # RouteMaster's Thunder Step factor

    def _log(self, message):
        if self.logger:
            self.logger.AddMessage(message)

    # --- state refresh -------------------------------------------------------

    def UpdatePlayServer(self):
        new_read = self.memory.ReadWebRoot() if self.memory else None
        if new_read and PLAY_SERVER_REGEX.match(new_read):
            self.webRoot = new_read
            return
        if self.webRoot and PLAY_SERVER_REGEX.match(self.webRoot):
            return  # existing webRoot is valid, keep it
        # Ask master for a play server; it allows redirects on future calls
        self.webRoot = MASTER_SERVER
        response = self.ServerCall("getPlayServerForDefinitions", "")
        if response and PLAY_SERVER_REGEX.match(str(response.get("play_server", ""))):
            self.webRoot = response["play_server"]
            return
        server = random.choice(CURRENT_PLAY_SERVERS)
        self.webRoot = f"http://ps{server}.idlechampions.com/~idledragons/"

    def UpdateStackData(self):
        if self.memory:
            new_read = self.memory.ReadInstanceID()
            if new_read:
                self.instanceID = new_read
        if self.stack_reader:
            self.sprint, self.steelbones = self.stack_reader()

    def Update(self):
        if self.memory:
            for attr, reader in (("userID", self.memory.ReadUserID),
                                 ("userHash", self.memory.ReadUserHash),
                                 ("clientVersion", self.memory.ReadBaseGameVersion),
                                 ("networkID", self.memory.ReadPlatform),
                                 ("activeModronID", self.memory.ReadActiveGameInstance),
                                 ("activePatronID", self.memory.ReadPatronID)):
                new_read = reader()
                if new_read:
                    setattr(self, attr, new_read)
        self.UpdateStackData()
        self.UpdatePlayServer()
        self.dummyData = ("&language_id=1&timestamp=0&request_id=0&network_id="
                          f"{self.networkID}&mobile_client_version="
                          f"{self.clientVersion}&offline_v2_build=1")

    # --- Briv stack conversion ------------------------------------------------

    def ShouldCallPreventStackFail(self, force_save=False):
        if self.steelbones is None or self.sprint is None:
            return False  # can't put the conversion together
        if not force_save and self.steelbones == 0:
            return False  # no SB to convert, don't send a pointless save
        return True

    def CallPreventStackFail(self, message, launch_script=False):
        """Convert Steelbones to Haste server-side. Call after
        UpdateStackData() and a truthy ShouldCallPreventStackFail()."""
        stacks = self.sprint + int(self.steelbones * self.stack_conversion_rate)
        self._log(f"Servercall Save via: {message} Converted Haste=[{stacks}]"
                  f" from Haste=[{self.sprint}] and Steelbones=[{self.steelbones}]"
                  f" with stackConversionRate=[{round(self.stack_conversion_rate, 1)}]")
        json_string = json.dumps(
            {"stats": {"briv_steelbones_stacks": 0,
                       "briv_sprint_stacks": stacks}},
            separators=(",", ":"))
        boundary = self.GetBoundryHeader()
        save = self.GetSaveFromJSON(json_string, boundary)
        if launch_script:
            # Do the call from a separate process to prevent hanging the
            # script on network issues (the SaveStacks helper).
            body_file = tempfile.NamedTemporaryFile(
                "w", suffix=".txt", prefix="ibm_save_", delete=False,
                encoding="utf-8")
            body_file.write(save)
            body_file.close()
            subprocess.Popen([sys.executable, "-m", "brivmaster.save_stacks",
                              self.webRoot, body_file.name, boundary],
                             close_fds=True)
            return None
        try:
            return self.ServerCallSave(save, boundary)
        except Exception:  # noqa: BLE001 - network failure must not kill a run
            self._log("Failed to save Briv stacks")
            return None

    # --- low-level HTTP ------------------------------------------------------

    @staticmethod
    def _http(request, timeout_s):
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as reply:
                return reply.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def _post_json(self, url, timeout_s, data=None, headers=None):
        request = urllib.request.Request(url, method="POST", data=data)
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        raw = self._http(request, timeout_s)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def ServerCall(self, call_name, parameters, timeout=60000, retry_num=0):
        """Internal - use the Call* wrappers."""
        url = f"{self.webRoot}post.php?call={call_name}{parameters}"
        response = self._post_json(url, timeout / 1000)
        if response and response.get("switch_play_server"):
            self.webRoot = response["switch_play_server"]
            if retry_num + 1 <= 3:
                return self.ServerCall(call_name, parameters, timeout,
                                       retry_num + 1)
        return response

    def ServerCallSave(self, save_body, boundary_header, retry_num=0):
        """Special server call specifically for saves; body must be the
        pre-built multipart string."""
        url = f"{self.webRoot}post.php?call=saveuserdetails&"
        request = urllib.request.Request(url, method="POST",
                                         data=save_body.encode("utf-8"))
        request.add_header("Accept-Encoding", "identity")
        request.add_header("Content-Type",
                           f'multipart/form-data; boundary="{boundary_header}"')
        request.add_header("User-Agent", "BestHTTP")
        raw = self._http(request, 30)
        if not raw:
            return None
        try:
            response = json.loads(raw)
        except ValueError:
            return None
        if response and response.get("switch_play_server"):
            self.webRoot = response["switch_play_server"]
            if retry_num + 1 <= 3:
                return self.ServerCallSave(save_body, boundary_header,
                                           retry_num + 1)
        return response

    def BasicServerCall(self, url, timeout=60000):
        request = urllib.request.Request(url)
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Accept", "application/json")
        raw = self._http(request, timeout / 1000)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def BasicServerCallRaw(self, url, timeout=60000):
        """Does not parse as JSON."""
        request = urllib.request.Request(url)
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Accept", "application/json")
        return self._http(request, timeout / 1000)

    # --- save body construction --------------------------------------------------

    @staticmethod
    def MD5Save(string_value):
        """Salted md5 checksum for a save string."""
        return hashlib.md5(
            (string_value + MD5_SALT).encode("utf-8")).hexdigest()

    @staticmethod
    def GetBoundryHeader():
        return ("BestHTTP_HTTPMultiPartForm_"
                f"{random.randint(0, 0xFFFF):04X}{random.randint(0, 0xFFFF):04X}")

    def GetSaveFromJSON(self, json_string, boundary_header, time_stamp="0"):
        """Converts user's data into the multipart form body the game's save
        endpoint expects."""
        user_data = deflate_b64(json_string)
        checksum = self.MD5Save(json_string)

        def part(name, value):
            value = str(value)
            return (f"--{boundary_header}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n'
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(value)}\r\n\r\n"
                    f"{value}\r\n")

        body = part("call", "saveuserdetails")
        body += part("language_id", 1)
        body += part("user_id", self.userID)
        body += part("hash", self.userHash)
        body += part("details_compressed", user_data)
        body += part("checksum", checksum)
        body += part("timestamp", time_stamp)
        body += part("request_id", 1)
        body += part("network_id", self.networkID)
        body += part("mobile_client_version", self.clientVersion)
        body += part("instance_id", self.instanceID)
        body += f"--{boundary_header}--\r\n"
        return body

    # --- game-facing calls -----------------------------------------------------------

    def CallLoadAdventure(self, adventure_to_load):
        """Starts a new adventure and returns the response."""
        patron_tier = 1 if self.activePatronID else 0
        params = (f"{self.dummyData}&patron_tier={patron_tier}"
                  f"&user_id={self.userID}&hash={self.userHash}"
                  f"&instance_id={self.instanceID}"
                  f"&game_instance_id={self.activeModronID}"
                  f"&adventure_id={adventure_to_load}"
                  f"&patron_id={self.activePatronID}")
        return self.ServerCall("setcurrentobjective", params)

    def CallEndAdventure(self):
        """Loses everything earned this adventure - only for when stuck."""
        params = (f"{self.dummyData}&user_id={self.userID}"
                  f"&hash={self.userHash}&instance_id={self.instanceID}"
                  f"&game_instance_id={self.activeModronID}")
        return self.ServerCall("softreset", params)

    def CallBuyChests(self, chest_id, chests):
        """Buys chests; basic non-patron non-event chests only."""
        chests = min(chests, 250)
        if chests < 1:
            return None
        params = (f"{self.dummyData}&user_id={self.userID}"
                  f"&hash={self.userHash}&instance_id={self.instanceID}"
                  f"&chest_type_id={chest_id}&count={chests}")
        return self.ServerCall("buysoftcurrencychest", params)

    def CallOpenChests(self, chest_id, chests):
        chests = min(chests, 1000)
        if chests < 1:
            return None
        params = ("&gold_per_second=0"
                  "&checksum=4c5f019b6fc6eefa4d47d21cfaf1bc68"
                  f"&user_id={self.userID}&hash={self.userHash}"
                  f"&instance_id={self.instanceID}&chest_type_id={chest_id}"
                  f"&game_instance_id={self.activeModronID}&count={chests}")
        return self.ServerCall("opengenericchest", params, 60000)
