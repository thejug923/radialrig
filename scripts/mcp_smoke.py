#!/usr/bin/env python3
"""Handshake with a RadialRig MCP server over stdio and call one tool.

Usage:  uv run scripts/mcp_smoke.py radialrig-read
        uv run scripts/mcp_smoke.py radialrig-write
Prints the tool names the server exposes and the `kind` of one call.

Evidence tooling for PLAN-0005 Phase 1: raw JSON-RPC over the stdio
transport, so the check does not depend on any client harness.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys


def send(proc, msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def read(proc):
    while True:
        line = proc.stdout.readline()
        if not line:
            raise SystemExit("server closed the stream")
        msg = json.loads(line)
        if "id" in msg:
            return msg


def main() -> int:
    command = sys.argv[1]
    exe = shutil.which(command) or command
    proc = subprocess.Popen(
        [exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
    )
    send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18",
                           "capabilities": {},
                           "clientInfo": {"name": "smoke", "version": "0"}}})
    read(proc)
    send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = sorted(t["name"] for t in read(proc)["result"]["tools"])
    print(command, "tools:", names)
    call = ({"name": "get_ruling", "arguments": {"query": "D-34"}}
            if "read" in command else
            {"name": "propose_record", "arguments": {"path": "", "text": ""}})
    send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": call})
    reply = read(proc)
    if "error" in reply:
        print(command, "call:", json.dumps(reply["error"])[:120])
    else:
        print(command, "call:", reply["result"]["content"][0]["text"][:120])
    proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
