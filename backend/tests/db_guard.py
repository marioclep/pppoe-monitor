"""Safety guard: the backend test suite writes to (and deletes from) the
database named by DATABASE_URL directly -- it does not use an isolated test
database. Refuse to run unless that database is clearly a local dev/test one.
"""
from collections.abc import Mapping

from sqlalchemy.engine import make_url

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_DATABASE_NAMES = {"pppoe", "pppoe_dev", "pppoe_test"}
OVERRIDE_ENV_VAR = "ALLOW_DESTRUCTIVE_TESTS"


def destructive_tests_refusal(database_url: str, environ: Mapping[str, str]) -> str | None:
    """Return None when the tests may run against `database_url`, otherwise a
    human-readable reason for refusing."""
    if environ.get(OVERRIDE_ENV_VAR) == "1":
        return None
    url = make_url(database_url)
    if url.host in LOCAL_HOSTS and url.database in ALLOWED_DATABASE_NAMES:
        return None
    return (
        f"Refusing to run the backend tests against database {url.database!r} on host "
        f"{url.host!r}: the tests write and delete data directly in that database. "
        f"They only run against a local dev/test database (host in {sorted(LOCAL_HOSTS)}, "
        f"name in {sorted(ALLOWED_DATABASE_NAMES)}). If you are SURE this is a disposable "
        f"database, set {OVERRIDE_ENV_VAR}=1."
    )
