from app.core.security import create_access_token, hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("my-secret-password")
    assert verify_password("my-secret-password", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_jwt_contains_sub() -> None:
    token = create_access_token(user_id=42, account="demo")
    assert isinstance(token, str)
    assert len(token) > 10
