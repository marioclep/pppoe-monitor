from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.alert import AlertEvent, AlertThreshold
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.traffic import AccumulationPeriod
from app.services.alerts import evaluate_alerts

GB = 1024**3


def _setup(db: Session, download_bytes: int = 0, upload_bytes: int = 0) -> PPPoEClient:
    """A client on router "Torre Norte" with this month's usage. On a
    PPPoE-server interface TX is the client's download and RX its upload."""
    router = Router(name="Torre Norte", host="10.0.0.7", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    client = PPPoEClient(router_id=router.id, username="heavy_user")
    db.add(client)
    db.flush()
    db.add(AccumulationPeriod(client_id=client.id, tx_bytes_total=download_bytes, rx_bytes_total=upload_bytes))
    db.commit()
    return client


def _threshold(
    db: Session, direction: str, bytes_threshold: int, client_id: int | None = None, channel: str = "email"
) -> AlertThreshold:
    threshold = AlertThreshold(
        client_id=client_id, direction=direction, bytes_threshold=bytes_threshold, notify_channel=channel
    )
    db.add(threshold)
    db.commit()
    return threshold


def _cleanup(db: Session, client_id: int) -> None:
    db.rollback()
    router_id = db.query(PPPoEClient.router_id).filter(PPPoEClient.id == client_id).scalar()
    db.query(AlertEvent).filter_by(client_id=client_id).delete()
    db.query(AlertThreshold).filter_by(client_id=client_id).delete()
    db.query(AlertThreshold).filter(AlertThreshold.client_id.is_(None)).delete()
    db.query(AccumulationPeriod).filter_by(client_id=client_id).delete()
    db.query(PPPoEClient).filter_by(id=client_id).delete()
    if router_id is not None:
        db.query(Router).filter_by(id=router_id).delete()
    db.commit()


def _run(test):
    db = SessionLocal()
    db.query(AlertThreshold).filter(AlertThreshold.client_id.is_(None)).delete()
    db.commit()
    client = None
    try:
        client = test(db)
    finally:
        if client is not None:
            _cleanup(db, client.id)
        db.close()


def test_download_threshold_fires_on_download_only():
    def t(db):
        client = _setup(db, download_bytes=2000, upload_bytes=0)
        _threshold(db, "download", 1000, client.id)
        pending = evaluate_alerts(db, client.id)
        assert len(pending) == 1
        event = db.query(AlertEvent).filter_by(client_id=client.id).one()
        assert event.direction == "download"
        assert event.accumulated_bytes_at_trigger == 2000
        assert event.threshold_bytes == 1000
        return client

    _run(t)


def test_upload_threshold_fires_on_upload_only():
    def t(db):
        client = _setup(db, download_bytes=0, upload_bytes=2000)
        _threshold(db, "upload", 1000, client.id)
        assert len(evaluate_alerts(db, client.id)) == 1
        assert db.query(AlertEvent).filter_by(client_id=client.id).one().direction == "upload"
        return client

    _run(t)


def test_download_and_upload_are_not_added_together():
    def t(db):
        client = _setup(db, download_bytes=600, upload_bytes=600)
        _threshold(db, "download", 1000, client.id)
        _threshold(db, "upload", 1000, client.id)
        assert evaluate_alerts(db, client.id) == []
        assert db.query(AlertEvent).filter_by(client_id=client.id).count() == 0
        return client

    _run(t)


def test_both_directions_fire_independently():
    def t(db):
        client = _setup(db, download_bytes=2000, upload_bytes=3000)
        _threshold(db, "download", 1000, client.id)
        _threshold(db, "upload", 1000, client.id)
        assert len(evaluate_alerts(db, client.id)) == 2
        directions = {e.direction for e in db.query(AlertEvent).filter_by(client_id=client.id)}
        assert directions == {"download", "upload"}
        return client

    _run(t)


def test_each_direction_alerts_once_per_period():
    def t(db):
        client = _setup(db, download_bytes=2000, upload_bytes=0)
        _threshold(db, "download", 1000, client.id)
        assert len(evaluate_alerts(db, client.id)) == 1
        assert evaluate_alerts(db, client.id) == []
        # Upload crossing later in the same period still alerts.
        _threshold(db, "upload", 1000, client.id)
        period = db.query(AccumulationPeriod).filter_by(client_id=client.id).one()
        period.rx_bytes_total = 5000
        db.commit()
        assert len(evaluate_alerts(db, client.id)) == 1
        assert db.query(AlertEvent).filter_by(client_id=client.id).count() == 2
        return client

    _run(t)


def test_nothing_fires_under_threshold():
    def t(db):
        client = _setup(db, download_bytes=500, upload_bytes=500)
        _threshold(db, "download", 1000, client.id)
        assert evaluate_alerts(db, client.id) == []
        return client

    _run(t)


def test_global_threshold_applies_when_client_has_none():
    def t(db):
        client = _setup(db, download_bytes=0, upload_bytes=2000)
        global_upload = _threshold(db, "upload", 1000)
        assert len(evaluate_alerts(db, client.id)) == 1
        assert db.query(AlertEvent).filter_by(client_id=client.id).one().threshold_id == global_upload.id
        return client

    _run(t)


def test_client_threshold_overrides_global_in_its_direction_only():
    def t(db):
        client = _setup(db, download_bytes=2000, upload_bytes=2000)
        _threshold(db, "download", 1000)  # global: would fire...
        _threshold(db, "download", 5000, client.id)  # ...but the client's own is higher
        _threshold(db, "upload", 1000)  # global upload still applies
        pending = evaluate_alerts(db, client.id)
        assert len(pending) == 1
        assert db.query(AlertEvent).filter_by(client_id=client.id).one().direction == "upload"
        return client

    _run(t)


def test_notification_names_the_pppoe_user_router_and_amounts():
    def t(db):
        client = _setup(db, download_bytes=0, upload_bytes=int(523.4 * GB))
        _threshold(db, "upload", 300 * GB, client.id, channel="telegram")
        [(channel, subject, message)] = evaluate_alerts(db, client.id)
        assert channel == "telegram"
        assert subject == "Heavy user (subida): heavy_user"
        assert "heavy_user" in message
        assert "Torre Norte" in message
        assert "523.40 GB" in message
        assert "300.00 GB" in message
        assert f"cliente {client.id}" not in message.lower()
        return client

    _run(t)


def test_download_subject_says_descarga():
    def t(db):
        client = _setup(db, download_bytes=2 * GB)
        _threshold(db, "download", GB, client.id)
        [(_, subject, _)] = evaluate_alerts(db, client.id)
        assert subject == "Heavy user (descarga): heavy_user"
        return client

    _run(t)


def test_event_survives_deleting_its_threshold():
    def t(db):
        client = _setup(db, download_bytes=2000)
        threshold = _threshold(db, "download", 1000, client.id)
        evaluate_alerts(db, client.id)
        db.commit()
        db.delete(threshold)
        db.commit()
        event = db.query(AlertEvent).filter_by(client_id=client.id).one()
        assert event.threshold_id is None
        assert event.threshold_bytes == 1000
        return client

    _run(t)


def test_notification_in_english_when_the_installation_is_in_english():
    from app.models.settings import AppSetting

    def t(db):
        db.merge(AppSetting(key="language", value="en"))
        db.commit()
        try:
            client = _setup(db, download_bytes=2 * GB, upload_bytes=int(523.4 * GB))
            _threshold(db, "download", GB, client.id)
            _threshold(db, "upload", 300 * GB, client.id)
            pending = evaluate_alerts(db, client.id)
            subjects = sorted(subject for _, subject, _ in pending)
            assert subjects == ["Heavy user (download): heavy_user", "Heavy user (upload): heavy_user"]
            upload_message = next(m for _, s, m in pending if "upload" in s)
            assert "Upload this month: 523.40 GB" in upload_message
            assert "Threshold: 300.00 GB" in upload_message
            return client
        finally:
            db.rollback()
            db.query(AppSetting).filter_by(key="language").delete()
            db.commit()

    _run(t)
