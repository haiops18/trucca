"""Schema PostgreSQL (SQLAlchemy Core) và quản lý engine.

Mọi cột thời gian là TIMESTAMPTZ, lưu UTC. Schema thực tế do Alembic tạo
(migrations/versions); định nghĩa ở đây phải khớp với migration mới nhất.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

metadata = MetaData()

TS = DateTime(timezone=True)

# Trạng thái mới nhất của từng fingerprint (phục vụ bảng "đang diễn ra")
alerts = Table(
    "alerts",
    metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("alertname", Text, nullable=False),
    Column("summary", Text, nullable=False, server_default=""),
    Column("severity", Text),
    Column("owner", Text),
    Column("application", Text),
    Column("instance", Text),
    Column("labels", JSONB, nullable=False),
    Column("annotations", JSONB, nullable=False),
    Column("starts_at", TS, nullable=False),
    Column("first_seen_at", TS, nullable=False),
    Column("last_seen_at", TS, nullable=False),
    Column("resolved_at", TS),
    Column("status", String(16), nullable=False),  # active | resolved
    Column("am_state", String(16)),  # active | suppressed | unprocessed (từ Alertmanager)
    Column("current_occurrence_id", BigInteger),
    Column("missing_polls", Integer, nullable=False, server_default="0"),
    Column("first_missing_at", TS),
    CheckConstraint("status IN ('active','resolved')", name="ck_alerts_status"),
)
Index("ix_alerts_status_starts", alerts.c.status, alerts.c.starts_at)

# Mỗi lần "phát sinh" = 1 dòng. Báo cáo ca tính trên bảng này.
occurrences = Table(
    "alert_occurrences",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("fingerprint", String(64), nullable=False),
    Column("alertname", Text, nullable=False),
    Column("summary", Text, nullable=False, server_default=""),
    Column("severity", Text),
    Column("owner", Text),
    Column("application", Text),
    Column("instance", Text),
    Column("labels", JSONB, nullable=False),
    Column("starts_at", TS, nullable=False),
    Column("first_seen_at", TS, nullable=False),
    Column("resolved_at", TS),
    Column("resolve_source", String(16)),  # api_missing | ends_at | refire | filtered_out
)
Index("ix_occ_starts_at", occurrences.c.starts_at)
Index("ix_occ_resolved_at", occurrences.c.resolved_at)
Index("ix_occ_fingerprint", occurrences.c.fingerprint)

# Snapshot chốt lúc hết ca
shift_snapshots = Table(
    "shift_snapshots",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("report_date", Date, nullable=False),
    Column("shift_name", String(32), nullable=False),
    Column("period_start", TS, nullable=False),
    Column("period_end", TS, nullable=False),
    Column("carried_over", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("pdf_path", Text),
    Column("generated_at", TS, nullable=False),
    UniqueConstraint("report_date", "shift_name", name="uq_snapshot_date_shift"),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("full_name", Text, nullable=False, server_default=""),
    Column("password_hash", Text, nullable=False),
    Column("role", String(16), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default=text("true")),
    Column("must_change_password", Boolean, nullable=False, server_default=text("false")),
    Column("failed_attempts", Integer, nullable=False, server_default="0"),
    Column("locked_until", TS),
    Column("created_at", TS, nullable=False, server_default=text("now()")),
    Column("updated_at", TS, nullable=False, server_default=text("now()")),
    Column("last_login_at", TS),
    CheckConstraint("role IN ('viewer','admin')", name="ck_users_role"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    Column("created_at", TS, nullable=False),
    Column("expires_at", TS, nullable=False),
    Column("last_seen_at", TS, nullable=False),
    Column("remember", Boolean, nullable=False, server_default=text("false")),
    Column("ip", Text),
    Column("user_agent", Text),
)
Index("ix_sessions_user", sessions.c.user_id)
Index("ix_sessions_expires", sessions.c.expires_at)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("username", String(64)),
    Column("action", String(64), nullable=False),
    Column("target", Text),
    Column("detail", JSONB),
    Column("ip", Text),
    Column("created_at", TS, nullable=False, server_default=text("now()")),
)
Index("ix_audit_created", audit_log.c.created_at)


_engine: Engine | None = None


def init_engine(url: str, pool_size: int = 5) -> Engine:
    global _engine
    _engine = create_engine(url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("DB engine chưa được khởi tạo")
    return _engine


def run_migrations(url: str) -> None:
    """Chạy Alembic upgrade head (gọi khi khởi động)."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["sqlalchemy_url"] = url
    command.upgrade(cfg, "head")
    log.info("db_migrated")
