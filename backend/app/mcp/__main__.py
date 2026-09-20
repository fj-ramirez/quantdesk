"""Entry point for the quantdesk MCP server (T82).

    uv run python -m app.mcp

**Logging is configured to stderr before anything else is imported, and that ordering is
load-bearing.** stdio *is* the MCP transport: one line on stdout that is not a protocol message
corrupts the stream, and the client reports it as a malformed-response error that looks like the
client's own bug. This codebase calls `logging.basicConfig` in several entry points, and
`basicConfig` defaults to stderr — but only if nothing has already installed a stdout handler,
so this file claims the root logger first and never gives it up.

The startup sequence is deliberately: configure logging, prove the role cannot write, then serve.
`assert_read_only` raising here is the intended behaviour for a misconfigured connector — a
server that starts and is not read-only is worse than one that refuses to start, because the
former is indistinguishable from a working one.
"""

from __future__ import annotations

import logging
import sys

# Before `app.*` is imported: see the module docstring. stdout belongs to the protocol.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from app.mcp.db import ReadOnlyViolation, assert_read_only
from app.mcp.server import build_server

log = logging.getLogger("app.mcp")


def main() -> int:
    try:
        role = assert_read_only()
    except ReadOnlyViolation as exc:
        log.error("refusing to start: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - a connect failure is also a refusal to start
        log.error("refusing to start: could not reach the database (%s)", exc)
        return 2

    log.info("quantdesk MCP server starting as %s (read-only, stdio)", role)
    build_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
