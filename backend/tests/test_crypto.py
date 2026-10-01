from app.core.crypto import decrypt, encrypt


def test_encrypt_then_decrypt_roundtrips():
    plain = "super-secret-router-password"
    token = encrypt(plain)
    assert token != plain
    assert decrypt(token) == plain
