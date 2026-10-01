"""In-memory limit on failed logins per client IP.

State lives in this process only: the backend runs a single uvicorn worker
(the scheduler lives inside it), and a restart clearing the counters is
acceptable for a LAN/VPN-only deployment.
"""
import ipaddress
import threading
import time
from collections import deque
from typing import Callable

from starlette.requests import Request


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: float = 900,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key in list(self._failures):
            failures = self._failures[key]
            while failures and failures[0] <= cutoff:
                failures.popleft()
            if not failures:
                del self._failures[key]

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            self._prune(self._clock())
            return len(self._failures.get(key, ())) >= self.max_failures

    def register_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._failures.setdefault(key, deque()).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()


def _is_internal(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def client_ip(request: Request) -> str:
    """The real client IP: nginx's X-Real-IP when the direct peer is inside
    the Docker/private network (i.e. it is our nginx), else the peer itself.
    The backend publishes no port, so only nginx can reach it in production."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded and _is_internal(peer):
        return forwarded
    return peer
