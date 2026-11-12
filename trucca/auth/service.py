"""Nghiệp vụ người dùng, phiên đăng nhập và nhật ký thao tác."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import Connection, Engine

from ..config import AuthConfig
from ..db import audit_log, sessions, users
from ..timeutil import utcnow
from .security import DUMMY_HASH, hash_password, new_token, token_hash, validate_password_policy, verify_password

log = logging.getLogger(__name__)

ROLES = ("viewer", "admin")
USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-]{3,64}$")
# Chỉ cập nhật last_seen_at tối đa 1 lần/phút để giảm ghi DB
TOUCH_INTERVAL = timedelta(minutes=1)


class AuthError(Exception):
    def __init__(self, message: str, code: str = "invalid", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


@dataclass
class CurrentUser:
    id: int
    username: str
    full_name: str
    role: str
    must_change_password: bool
    session_id: int
    csrf_token: str
    remember: bool = False
    renewed: bool = False  # phiên vừa được gia hạn trượt -> cần gia hạn cả cookie


    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


USER_PUBLIC_COLS = [
    users.c.id, users.c.username, users.c.full_name, users.c.role, users.c.is_active,
    users.c.must_change_password, users.c.locked_until, users.c.created_at, users.c.updated_at,
    users.c.last_login_at, users.c.failed_attempts,
]


def audit(conn: Connection, user: CurrentUser | None, action: str, target: str | None = None,
          detail: dict[str, Any] | None = None, ip: str | None = None, username: str | None = None) -> None:
    conn.execute(
        audit_log.insert().values(
            user_id=user.id if user else None,
            username=user.username if user else username,
            action=action, target=target, detail=detail, ip=ip, created_at=utcnow(),
        )
    )


# --------------------------------------------------------------------------- bootstrap


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"  # noqa: S105 - mặc định theo yêu cầu, bắt buộc đổi ở lần đăng nhập đầu


def ensure_bootstrap_admin(engine: Engine, username: str | None, password: str | None) -> None:
    """Tạo admin đầu tiên khi DB chưa có user nào.

    Lấy từ TRUCCA_ADMIN_USERNAME / TRUCCA_ADMIN_PASSWORD; không đặt thì dùng mặc định admin / admin.
    Tài khoản luôn bị bắt đổi mật khẩu ở lần đăng nhập đầu (mật khẩu mới phải theo chính sách mật khẩu).
    """
    username = (username or "").strip() or DEFAULT_ADMIN_USERNAME
    password = password or DEFAULT_ADMIN_PASSWORD
    with engine.begin() as conn:
        count = conn.execute(select(func.count()).select_from(users)).scalar_one()
        if count:
            return
        if not USERNAME_RE.match(username):
            raise ValueError("TRUCCA_ADMIN_USERNAME không hợp lệ (3-64 ký tự: chữ, số, . _ -)")
        if password == DEFAULT_ADMIN_PASSWORD:
            log.warning("bootstrap_admin_default_password",
                        extra={"hint": "Đăng nhập và đổi mật khẩu ngay (hệ thống bắt buộc đổi ở lần đầu)"})
        now = utcnow()
        conn.execute(
            users.insert().values(
                username=username, full_name="Quản trị viên", password_hash=hash_password(password), role="admin",
                is_active=True, must_change_password=True, created_at=now, updated_at=now,
            )
        )
        audit(conn, None, "user.bootstrap", target=username, username="system")
        log.info("bootstrap_admin_created", extra={"username": username})


# --------------------------------------------------------------------------- login / session


def session_ttl(cfg: AuthConfig, remember: bool) -> timedelta:
    """Thời gian phiên còn hiệu lực kể từ lần hoạt động cuối."""
    return timedelta(days=cfg.remember_days) if remember else timedelta(hours=cfg.session_ttl_hours)


def login(engine: Engine, cfg: AuthConfig, username: str, password: str, ip: str | None,
          user_agent: str | None, remember: bool = False) -> tuple[str, CurrentUser]:
    generic = AuthError("Tên đăng nhập hoặc mật khẩu không đúng", "bad_credentials", 401)
    now = utcnow()
    username = (username or "").strip()
    failure: AuthError | None = None
    # Lưu ý: các nhánh thất bại KHÔNG raise trong transaction, để bộ đếm sai mật khẩu
    # và nhật ký được commit; raise sau khi thoát khỏi khối with.
    with engine.begin() as conn:
        row = conn.execute(
            select(users).where(func.lower(users.c.username) == username.lower()).with_for_update()
        ).first()
        if row is None:
            verify_password(password, DUMMY_HASH)
            audit(conn, None, "auth.login_failed", target=username[:64], ip=ip, username=username[:64],
                  detail={"reason": "unknown_user"})
            failure = generic
        elif row.locked_until and row.locked_until > now:
            mins = max(1, int((row.locked_until - now).total_seconds() // 60) + 1)
            failure = AuthError(f"Tài khoản tạm khoá do đăng nhập sai nhiều lần. Thử lại sau {mins} phút.",
                                "locked", 423)
        elif not verify_password(password, row.password_hash):
            attempts = row.failed_attempts + 1
            values: dict[str, Any] = {"failed_attempts": attempts}
            if attempts >= cfg.max_failed_attempts:
                values = {"failed_attempts": 0, "locked_until": now + timedelta(minutes=cfg.lockout_minutes)}
            conn.execute(update(users).where(users.c.id == row.id).values(**values))
            audit(conn, None, "auth.login_failed", target=row.username, ip=ip, username=row.username,
                  detail={"reason": "bad_password", "locked": "locked_until" in values})
            failure = generic
        elif not row.is_active:
            audit(conn, None, "auth.login_failed", target=row.username, ip=ip, username=row.username,
                  detail={"reason": "disabled"})
            failure = AuthError("Tài khoản đã bị vô hiệu hoá. Liên hệ quản trị viên.", "disabled", 403)
        else:
            token = new_token()
            csrf = new_token()
            sid = conn.execute(
                sessions.insert().values(
                    token_hash=token_hash(token), user_id=row.id, csrf_token=csrf, created_at=now,
                    expires_at=now + session_ttl(cfg, remember), last_seen_at=now, remember=remember,
                    ip=ip, user_agent=(user_agent or "")[:300],
                ).returning(sessions.c.id)
            ).scalar_one()
            conn.execute(update(users).where(users.c.id == row.id)
                         .values(failed_attempts=0, locked_until=None, last_login_at=now))
            user = CurrentUser(row.id, row.username, row.full_name, row.role, row.must_change_password, sid, csrf,
                               remember=remember)
            audit(conn, user, "auth.login", ip=ip, detail={"remember": remember})
    if failure is not None:
        raise failure
    return token, user


def session_user(engine: Engine, token: str | None, cfg: AuthConfig) -> CurrentUser | None:
    if not token or len(token) > 200:
        return None
    now = utcnow()
    stmt = (
        select(sessions.c.id, sessions.c.csrf_token, sessions.c.last_seen_at, sessions.c.remember,
               users.c.id.label("uid"),
               users.c.username, users.c.full_name, users.c.role, users.c.must_change_password)
        .join(users, users.c.id == sessions.c.user_id)
        .where(sessions.c.token_hash == token_hash(token), sessions.c.expires_at > now, users.c.is_active.is_(True))
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    if row is None:
        return None
    renewed = now - row.last_seen_at > TOUCH_INTERVAL
    if renewed:
        # Gia hạn trượt: phiên hết hạn sau ttl kể từ lần hoạt động cuối
        with engine.begin() as conn:
            conn.execute(update(sessions).where(sessions.c.id == row.id)
                         .values(last_seen_at=now, expires_at=now + session_ttl(cfg, row.remember)))
    return CurrentUser(row.uid, row.username, row.full_name, row.role, row.must_change_password, row.id,
                       row.csrf_token, remember=row.remember, renewed=renewed)


def logout(engine: Engine, user: CurrentUser, ip: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.id == user.session_id))
        audit(conn, user, "auth.logout", ip=ip)


def purge_expired_sessions(engine: Engine) -> int:
    with engine.begin() as conn:
        return conn.execute(delete(sessions).where(sessions.c.expires_at <= utcnow())).rowcount


# --------------------------------------------------------------------------- password


def change_own_password(engine: Engine, cfg: AuthConfig, user: CurrentUser, current: str, new: str,
                        ip: str | None) -> None:
    err = validate_password_policy(new, cfg.min_password_length, user.username)
    if err:
        raise AuthError(err, "weak_password")
    if current == new:
        raise AuthError("Mật khẩu mới phải khác mật khẩu hiện tại", "same_password")
    with engine.begin() as conn:
        row = conn.execute(select(users.c.password_hash).where(users.c.id == user.id)).first()
        if row is None or not verify_password(current, row.password_hash):
            raise AuthError("Mật khẩu hiện tại không đúng", "bad_current_password")
        conn.execute(update(users).where(users.c.id == user.id).values(
            password_hash=hash_password(new), must_change_password=False, updated_at=utcnow()))
        # Đăng xuất các phiên khác
        conn.execute(delete(sessions).where(sessions.c.user_id == user.id, sessions.c.id != user.session_id))
        audit(conn, user, "user.password_changed", target=user.username, ip=ip)


# --------------------------------------------------------------------------- user admin


ADMIN_LOCK_KEY = 724110002


def _lock_user_admin(conn: Connection) -> None:
    """Tuần tự hoá các thao tác có thể làm mất admin cuối cùng (chống race 2 admin hạ quyền nhau)."""
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADMIN_LOCK_KEY})


def _active_admin_count(conn: Connection, exclude_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(users).where(users.c.role == "admin", users.c.is_active.is_(True))
    if exclude_id is not None:
        stmt = stmt.where(users.c.id != exclude_id)
    return conn.execute(stmt).scalar_one()


def list_users(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(*USER_PUBLIC_COLS).order_by(users.c.username))]


def create_user(engine: Engine, cfg: AuthConfig, actor: CurrentUser, username: str, full_name: str, role: str,
                password: str, ip: str | None) -> dict[str, Any]:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError("Tên đăng nhập 3-64 ký tự, chỉ gồm chữ, số và . _ -", "bad_username")
    if role not in ROLES:
        raise AuthError("Vai trò không hợp lệ", "bad_role")
    err = validate_password_policy(password, cfg.min_password_length, username)
    if err:
        raise AuthError(err, "weak_password")
    now = utcnow()
    with engine.begin() as conn:
        exists = conn.execute(select(users.c.id).where(func.lower(users.c.username) == username.lower())).first()
        if exists:
            raise AuthError("Tên đăng nhập đã tồn tại", "duplicate", 409)
        uid = conn.execute(
            users.insert().values(
                username=username, full_name=(full_name or "").strip()[:200], password_hash=hash_password(password),
                role=role, is_active=True, must_change_password=True, created_at=now, updated_at=now,
            ).returning(users.c.id)
        ).scalar_one()
        audit(conn, actor, "user.create", target=username, detail={"role": role}, ip=ip)
        return dict(conn.execute(select(*USER_PUBLIC_COLS).where(users.c.id == uid)).one()._mapping)


def update_user(engine: Engine, actor: CurrentUser, user_id: int, *, full_name: str | None, role: str | None,
                is_active: bool | None, unlock: bool, ip: str | None) -> dict[str, Any]:
    with engine.begin() as conn:
        _lock_user_admin(conn)
        row = conn.execute(select(users).where(users.c.id == user_id).with_for_update()).first()
        if row is None:
            raise AuthError("Không tìm thấy người dùng", "not_found", 404)
        values: dict[str, Any] = {}
        if full_name is not None:
            values["full_name"] = full_name.strip()[:200]
        if role is not None:
            if role not in ROLES:
                raise AuthError("Vai trò không hợp lệ", "bad_role")
            values["role"] = role
        if is_active is not None:
            values["is_active"] = is_active
        if unlock:
            values.update(locked_until=None, failed_attempts=0)
        removing_admin = row.role == "admin" and row.is_active and (
            values.get("role", "admin") != "admin" or values.get("is_active", True) is False
        )
        if removing_admin and _active_admin_count(conn, exclude_id=user_id) == 0:
            raise AuthError("Phải còn ít nhất một quản trị viên đang hoạt động", "last_admin", 409)
        if values:
            values["updated_at"] = utcnow()
            conn.execute(update(users).where(users.c.id == user_id).values(**values))
            if values.get("is_active") is False or ("role" in values and values["role"] != row.role):
                # Đổi quyền / khoá => buộc đăng nhập lại
                conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
            audit(conn, actor, "user.update", target=row.username,
                  detail={k: v for k, v in values.items() if k != "updated_at" and k != "locked_until"}, ip=ip)
        return dict(conn.execute(select(*USER_PUBLIC_COLS).where(users.c.id == user_id)).one()._mapping)


def reset_password(engine: Engine, cfg: AuthConfig, actor: CurrentUser, user_id: int, new_password: str,
                   ip: str | None) -> None:
    with engine.begin() as conn:
        row = conn.execute(select(users.c.username).where(users.c.id == user_id)).first()
        if row is None:
            raise AuthError("Không tìm thấy người dùng", "not_found", 404)
        err = validate_password_policy(new_password, cfg.min_password_length, row.username)
        if err:
            raise AuthError(err, "weak_password")
        conn.execute(update(users).where(users.c.id == user_id).values(
            password_hash=hash_password(new_password), must_change_password=True, failed_attempts=0,
            locked_until=None, updated_at=utcnow()))
        conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
        audit(conn, actor, "user.reset_password", target=row.username, ip=ip)


def delete_user(engine: Engine, actor: CurrentUser, user_id: int, ip: str | None) -> None:
    if user_id == actor.id:
        raise AuthError("Không thể tự xoá tài khoản của mình", "self_delete", 409)
    with engine.begin() as conn:
        _lock_user_admin(conn)
        row = conn.execute(select(users).where(users.c.id == user_id).with_for_update()).first()
        if row is None:
            raise AuthError("Không tìm thấy người dùng", "not_found", 404)
        if row.role == "admin" and row.is_active and _active_admin_count(conn, exclude_id=user_id) == 0:
            raise AuthError("Phải còn ít nhất một quản trị viên đang hoạt động", "last_admin", 409)
        conn.execute(delete(users).where(users.c.id == user_id))
        audit(conn, actor, "user.delete", target=row.username, ip=ip)


def list_audit(engine: Engine, limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(audit_log).order_by(audit_log.c.created_at.desc(), audit_log.c.id.desc()).limit(limit)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]


def record(engine: Engine, user: CurrentUser | None, action: str, target: str | None = None,
           detail: dict[str, Any] | None = None, ip: str | None = None) -> None:
    with engine.begin() as conn:
        audit(conn, user, action, target, detail, ip)


def utc(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
