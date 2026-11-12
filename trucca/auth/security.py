"""Băm mật khẩu (scrypt - thư viện chuẩn) và sinh token phiên."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# Tham số scrypt: N=2^15, r=8, p=1 (~32 MiB RAM, ~50-100ms) - khuyến nghị OWASP
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024
_DKLEN = 32


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM, dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


# Hash giả dùng khi username không tồn tại, để thời gian phản hồi tương đương (chống dò username)
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def validate_password_policy(password: str, min_length: int, username: str = "") -> str | None:
    """Trả về thông báo lỗi (tiếng Việt) hoặc None nếu hợp lệ."""
    if len(password) < min_length:
        return f"Mật khẩu phải có ít nhất {min_length} ký tự"
    if len(password) > 256:
        return "Mật khẩu quá dài (tối đa 256 ký tự)"
    if username and password.lower() == username.lower():
        return "Mật khẩu không được trùng tên đăng nhập"
    classes = sum(
        [any(c.islower() for c in password), any(c.isupper() for c in password),
         any(c.isdigit() for c in password), any(not c.isalnum() for c in password)]
    )
    if classes < 3:
        return "Mật khẩu cần có ít nhất 3 trong 4 loại: chữ thường, chữ hoa, chữ số, ký tự đặc biệt"
    return None
