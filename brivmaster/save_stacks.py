"""Port of IC_BrivMaster_SaveStacks.ahk - a fire-and-forget helper process
that posts a pre-built save body, so network stalls can't hang the farm.

Usage (spawned by ServerCall.CallPreventStackFail):
    python -m brivmaster.save_stacks <webroot> <body-file> <boundary>

The body is passed via a temp file (it is far too large for argv); this
helper deletes it when done.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def server_call_save(web_root, save_body, boundary_header, retry_num=0):
    url = f"{web_root}post.php?call=saveuserdetails&"
    request = urllib.request.Request(url, method="POST",
                                     data=save_body.encode("utf-8"))
    request.add_header("Accept-Encoding", "identity")
    request.add_header("Content-Type",
                       f'multipart/form-data; boundary="{boundary_header}"')
    request.add_header("User-Agent", "BestHTTP")
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            response = json.loads(reply.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - fire and forget
        return
    if response and response.get("switch_play_server") and retry_num < 3:
        server_call_save(response["switch_play_server"], save_body,
                         boundary_header, retry_num + 1)


def main():
    if len(sys.argv) < 4:
        return 2
    web_root, body_file, boundary = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        with open(body_file, "r", encoding="utf-8") as f:
            save_body = f.read()
    except OSError:
        return 1
    try:
        server_call_save(web_root, save_body, boundary)
    finally:
        try:
            os.unlink(body_file)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
