import logging
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from app.models.settings import AppSetting

logger = logging.getLogger(__name__)


def _get_settings(db: Session, keys: list[str]) -> dict[str, str]:
    rows = db.query(AppSetting).filter(AppSetting.key.in_(keys)).all()
    return {row.key: row.value for row in rows}


def _smtp_send(host: str, port: int, username: str, password: str, from_addr: str, to_addr: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = to_addr
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


class TelegramSendError(RuntimeError):
    """Telegram delivery failure. Its message never contains the request URL,
    which embeds the bot token."""


def _telegram_send(bot_token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = httpx.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
    except httpx.HTTPError as exc:
        # httpx error messages can include the URL (and so the token): report
        # only the error type, and drop the chained exception.
        raise TelegramSendError(f"request to Telegram failed ({type(exc).__name__})") from None
    if response.is_error:
        try:
            description = str(response.json().get("description", ""))
        except ValueError:
            description = ""
        description = description.replace(bot_token, "<redacted>")
        raise TelegramSendError(f"Telegram API returned HTTP {response.status_code}: {description}")


def send_email(db: Session, subject: str, body: str) -> None:
    keys = ["smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from", "smtp_to"]
    values = _get_settings(db, keys)
    if not all(values.get(k) for k in keys):
        return
    try:
        _smtp_send(
            values["smtp_host"],
            int(values["smtp_port"]),
            values["smtp_username"],
            values["smtp_password"],
            values["smtp_from"],
            values["smtp_to"],
            subject,
            body,
        )
    except Exception:
        logger.exception("Failed to send email notification")


def send_telegram(db: Session, message: str) -> None:
    keys = ["telegram_bot_token", "telegram_chat_id"]
    values = _get_settings(db, keys)
    if not all(values.get(k) for k in keys):
        return
    try:
        _telegram_send(values["telegram_bot_token"], values["telegram_chat_id"], message)
    except TelegramSendError as exc:
        logger.error("Failed to send Telegram notification: %s", exc)
    except Exception as exc:
        # Never log the traceback/message here: it could carry the bot token.
        logger.error("Failed to send Telegram notification (%s)", type(exc).__name__)


NOTIFY_CHANNELS = ("email", "telegram")


def notify(db: Session, subject: str, message: str, channel: str) -> None:
    """Send an alert through the threshold's chosen channel only."""
    if channel == "email":
        send_email(db, subject, message)
    elif channel == "telegram":
        send_telegram(db, message)
    else:
        logger.warning("Unknown notification channel %r; alert not sent: %s", channel, subject)
