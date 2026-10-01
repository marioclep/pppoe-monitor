from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.client import PPPoEClient, SessionState
from app.models.router import Router
from app.models.settings import AppSetting
from app.models.traffic import AccumulationPeriod, TrafficHourly, TrafficSample
from app.models.user import User
from app.services.rollup import HOURLY_ROLLUP_KEY, floor_hour

client = TestClient(app)

_created_client_ids: list[int] = []
_created_router_ids: list[int] = []


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "clients-tester").delete()
        db.commit()
        db.add(User(username="clients-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('clients-tester')}"}


def _seed_client(
    db: Session,
    username: str,
    rx_total: int,
    is_active: bool = True,
    tx_total: int = 0,
    rx_bps: int = 0,
    tx_bps: int = 0,
    router_name: str = "Clients Router",
) -> int:
    router = db.query(Router).filter_by(name=router_name).first()
    if router is None:
        router = Router(name=router_name, host="10.0.0.10", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
    _created_router_ids.append(router.id)
    c = PPPoEClient(router_id=router.id, username=username, is_active=is_active)
    db.add(c)
    db.flush()
    db.add(AccumulationPeriod(client_id=c.id, rx_bytes_total=rx_total, tx_bytes_total=tx_total))
    db.add(
        TrafficSample(
            client_id=c.id,
            sampled_at=datetime.now(timezone.utc),
            rx_bytes_delta=rx_total,
            tx_bytes_delta=0,
            # Deliberately different from the session_state bps: current bps
            # must come from session_state, never from samples.
            rx_bps=rx_bps + 1,
            tx_bps=tx_bps + 1,
        )
    )
    if is_active:
        # Polling keeps a session_state row only for online clients.
        db.add(
            SessionState(
                router_id=router.id, client_id=c.id, interface_id=f"*{username}", last_rx_bps=rx_bps, last_tx_bps=tx_bps
            )
        )
    db.commit()
    _created_client_ids.append(c.id)
    return c.id


def _cleanup():
    db = SessionLocal()
    try:
        if _created_client_ids:
            db.query(TrafficSample).filter(TrafficSample.client_id.in_(_created_client_ids)).delete(
                synchronize_session=False
            )
            db.query(SessionState).filter(SessionState.client_id.in_(_created_client_ids)).delete(
                synchronize_session=False
            )
            db.query(AccumulationPeriod).filter(AccumulationPeriod.client_id.in_(_created_client_ids)).delete(
                synchronize_session=False
            )
            db.query(PPPoEClient).filter(PPPoEClient.id.in_(_created_client_ids)).delete(
                synchronize_session=False
            )
            _created_client_ids.clear()
        if _created_router_ids:
            db.query(Router).filter(Router.id.in_(_created_router_ids)).delete(synchronize_session=False)
            _created_router_ids.clear()
        db.commit()
    finally:
        db.close()


def test_list_clients_sorted_by_upload_desc():
    db = SessionLocal()
    try:
        _seed_client(db, "low_user", 100)
        _seed_client(db, "heavy_user", 999999)
    finally:
        db.close()

    try:
        response = client.get("/clients?sort_by=upload&dir=desc&q=_user&page_size=200", headers=_auth_headers())
        assert response.status_code == 200
        usernames = [c["username"] for c in response.json()["items"]]
        assert usernames.index("heavy_user") < usernames.index("low_user")
    finally:
        _cleanup()


def test_list_clients_current_bps_from_session_state_and_zero_when_inactive():
    db = SessionLocal()
    try:
        active_id = _seed_client(db, "active_user", 100, is_active=True, rx_bps=1234, tx_bps=567)
        inactive_id = _seed_client(db, "inactive_user", 100, is_active=False, rx_bps=9999, tx_bps=8888)
    finally:
        db.close()

    try:
        response = client.get("/clients?q=active_user&page_size=200", headers=_auth_headers())
        assert response.status_code == 200
        by_id = {c["id"]: c for c in response.json()["items"]}
        assert by_id[active_id]["current_rx_bps"] == 1234
        assert by_id[active_id]["current_tx_bps"] == 567
        assert by_id[inactive_id]["current_rx_bps"] == 0
        assert by_id[inactive_id]["current_tx_bps"] == 0
    finally:
        _cleanup()


def test_client_history_returns_samples():
    db = SessionLocal()
    try:
        client_id = _seed_client(db, "history_user", 500)
    finally:
        db.close()

    try:
        response = client.get(f"/clients/{client_id}/history?hours=24", headers=_auth_headers())
        assert response.status_code == 200
        points = response.json()
        assert len(points) >= 1
        assert points[0]["rx_bytes_delta"] == 500
    finally:
        _cleanup()


def test_client_history_includes_each_sample_speed():
    db = SessionLocal()
    try:
        # The seeded sample stores bps = session bps + 1 (see _seed_client).
        client_id = _seed_client(db, "history_speed_user", 500, rx_bps=2_000_000, tx_bps=30_000_000)
    finally:
        db.close()

    try:
        response = client.get(f"/clients/{client_id}/history?hours=24", headers=_auth_headers())
        point = response.json()[0]
        assert (point["rx_bps"], point["tx_bps"]) == (2_000_001, 30_000_001)
    finally:
        _cleanup()


def test_client_history_returns_404_for_nonexistent_client():
    response = client.get("/clients/999999999/history?hours=24", headers=_auth_headers())
    assert response.status_code == 404


def test_list_clients_filters_by_router_id():
    db = SessionLocal()
    try:
        id_a = _seed_client(db, "router_a_user", 100, router_name="Router A")
        _seed_client(db, "router_b_user", 100, router_name="Router B")
        router_a_id = db.get(PPPoEClient, id_a).router_id
    finally:
        db.close()

    try:
        response = client.get(f"/clients?router_id={router_a_id}&q=router_&page_size=200", headers=_auth_headers())
        assert response.status_code == 200
        usernames = {c["username"] for c in response.json()["items"]}
        assert "router_a_user" in usernames
        assert "router_b_user" not in usernames
    finally:
        _cleanup()


def test_list_clients_active_only_excludes_inactive():
    db = SessionLocal()
    try:
        _seed_client(db, "active_only_active", 100, is_active=True)
        _seed_client(db, "active_only_inactive", 100, is_active=False)
    finally:
        db.close()

    try:
        response = client.get("/clients?active_only=true&q=active_only_&page_size=200", headers=_auth_headers())
        assert response.status_code == 200
        usernames = {c["username"] for c in response.json()["items"]}
        assert "active_only_active" in usernames
        assert "active_only_inactive" not in usernames
    finally:
        _cleanup()



def _seed_many(prefix: str, count: int) -> list[int]:
    db = SessionLocal()
    try:
        # download totals 1000, 2000, ... so download order == creation order
        return [_seed_client(db, f"{prefix}{i}", 10, tx_total=(i + 1) * 1000) for i in range(count)]
    finally:
        db.close()


def _page(params: str) -> dict:
    response = client.get(f"/clients?{params}", headers=_auth_headers())
    assert response.status_code == 200, response.text
    return response.json()


def test_list_clients_paginates_and_reports_total():
    _seed_many("pgtest_", 5)
    try:
        body = _page("q=pgtest_&sort_by=username&dir=asc&page=2&page_size=2")
        assert body["total"] == 5
        assert (body["page"], body["page_size"]) == (2, 2)
        assert [c["username"] for c in body["items"]] == ["pgtest_2", "pgtest_3"]

        last = _page("q=pgtest_&sort_by=username&dir=asc&page=3&page_size=2")
        assert [c["username"] for c in last["items"]] == ["pgtest_4"]
    finally:
        _cleanup()


def test_list_clients_page_past_the_end_is_empty():
    _seed_many("pgend_", 2)
    try:
        body = _page("q=pgend_&page=5&page_size=2")
        assert body["items"] == []
        assert body["total"] == 2
    finally:
        _cleanup()


def test_list_clients_search_is_case_insensitive_substring():
    _seed_many("SearchMe_", 2)
    try:
        body = _page("q=earchme")
        assert body["total"] == 2
    finally:
        _cleanup()


def test_list_clients_defaults_to_download_desc():
    _seed_many("dldefault_", 3)
    try:
        body = _page("q=dldefault_")
        assert [c["username"] for c in body["items"]] == ["dldefault_2", "dldefault_1", "dldefault_0"]
    finally:
        _cleanup()


def test_list_clients_sorts_by_current_speed():
    db = SessionLocal()
    try:
        _seed_client(db, "speedsort_slow", 10, rx_bps=1, tx_bps=10)
        _seed_client(db, "speedsort_fast", 10, rx_bps=1, tx_bps=900)
    finally:
        db.close()
    try:
        body = _page("q=speedsort_&sort_by=current&dir=desc")
        assert [c["username"] for c in body["items"]] == ["speedsort_fast", "speedsort_slow"]
    finally:
        _cleanup()


def test_list_clients_rejects_bad_paging_and_sort_params():
    headers = _auth_headers()
    assert client.get("/clients?page_size=500", headers=headers).status_code == 422
    assert client.get("/clients?page=0", headers=headers).status_code == 422
    assert client.get("/clients?sort_by=password", headers=headers).status_code == 422
    assert client.get("/clients?dir=sideways", headers=headers).status_code == 422


def test_get_single_client():
    (client_id,) = _seed_many("single_", 1)
    try:
        response = client.get(f"/clients/{client_id}", headers=_auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert (body["username"], body["accumulated_tx_bytes"]) == ("single_0", 1000)
    finally:
        _cleanup()


def test_get_single_client_404():
    assert client.get("/clients/999999999", headers=_auth_headers()).status_code == 404


def test_list_clients_search_treats_underscore_literally():
    db = SessionLocal()
    try:
        _seed_client(db, "litq_a", 10)
        _seed_client(db, "litqXa", 10)  # would match "litq_" if "_" were a wildcard
    finally:
        db.close()
    try:
        assert [c["username"] for c in _page("q=litq_")["items"]] == ["litq_a"]
    finally:
        _cleanup()


def test_current_speed_is_the_sum_of_all_sessions_and_client_listed_once():
    db = SessionLocal()
    try:
        double_id = _seed_client(db, "two_sessions_user", 100, rx_bps=1000, tx_bps=100)
        router_id = db.get(PPPoEClient, double_id).router_id
        db.add(
            SessionState(
                router_id=router_id,
                client_id=double_id,
                interface_id="*two_sessions_user-1",
                last_rx_bps=500,
                last_tx_bps=50,
            )
        )
        db.commit()
        _seed_client(db, "one_session_user", 100, rx_bps=1200, tx_bps=200)
    finally:
        db.close()

    try:
        response = client.get(
            "/clients?q=_session&sort_by=current&dir=desc&page_size=200", headers=_auth_headers()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        items = body["items"]
        assert [c["username"] for c in items] == ["two_sessions_user", "one_session_user"]
        assert (items[0]["current_rx_bps"], items[0]["current_tx_bps"]) == (1500, 150)

        single = client.get(f"/clients/{double_id}", headers=_auth_headers()).json()
        assert (single["current_rx_bps"], single["current_tx_bps"]) == (1500, 150)
    finally:
        _cleanup()


def _settings_snapshot(keys: list[str]) -> dict[str, str | None]:
    db = SessionLocal()
    try:
        return {key: (row.value if (row := db.get(AppSetting, key)) else None) for key in keys}
    finally:
        db.close()


def _write_settings(values: dict[str, str | None]) -> None:
    db = SessionLocal()
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


def _bare_client(db: Session, username: str) -> int:
    router = Router(name=f"History Router {username}", host="10.0.0.12", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    _created_router_ids.append(router.id)
    c = PPPoEClient(router_id=router.id, username=username)
    db.add(c)
    db.commit()
    _created_client_ids.append(c.id)
    return c.id


def test_client_history_short_range_returns_samples_without_peaks():
    snapshot = _settings_snapshot(["raw_retention_days"])
    _write_settings({"raw_retention_days": "7"})
    db = SessionLocal()
    try:
        client_id = _bare_client(db, "short_history_user")
        db.add(TrafficSample(client_id=client_id, sampled_at=datetime.now(timezone.utc), rx_bps=10, tx_bps=20))
        db.commit()
    finally:
        db.close()

    try:
        response = client.get(f"/clients/{client_id}/history?hours=168", headers=_auth_headers())
        assert response.status_code == 200
        (point,) = response.json()
        assert (point["rx_bps"], point["tx_bps"]) == (10, 20)
        assert point["peak_rx_bps"] is None and point["peak_tx_bps"] is None
    finally:
        _cleanup()
        _write_settings(snapshot)


def test_client_history_long_range_is_hourly_from_rollup_and_live_samples():
    snapshot = _settings_snapshot(["raw_retention_days", HOURLY_ROLLUP_KEY])
    current = floor_hour(datetime.now(timezone.utc))
    previous = current - timedelta(hours=1)
    _write_settings({"raw_retention_days": "7", HOURLY_ROLLUP_KEY: previous.isoformat()})
    db = SessionLocal()
    try:
        client_id = _bare_client(db, "long_history_user")
        # A rolled-up hour two days ago: 450 kB down in the hour = 1000 bps.
        db.add(
            TrafficHourly(
                client_id=client_id,
                hour_start=current - timedelta(hours=48),
                rx_bytes=450_000,
                tx_bytes=900_000,
                peak_rx_bps=5_000,
                peak_tx_bps=6_000,
            )
        )
        # A stale row at/after the watermark must be ignored: those hours
        # always come from the samples.
        db.add(
            TrafficHourly(
                client_id=client_id,
                hour_start=previous,
                rx_bytes=999_999_999,
                tx_bytes=999_999_999,
                peak_rx_bps=1,
                peak_tx_bps=1,
            )
        )
        db.add_all(
            [
                TrafficSample(
                    client_id=client_id,
                    sampled_at=previous + timedelta(minutes=10),
                    rx_bytes_delta=360_000,
                    tx_bytes_delta=450_000,
                    rx_bps=8_000,
                    tx_bps=7_000,
                ),
                TrafficSample(
                    client_id=client_id,
                    sampled_at=previous + timedelta(minutes=20),
                    rx_bytes_delta=90_000,
                    tx_bytes_delta=450_000,
                    rx_bps=3_000,
                    tx_bps=9_000,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    try:
        response = client.get(f"/clients/{client_id}/history?hours=720", headers=_auth_headers())
        assert response.status_code == 200
        points = response.json()
        assert [datetime.fromisoformat(p["sampled_at"]) for p in points] == [current - timedelta(hours=48), previous]
        stored, live = points
        assert (stored["rx_bytes_delta"], stored["rx_bps"], stored["tx_bps"]) == (450_000, 1000, 2000)
        assert (stored["peak_rx_bps"], stored["peak_tx_bps"]) == (5_000, 6_000)
        assert (live["rx_bytes_delta"], live["tx_bytes_delta"]) == (450_000, 900_000)
        assert (live["rx_bps"], live["tx_bps"]) == (1000, 2000)
        assert (live["peak_rx_bps"], live["peak_tx_bps"]) == (8_000, 9_000)
    finally:
        _cleanup()
        _write_settings(snapshot)


def test_client_history_validates_hours():
    db = SessionLocal()
    try:
        client_id = _bare_client(db, "hours_validation_user")
    finally:
        db.close()

    try:
        headers = _auth_headers()
        assert client.get(f"/clients/{client_id}/history?hours=0", headers=headers).status_code == 422
        assert client.get(f"/clients/{client_id}/history?hours=2161", headers=headers).status_code == 422
        assert client.get(f"/clients/{client_id}/history?hours=2160", headers=headers).status_code == 200
    finally:
        _cleanup()


def _set_addresses(client_id: int, session_addresses: list[str], last_address: str | None, last_mac: str | None):
    db = SessionLocal()
    try:
        c = db.get(PPPoEClient, client_id)
        c.last_address, c.last_mac = last_address, last_mac
        db.query(SessionState).filter_by(client_id=client_id).delete()
        for n, address in enumerate(session_addresses):
            db.add(SessionState(router_id=c.router_id, client_id=c.id, interface_id=f"*ip{client_id}-{n}", address=address))
        db.commit()
    finally:
        db.close()


def test_client_list_and_detail_show_current_ips_last_ip_and_mac():
    db = SessionLocal()
    try:
        two = _seed_client(db, "ipcols_two", 10)
        off = _seed_client(db, "ipcols_off", 5, is_active=False)
    finally:
        db.close()
    _set_addresses(two, ["10.5.0.2", "10.5.0.1"], "10.5.0.2", "AA:AA:AA:AA:AA:02")
    _set_addresses(off, [], "10.5.0.9", "AA:AA:AA:AA:AA:09")
    try:
        items = {c["username"]: c for c in _page("q=ipcols_&page_size=200")["items"]}
        assert items["ipcols_two"]["addresses"] == ["10.5.0.1", "10.5.0.2"]
        assert items["ipcols_off"]["addresses"] == []
        assert items["ipcols_off"]["last_address"] == "10.5.0.9"
        detail = client.get(f"/clients/{two}", headers=_auth_headers()).json()
        assert detail["last_mac"] == "AA:AA:AA:AA:AA:02"
    finally:
        _cleanup()


def test_client_search_matches_current_or_last_ip():
    db = SessionLocal()
    try:
        live = _seed_client(db, "ipsearch_live", 10)
        gone = _seed_client(db, "ipsearch_gone", 5, is_active=False)
        other = _seed_client(db, "ipsearch_other", 1)
    finally:
        db.close()
    _set_addresses(live, ["172.31.77.10"], None, None)
    _set_addresses(gone, [], "172.31.77.20", None)
    _set_addresses(other, ["172.30.1.1"], "172.30.1.1", None)
    try:
        body = _page("q=172.31.77.&page_size=200")
        assert sorted(c["username"] for c in body["items"]) == ["ipsearch_gone", "ipsearch_live"]
    finally:
        _cleanup()
