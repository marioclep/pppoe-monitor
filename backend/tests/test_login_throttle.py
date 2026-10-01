from starlette.requests import Request

from app.core.login_throttle import LoginThrottle, client_ip


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _throttle(clock):
    return LoginThrottle(max_failures=5, window_seconds=900, clock=clock)


def test_blocks_after_max_failures():
    t = _throttle(FakeClock())
    for _ in range(4):
        t.register_failure("10.0.0.1")
    assert not t.is_blocked("10.0.0.1")
    t.register_failure("10.0.0.1")
    assert t.is_blocked("10.0.0.1")


def test_unblocks_when_failures_leave_the_window():
    clock = FakeClock()
    t = _throttle(clock)
    for _ in range(5):
        t.register_failure("10.0.0.1")
    clock.now += 900
    assert not t.is_blocked("10.0.0.1")


def test_window_is_sliding():
    clock = FakeClock()
    t = _throttle(clock)
    t.register_failure("10.0.0.1")
    clock.now += 600
    for _ in range(4):
        t.register_failure("10.0.0.1")
    assert t.is_blocked("10.0.0.1")
    clock.now += 300  # the first failure leaves the window
    assert not t.is_blocked("10.0.0.1")


def test_reset_clears_one_key_only():
    t = _throttle(FakeClock())
    for _ in range(5):
        t.register_failure("10.0.0.1")
        t.register_failure("10.0.0.2")
    t.reset("10.0.0.1")
    assert not t.is_blocked("10.0.0.1")
    assert t.is_blocked("10.0.0.2")


def test_keys_are_independent():
    t = _throttle(FakeClock())
    for _ in range(5):
        t.register_failure("10.0.0.1")
    assert not t.is_blocked("10.0.0.2")


def test_expired_keys_are_dropped():
    clock = FakeClock()
    t = _throttle(clock)
    t.register_failure("10.0.0.1")
    clock.now += 901
    t.register_failure("10.0.0.2")
    assert list(t._failures) == ["10.0.0.2"]


def _request(peer, headers=None):
    scope = {
        "type": "http",
        "client": peer,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


def test_client_ip_trusts_x_real_ip_from_private_peer():
    req = _request(("172.18.0.4", 51000), {"X-Real-IP": "192.168.10.25"})
    assert client_ip(req) == "192.168.10.25"


def test_client_ip_ignores_x_real_ip_from_public_peer():
    req = _request(("8.8.8.8", 51000), {"X-Real-IP": "192.168.10.25"})
    assert client_ip(req) == "8.8.8.8"


def test_client_ip_without_header_uses_peer():
    assert client_ip(_request(("172.18.0.4", 51000))) == "172.18.0.4"


def test_client_ip_handles_non_ip_or_missing_peer():
    assert client_ip(_request(("testclient", 50000), {"X-Real-IP": "1.2.3.4"})) == "testclient"
    assert client_ip(_request(None)) == "unknown"
