from app.services.delta import compute_delta


def test_first_seen_returns_current_bytes_as_delta():
    assert compute_delta(current_uptime=120, current_bytes=5000, last_uptime=None, last_bytes=None) == 5000


def test_same_session_continuing_returns_difference():
    assert compute_delta(current_uptime=600, current_bytes=9000, last_uptime=300, last_bytes=5000) == 4000


def test_session_restarted_returns_current_bytes_as_delta():
    # uptime dropped => interface/session was recreated, counters reset to 0
    assert compute_delta(current_uptime=30, current_bytes=1200, last_uptime=900, last_bytes=50000) == 1200


def test_same_uptime_no_traffic_returns_zero():
    assert compute_delta(current_uptime=300, current_bytes=5000, last_uptime=300, last_bytes=5000) == 0


def test_never_returns_negative_delta():
    # Defensive: if bytes somehow decreased without an uptime drop, clamp to 0
    assert compute_delta(current_uptime=610, current_bytes=4000, last_uptime=600, last_bytes=5000) == 0
