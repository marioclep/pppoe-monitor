import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.settings import AppSetting
from app.models.user import User
from app.schemas.settings import SECRET_MASK

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "settings-tester").delete()
        db.commit()
        db.add(User(username="settings-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('settings-tester')}"}


def _snapshot(keys: list[str]) -> dict[str, str | None]:
    db: Session = SessionLocal()
    try:
        return {key: (row.value if (row := db.get(AppSetting, key)) else None) for key in keys}
    finally:
        db.close()


def _restore(values: dict[str, str | None]) -> None:
    db: Session = SessionLocal()
    try:
        for key, value in values.items():
            row = db.get(AppSetting, key)
            if value is None:
                if row is not None:
                    db.delete(row)
            elif row is None:
                db.add(AppSetting(key=key, value=value))
            else:
                row.value = value
        db.commit()
    finally:
        db.close()


def test_get_settings_returns_defaults():
    snapshot = _snapshot(["polling_interval_seconds", "reset_day_of_month"])
    _restore({"polling_interval_seconds": "300", "reset_day_of_month": "1"})
    try:
        response = client.get("/settings", headers=_auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["polling_interval_seconds"] == 300
        assert body["reset_day_of_month"] == 1
    finally:
        _restore(snapshot)


def test_put_settings_updates_polling_interval():
    headers = _auth_headers()
    snapshot = _snapshot(["polling_interval_seconds"])
    try:
        response = client.put("/settings", json={"polling_interval_seconds": 600}, headers=headers)
        assert response.status_code == 200
        assert response.json()["polling_interval_seconds"] == 600

        get_response = client.get("/settings", headers=headers)
        assert get_response.json()["polling_interval_seconds"] == 600
    finally:
        _restore(snapshot)


def test_put_settings_rejects_reset_day_out_of_range():
    headers = _auth_headers()
    response = client.put("/settings", json={"reset_day_of_month": 31}, headers=headers)
    assert response.status_code == 422


def test_put_settings_rejects_polling_interval_below_minimum():
    headers = _auth_headers()
    response = client.put("/settings", json={"polling_interval_seconds": 30}, headers=headers)
    assert response.status_code == 422


def test_put_settings_rejects_retention_days_below_minimum():
    headers = _auth_headers()
    response = client.put("/settings", json={"retention_days": 0}, headers=headers)
    assert response.status_code == 422


def test_put_settings_rejects_smtp_port_out_of_range():
    headers = _auth_headers()
    response = client.put("/settings", json={"smtp_port": 70000}, headers=headers)
    assert response.status_code == 422


def test_put_settings_does_not_overwrite_secret_with_mask_placeholder():
    headers = _auth_headers()
    snapshot = _snapshot(["smtp_password"])
    try:
        set_resp = client.put("/settings", json={"smtp_password": "supersecret"}, headers=headers)
        assert set_resp.status_code == 200
        assert set_resp.json()["smtp_password_set"] is True
        assert set_resp.json()["smtp_password"] == SECRET_MASK

        # Resubmitting the mask placeholder GET returns (e.g. an untouched
        # form field) must not clobber the real stored secret.
        resend_resp = client.put("/settings", json={"smtp_password": SECRET_MASK}, headers=headers)
        assert resend_resp.status_code == 200
        assert resend_resp.json()["smtp_password"] == SECRET_MASK

        db: Session = SessionLocal()
        try:
            row = db.get(AppSetting, "smtp_password")
            assert row.value == "supersecret"
        finally:
            db.close()
    finally:
        _restore(snapshot)


_OPTIONAL_KEYS = ["smtp_host", "smtp_port", "smtp_username", "smtp_from", "smtp_to", "telegram_chat_id"]
_ALL_TOUCHED = ["polling_interval_seconds", "reset_day_of_month", "retention_days", "raw_retention_days", *_OPTIONAL_KEYS]


def _ui_payload(**overrides) -> dict:
    """Exactly the shape SettingsAdmin.tsx sends: every non-secret field,
    with null for any empty optional input (secrets only when typed)."""
    payload = {
        "polling_interval_seconds": 300,
        "reset_day_of_month": 1,
        "retention_days": 90,
        "raw_retention_days": 7,
        "smtp_host": None,
        "smtp_port": None,
        "smtp_username": None,
        "smtp_from": None,
        "smtp_to": None,
        "telegram_chat_id": None,
    }
    payload.update(overrides)
    return payload


def test_put_settings_with_ui_null_payload_round_trips_and_clears_optional_fields():
    headers = _auth_headers()
    snapshot = _snapshot(_ALL_TOUCHED)
    try:
        # Start from populated optional values so we can see them cleared.
        seeded = client.put(
            "/settings",
            json=_ui_payload(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_username="u",
                smtp_from="a@example.com",
                smtp_to="b@example.com",
                telegram_chat_id="12345",
            ),
            headers=headers,
        )
        assert seeded.status_code == 200
        assert seeded.json()["smtp_port"] == 587

        put_resp = client.put("/settings", json=_ui_payload(), headers=headers)
        assert put_resp.status_code == 200

        get_resp = client.get("/settings", headers=headers)
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["polling_interval_seconds"] == 300
        for key in _OPTIONAL_KEYS:
            assert body[key] is None, key

        # Cleared means no stored row at all -- never the string "None".
        stored = _snapshot(_OPTIONAL_KEYS)
        assert stored == {key: None for key in _OPTIONAL_KEYS}
    finally:
        _restore(snapshot)


def test_put_settings_empty_string_also_clears_optional_field():
    headers = _auth_headers()
    snapshot = _snapshot(["smtp_host"])
    try:
        client.put("/settings", json={"smtp_host": "smtp.example.com"}, headers=headers)
        resp = client.put("/settings", json={"smtp_host": ""}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["smtp_host"] is None
        assert _snapshot(["smtp_host"]) == {"smtp_host": None}
    finally:
        _restore(snapshot)


@pytest.mark.parametrize("key", ["polling_interval_seconds", "reset_day_of_month", "retention_days", "raw_retention_days"])
def test_put_settings_rejects_null_for_numeric_settings(key):
    headers = _auth_headers()
    snapshot = _snapshot([key])
    try:
        resp = client.put("/settings", json=_ui_payload(**{key: None}), headers=headers)
        assert resp.status_code == 422
        assert _snapshot([key]) == snapshot  # nothing written
    finally:
        _restore(snapshot)


def test_get_settings_tolerates_garbage_stored_values():
    headers = _auth_headers()
    snapshot = _snapshot(_ALL_TOUCHED)
    try:
        _restore(
            {
                "polling_interval_seconds": "None",
                "reset_day_of_month": "99",
                "retention_days": "abc",
                "raw_retention_days": "x",
                "smtp_port": "None",
            }
        )
        resp = client.get("/settings", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["polling_interval_seconds"] == 300
        assert body["reset_day_of_month"] == 1
        assert body["retention_days"] == 90
        assert body["raw_retention_days"] == 7
        assert body["smtp_port"] is None
    finally:
        _restore(snapshot)


def test_get_settings_defaults_raw_retention_to_seven_days():
    snapshot = _snapshot(["raw_retention_days"])
    _restore({"raw_retention_days": None})
    try:
        response = client.get("/settings", headers=_auth_headers())
        assert response.status_code == 200
        assert response.json()["raw_retention_days"] == 7
    finally:
        _restore(snapshot)


def test_put_settings_updates_raw_retention_days():
    headers = _auth_headers()
    snapshot = _snapshot(["raw_retention_days", "retention_days"])
    _restore({"retention_days": "90"})
    try:
        response = client.put("/settings", json={"raw_retention_days": 3}, headers=headers)
        assert response.status_code == 200
        assert response.json()["raw_retention_days"] == 3
        assert _snapshot(["raw_retention_days"]) == {"raw_retention_days": "3"}
    finally:
        _restore(snapshot)


def test_put_settings_rejects_raw_retention_below_minimum():
    response = client.put("/settings", json={"raw_retention_days": 0}, headers=_auth_headers())
    assert response.status_code == 422


def test_put_settings_rejects_raw_retention_longer_than_retention():
    headers = _auth_headers()
    snapshot = _snapshot(["raw_retention_days", "retention_days"])
    _restore({"raw_retention_days": "7", "retention_days": "90"})
    try:
        too_long = client.put("/settings", json={"raw_retention_days": 91}, headers=headers)
        assert too_long.status_code == 422
        # Lowering retention below the stored raw retention is rejected too.
        too_short = client.put("/settings", json={"retention_days": 5}, headers=headers)
        assert too_short.status_code == 422
        assert _snapshot(["raw_retention_days", "retention_days"]) == {
            "raw_retention_days": "7",
            "retention_days": "90",
        }
        # Both at once, consistent: accepted.
        both = client.put("/settings", json={"raw_retention_days": 30, "retention_days": 30}, headers=headers)
        assert both.status_code == 200
    finally:
        _restore(snapshot)


# --- installation name (site_name) --------------------------------------------


def test_site_name_is_empty_by_default():
    snapshot = _snapshot(["site_name"])
    _restore({"site_name": None})
    try:
        body = client.get("/settings", headers=_auth_headers()).json()
        assert body["site_name"] is None
    finally:
        _restore(snapshot)


def test_put_site_name_saves_it_trimmed():
    headers = _auth_headers()
    snapshot = _snapshot(["site_name"])
    try:
        resp = client.put("/settings", json={"site_name": "  ACME  "}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["site_name"] == "ACME"
        assert _snapshot(["site_name"]) == {"site_name": "ACME"}
    finally:
        _restore(snapshot)


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_put_empty_site_name_clears_it(empty):
    headers = _auth_headers()
    snapshot = _snapshot(["site_name"])
    try:
        client.put("/settings", json={"site_name": "ACME"}, headers=headers)
        resp = client.put("/settings", json={"site_name": empty}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["site_name"] is None
        assert _snapshot(["site_name"]) == {"site_name": None}
    finally:
        _restore(snapshot)


def test_put_site_name_rejects_more_than_40_characters():
    headers = _auth_headers()
    snapshot = _snapshot(["site_name"])
    try:
        assert client.put("/settings", json={"site_name": "x" * 40}, headers=headers).status_code == 200
        assert client.put("/settings", json={"site_name": "x" * 41}, headers=headers).status_code == 422
    finally:
        _restore(snapshot)


# --- interface language -----------------------------------------------------


def test_language_defaults_to_spanish():
    snapshot = _snapshot(["language"])
    _restore({"language": None})
    try:
        assert client.get("/settings", headers=_auth_headers()).json()["language"] == "es"
    finally:
        _restore(snapshot)


def test_put_language_english_and_back():
    headers = _auth_headers()
    snapshot = _snapshot(["language"])
    try:
        resp = client.put("/settings", json={"language": "en"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["language"] == "en"
        assert client.put("/settings", json={"language": "es"}, headers=headers).json()["language"] == "es"
    finally:
        _restore(snapshot)


@pytest.mark.parametrize("bad", ["fr", "", None, "EN"])
def test_put_language_rejects_anything_but_es_or_en(bad):
    assert client.put("/settings", json={"language": bad}, headers=_auth_headers()).status_code == 422


def test_get_settings_falls_back_to_spanish_on_garbage_language():
    snapshot = _snapshot(["language"])
    _restore({"language": "klingon"})
    try:
        assert client.get("/settings", headers=_auth_headers()).json()["language"] == "es"
    finally:
        _restore(snapshot)
