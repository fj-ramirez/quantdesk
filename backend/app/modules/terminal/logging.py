"""Single logging setup, used by the CLI. Library code only calls getLogger."""

import logging
import sys

_CONFIGURED = False


def configure(level: str = "INFO") -> None:
    """Install one stderr handler. Idempotent: safe to call from every entry point."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger("xactx")
    root.setLevel(level.upper())
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"xactx.{name}")
