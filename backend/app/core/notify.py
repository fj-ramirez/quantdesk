"""Reaching the user when they are not looking at the desk (T104).

The failure this exists for is twelve days of not noticing, so a red banner on a page nobody
opened is necessary and not sufficient. Telegram is the channel: one outbound HTTPS POST, no
inbound port, no third-party service to pay for, and it arrives on a phone.

**Detection and delivery are deliberately separated.** Every caller here logs its message
regardless of whether a channel is configured, and `send` reports whether anything left the
box. Two reasons, and the second is the durable one:

1. The desk must not require a Telegram account. Unconfigured is a supported, first-class
   state — the worker says at boot which mode it is in, so nobody discovers months later that
   the alerting they thought was on never was.
2. **A notifier that hard-depends on an external service has a failure indistinguishable from
   the silence it watches for.** If a bad token meant no alert *and* no trace, the watchdog
   would fail exactly the way the capture worker failed in September, for the same reason.

Nothing here raises. An alerter that dies trying to alert is worse than one that logs and
carries on, because the caller is usually a scheduled job whose next run is the only other
chance anyone gets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

__all__ = ["NotifyResult", "notifier_status", "send"]

logger = logging.getLogger("app.core.notify")

#: Telegram rejects messages over 4096 characters outright. Truncating beats a silent 400.
MAX_MESSAGE = 4000

#: Short: this runs inside a scheduled job, and a hung POST must not hold the scheduler.
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class NotifyResult:
    """What actually happened. `delivered` is False for both "not configured" and "failed",
    which are different facts -- `reason` distinguishes them, and callers that care should read
    it rather than inferring from the flag."""

    logged: bool
    delivered: bool
    reason: str


def notifier_status() -> str:
    """One line for a worker to log at boot, so the configured mode is never a guess."""
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        return "telegram notifications ENABLED (bot token and chat id are set)"
    if settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_CHAT_ID:
        return (
            "telegram notifications DISABLED: only one of TELEGRAM_BOT_TOKEN / "
            "TELEGRAM_CHAT_ID is set, and both are required. Alerts will be logged only."
        )
    return (
        "telegram notifications DISABLED (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset). "
        "Alerts will be logged only -- this is the default and is a supported configuration."
    )


def send(message: str, *, level: int = logging.WARNING) -> NotifyResult:
    """Log `message`, and deliver it to Telegram when one is configured. Never raises."""
    logger.log(level, "notify: %s", message)

    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return NotifyResult(logged=True, delivered=False, reason="no channel configured")

    body = message if len(message) <= MAX_MESSAGE else message[: MAX_MESSAGE - 1] + "…"
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": body, "disable_web_page_preview": True},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - see the module docstring: this must not raise
        # Deliberately not `logger.exception`: the token is in the URL, and a traceback from
        # httpx can carry the request in its representation.
        logger.error("notify: telegram delivery failed (%s). The message above was logged.",
                     type(exc).__name__)
        return NotifyResult(logged=True, delivered=False, reason=f"delivery failed: {type(exc).__name__}")

    return NotifyResult(logged=True, delivered=True, reason="delivered")
