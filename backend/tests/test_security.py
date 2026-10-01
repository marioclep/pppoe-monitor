import pytest
from jwt import PyJWTError

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed)
    assert not verify_password("wrong", hashed)


def test_create_and_decode_access_token():
    token = create_access_token("admin")
    assert decode_access_token(token) == "admin"


def test_decode_invalid_token_raises():
    with pytest.raises(PyJWTError):
        decode_access_token("not-a-real-token")
