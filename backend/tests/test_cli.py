import pytest

from app import cli
from app.core.security import hash_password, verify_password
from app.database import SessionLocal
from app.models.user import User

USERNAME = "cli-test-user"


def _delete_user():
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == USERNAME).delete()
        db.commit()
    finally:
        db.close()


def _stored_hash():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == USERNAME).first()
        return user.password_hash if user else None
    finally:
        db.close()


def _add_user(password: str):
    db = SessionLocal()
    try:
        db.add(User(username=USERNAME, password_hash=hash_password(password)))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_user():
    _delete_user()
    yield
    _delete_user()


def test_create_admin_with_env_password(monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "long-enough-pass")
    assert cli.main(["create-admin", USERNAME]) == 0
    assert verify_password("long-enough-pass", _stored_hash())
    assert USERNAME in capsys.readouterr().out


def test_create_admin_prompts_twice(monkeypatch):
    monkeypatch.delenv(cli.PASSWORD_ENV_VAR, raising=False)
    answers = iter(["typed-password-1", "typed-password-1"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create-admin", USERNAME]) == 0
    assert verify_password("typed-password-1", _stored_hash())


def test_create_admin_rejects_mismatched_prompts(monkeypatch, capsys):
    monkeypatch.delenv(cli.PASSWORD_ENV_VAR, raising=False)
    answers = iter(["typed-password-1", "typed-password-2"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create-admin", USERNAME]) == 1
    assert _stored_hash() is None
    assert "no coinciden" in capsys.readouterr().err


def test_create_admin_rejects_short_password(monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "short")
    assert cli.main(["create-admin", USERNAME]) == 1
    assert _stored_hash() is None
    assert "10 caracteres" in capsys.readouterr().err


def test_create_admin_fails_if_user_exists(monkeypatch, capsys):
    _add_user("original-pass")
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "another-long-pass")

    assert cli.main(["create-admin", USERNAME]) == 1
    assert verify_password("original-pass", _stored_hash())
    assert "ya existe" in capsys.readouterr().err


def test_reset_password_changes_the_hash(monkeypatch):
    _add_user("original-pass")
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "brand-new-password")

    assert cli.main(["reset-password", USERNAME]) == 0
    assert verify_password("brand-new-password", _stored_hash())


def test_reset_password_fails_for_unknown_user(monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV_VAR, "brand-new-password")
    assert cli.main(["reset-password", USERNAME]) == 1
    assert "no existe" in capsys.readouterr().err
