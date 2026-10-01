from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.alert import DIRECTION_DOWNLOAD, DIRECTIONS, AlertEvent, AlertThreshold
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.traffic import AccumulationPeriod
from app.services.app_settings import get_language

# (notify_channel, subject, message)
PendingNotification = tuple[str, str, str]

# Alert texts in the installation's language (Configuración).
TEXTS = {
    "es": {
        "download": "descarga",
        "upload": "subida",
        "used": "{label} del mes: {value}",
        "threshold": "Umbral: {value}",
    },
    "en": {
        "download": "download",
        "upload": "upload",
        "used": "{label} this month: {value}",
        "threshold": "Threshold: {value}",
    },
}


def format_gb(value: int) -> str:
    """Same units as the web UI (GB of 1024**3)."""
    return f"{value / 1024**3:.2f} GB"


def _usage(period: AccumulationPeriod, direction: str) -> int:
    # On a PPPoE-server interface TX is the client's download, RX its upload.
    return period.tx_bytes_total if direction == DIRECTION_DOWNLOAD else period.rx_bytes_total


def _applicable_threshold(db: Session, client_id: int, direction: str) -> AlertThreshold | None:
    """The client's own threshold for this direction, else the global one."""
    return (
        db.query(AlertThreshold).filter_by(client_id=client_id, direction=direction).first()
        or db.query(AlertThreshold).filter_by(client_id=None, direction=direction).first()
    )


def evaluate_alerts(db: Session, client_id: int) -> list[PendingNotification]:
    """Check the client's current open accumulation period against its
    download and upload thresholds, separately, recording an AlertEvent the
    first time each one is crossed in the period.

    Does NOT send notifications itself — sending has side effects that must
    not run inside a transaction that might still be rolled back. Instead,
    returns (channel, subject, message) tuples -- channel being the
    threshold's notify_channel -- for the caller to notify() *after* the
    enclosing transaction has committed. Empty when nothing fired.
    """
    period = db.query(AccumulationPeriod).filter_by(client_id=client_id, period_end=None).first()
    if period is None:
        return []

    pending: list[PendingNotification] = []
    for direction in DIRECTIONS:
        threshold = _applicable_threshold(db, client_id, direction)
        used = _usage(period, direction)
        if threshold is None or used < threshold.bytes_threshold:
            continue
        already_notified = (
            db.query(AlertEvent)
            .filter(
                AlertEvent.client_id == client_id,
                AlertEvent.direction == direction,
                AlertEvent.triggered_at >= period.period_start,
            )
            .first()
        )
        if already_notified is not None:
            continue

        db.add(
            AlertEvent(
                client_id=client_id,
                threshold_id=threshold.id,
                direction=direction,
                threshold_bytes=threshold.bytes_threshold,
                triggered_at=datetime.now(timezone.utc),
                accumulated_bytes_at_trigger=used,
            )
        )
        db.flush()
        pending.append((threshold.notify_channel, *_message(db, client_id, direction, used, threshold)))
    return pending


def _message(db: Session, client_id: int, direction: str, used: int, threshold: AlertThreshold) -> tuple[str, str]:
    username, router_name = (
        db.query(PPPoEClient.username, Router.name)
        .join(Router, Router.id == PPPoEClient.router_id)
        .filter(PPPoEClient.id == client_id)
        .one()
    )
    texts = TEXTS[get_language(db)]
    label = texts[direction]
    subject = f"Heavy user ({label}): {username}"
    message = "\n".join(
        [
            subject,
            f"Router: {router_name}",
            texts["used"].format(label=label.capitalize(), value=format_gb(used)),
            texts["threshold"].format(value=format_gb(threshold.bytes_threshold)),
        ]
    )
    return subject, message
