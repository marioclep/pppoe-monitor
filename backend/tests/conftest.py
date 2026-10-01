import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The app refuses to start without a real JWT_SECRET (see app/config.py).
# Tests only need *some* valid secret to sign/verify their own tokens, so
# provide a test-only one unless the environment already sets it.
os.environ.setdefault("JWT_SECRET", "pytest-only-jwt-secret-not-for-production-use-0123456789")

from db_guard import destructive_tests_refusal  # noqa: E402


def pytest_sessionstart(session):
    from app.config import settings

    reason = destructive_tests_refusal(settings.DATABASE_URL, os.environ)
    if reason is not None:
        pytest.exit(reason, returncode=4)


@pytest.fixture(autouse=True)
def _clear_login_throttle():
    """The login throttle is process-wide state; don't let one test's failed
    logins block another test's."""
    from app.core.login_throttle import login_throttle

    login_throttle.clear()
    yield
    login_throttle.clear()
