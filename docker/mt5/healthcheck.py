#!/usr/bin/env python3
"""Container healthcheck (T143): healthy when the bridge answers `account` for a demo login.

Linux Python, standard library only. A `real` account fails the check on purpose: this
container is built for the demo account, and finding a live login here is a misconfiguration
worth a red container.
"""

import json
import os
import socket
import sys

port = int(os.environ.get("BRIDGE_PORT", "18812"))
try:
    # 25 s: longer than the bridge's 10 s initialize, so a "not connected" answer arrives
    # instead of a timeout.
    with socket.create_connection(("127.0.0.1", port), timeout=25) as s:
        s.sendall(b'{"id":1,"op":"account"}\n')
        answer = json.loads(s.makefile().readline())
except (OSError, ValueError) as e:
    print(f"bridge unreachable: {e}")
    sys.exit(1)
if not answer.get("ok"):
    print(answer.get("error"))
    sys.exit(1)
mode = answer["result"]["trade_mode"]
if mode != "demo":
    print(f"account trade_mode is {mode}, expected demo")
    sys.exit(1)
