"""Send the app's log records (polling, rollup, purge, reset...) to stdout.

Without this only WARNING+ records reach the console (Python's last-resort
handler), so `docker compose logs backend` hides the INFO lines that show
what the scheduler is doing. uvicorn's own loggers don't propagate to the
root logger, so they are not duplicated.

httpx/httpcore stay at WARNING: httpx logs every request URL at INFO, and the
Telegram URL embeds the bot token.
"""
import logging
import sys

HANDLER_NAME = "pppoe-monitor-stdout"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
QUIET_LOGGERS = ("httpx", "httpcore")


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    if any(h.get_name() == HANDLER_NAME for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
