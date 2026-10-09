#!/usr/bin/env python3
"""Container healthcheck (T143): healthy when the bridge answers `account`.

Linux Python, standard library only. Any account mode is healthy. T151 dropped the demo-only
check on the user's decision (2026-10-09): the terminal trades whichever account is configured.
The mode is printed so `docker inspect` shows which one it is.
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
print(f"account #{answer['result']['login']} trade_mode={answer['result']['trade_mode']}")
