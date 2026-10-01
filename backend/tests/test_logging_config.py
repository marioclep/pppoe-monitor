import logging

import pytest

from app.logging_config import HANDLER_NAME, configure_logging


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    yield root
    root.setLevel(saved_level)
    root.handlers[:] = saved_handlers


def _ours(root):
    return [h for h in root.handlers if h.get_name() == HANDLER_NAME]


def test_sets_level_and_adds_one_stdout_handler(restore_root_logger):
    root = restore_root_logger
    root.handlers[:] = [h for h in root.handlers if h.get_name() != HANDLER_NAME]

    configure_logging("WARNING")

    assert root.level == logging.WARNING
    assert len(_ours(root)) == 1
    assert _ours(root)[0].formatter._fmt == "%(asctime)s %(levelname)s %(name)s: %(message)s"


def test_is_idempotent_and_updates_level(restore_root_logger):
    root = restore_root_logger
    configure_logging("INFO")
    configure_logging("DEBUG")

    assert root.level == logging.DEBUG
    assert len(_ours(root)) == 1


def test_http_client_request_lines_are_not_logged_at_info(restore_root_logger):
    """httpx logs every request URL at INFO, and the Telegram URL carries the
    bot token (api.telegram.org/bot<TOKEN>/sendMessage)."""
    configure_logging("INFO")

    for name in ("httpx", "httpcore"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
        assert logging.getLogger(name).isEnabledFor(logging.WARNING)
