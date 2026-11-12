from trucca.auth.security import hash_password, token_hash, validate_password_policy, verify_password


def test_hash_roundtrip():
    h = hash_password("Abc12345!")
    assert h.startswith("scrypt$") and verify_password("Abc12345!", h)
    assert not verify_password("abc12345!", h)
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")  # salt ngẫu nhiên


def test_token_hash_is_stable():
    assert token_hash("abc") == token_hash("abc") and len(token_hash("abc")) == 64


def test_password_policy():
    assert validate_password_policy("short", 8)
    assert validate_password_policy("alllowercase", 8)
    assert validate_password_policy("Admin123", 8, username="admin123")
    assert validate_password_policy("Noc-Shift-2026", 8) is None
