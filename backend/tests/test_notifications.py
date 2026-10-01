from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.services.notifications import notify


def _set(db: Session, key: str, value: str):
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value


def test_notify_calls_email_when_smtp_configured(monkeypatch):
    db = SessionLocal()
    try:
        for key, value in [
            ("smtp_host", "smtp.example.com"),
            ("smtp_port", "587"),
            ("smtp_username", "user"),
            ("smtp_password", "pass"),
            ("smtp_from", "alerts@example.com"),
            ("smtp_to", "admin@example.com"),
            ("telegram_bot_token", ""),
            ("telegram_chat_id", ""),
        ]:
            _set(db, key, value)
        db.commit()

        mock_send_email = MagicMock()
        monkeypatch.setattr("app.services.notifications._smtp_send", mock_send_email)

        notify(db, "Heavy user detected", "cliente1 superó el umbral", "email")

        mock_send_email.assert_called_once()
    finally:
        db.rollback()
        db.close()


def test_notify_skips_channels_with_incomplete_settings(monkeypatch):
    db = SessionLocal()
    try:
        for key in ["smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from", "smtp_to",
                    "telegram_bot_token", "telegram_chat_id"]:
            _set(db, key, "")
        db.commit()

        mock_send_email = MagicMock()
        mock_send_telegram = MagicMock()
        monkeypatch.setattr("app.services.notifications._smtp_send", mock_send_email)
        monkeypatch.setattr("app.services.notifications._telegram_send", mock_send_telegram)

        notify(db, "subject", "message", "email")
        notify(db, "subject", "message", "telegram")

        mock_send_email.assert_not_called()
        mock_send_telegram.assert_not_called()
    finally:
        db.rollback()
        db.close()


def _configure_both_channels(db: Session) -> None:
    for key, value in [
        ("smtp_host", "smtp.example.com"),
        ("smtp_port", "587"),
        ("smtp_username", "user"),
        ("smtp_password", "pass"),
        ("smtp_from", "alerts@example.com"),
        ("smtp_to", "admin@example.com"),
        ("telegram_bot_token", "123:abc"),
        ("telegram_chat_id", "42"),
    ]:
        _set(db, key, value)
    db.commit()


def test_notify_routes_to_chosen_channel_only(monkeypatch):
    db = SessionLocal()
    try:
        _configure_both_channels(db)
        mock_email = MagicMock()
        mock_telegram = MagicMock()
        monkeypatch.setattr("app.services.notifications._smtp_send", mock_email)
        monkeypatch.setattr("app.services.notifications._telegram_send", mock_telegram)

        notify(db, "s", "m", "telegram")
        mock_email.assert_not_called()
        mock_telegram.assert_called_once()

        mock_telegram.reset_mock()
        notify(db, "s", "m", "email")
        mock_email.assert_called_once()
        mock_telegram.assert_not_called()
    finally:
        db.rollback()
        for key in ["smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from", "smtp_to",
                    "telegram_bot_token", "telegram_chat_id"]:
            _set(db, key, "")
        db.commit()
        db.close()


_TOKEN = "123456:SECRET-BOT-TOKEN"


def _configure_telegram(db: Session) -> None:
    _set(db, "telegram_bot_token", _TOKEN)
    _set(db, "telegram_chat_id", "42")
    db.commit()


def _clear_telegram(db: Session) -> None:
    db.rollback()
    _set(db, "telegram_bot_token", "")
    _set(db, "telegram_chat_id", "")
    db.commit()


def test_telegram_http_error_is_logged_without_bot_token(monkeypatch, caplog):
    import httpx

    def fake_post(url, **kwargs):
        return httpx.Response(
            401,
            json={"ok": False, "error_code": 401, "description": "Unauthorized"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.notifications.httpx.post", fake_post)
    db = SessionLocal()
    try:
        _configure_telegram(db)
        with caplog.at_level("ERROR", logger="app.services.notifications"):
            notify(db, "s", "m", "telegram")
        assert "401" in caplog.text
        assert "Unauthorized" in caplog.text
        assert _TOKEN not in caplog.text
        assert "SECRET-BOT-TOKEN" not in caplog.text
    finally:
        _clear_telegram(db)
        db.close()


def test_telegram_transport_error_is_logged_without_bot_token(monkeypatch, caplog):
    import httpx

    def fake_post(url, **kwargs):
        raise httpx.ConnectError(f"cannot connect to {url}", request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.notifications.httpx.post", fake_post)
    db = SessionLocal()
    try:
        _configure_telegram(db)
        with caplog.at_level("ERROR", logger="app.services.notifications"):
            notify(db, "s", "m", "telegram")
        assert "ConnectError" in caplog.text
        assert "SECRET-BOT-TOKEN" not in caplog.text
    finally:
        _clear_telegram(db)
        db.close()
