"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("alertname", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("severity", sa.Text),
        sa.Column("owner", sa.Text),
        sa.Column("application", sa.Text),
        sa.Column("instance", sa.Text),
        sa.Column("labels", JSONB, nullable=False),
        sa.Column("annotations", JSONB, nullable=False),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("resolved_at", TS),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("am_state", sa.String(16)),
        sa.Column("current_occurrence_id", sa.BigInteger),
        sa.Column("missing_polls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_missing_at", TS),
        sa.CheckConstraint("status IN ('active','resolved')", name="ck_alerts_status"),
    )
    op.create_index("ix_alerts_status_starts", "alerts", ["status", "starts_at"])

    op.create_table(
        "alert_occurrences",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("alertname", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("severity", sa.Text),
        sa.Column("owner", sa.Text),
        sa.Column("application", sa.Text),
        sa.Column("instance", sa.Text),
        sa.Column("labels", JSONB, nullable=False),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("resolved_at", TS),
        sa.Column("resolve_source", sa.String(16)),
    )
    op.create_index("ix_occ_starts_at", "alert_occurrences", ["starts_at"])
    op.create_index("ix_occ_resolved_at", "alert_occurrences", ["resolved_at"])
    op.create_index("ix_occ_fingerprint", "alert_occurrences", ["fingerprint"])

    op.create_table(
        "shift_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("shift_name", sa.String(32), nullable=False),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS, nullable=False),
        sa.Column("carried_over", sa.Integer, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("pdf_path", sa.Text),
        sa.Column("generated_at", TS, nullable=False),
        sa.UniqueConstraint("report_date", "shift_name", name="uq_snapshot_date_shift"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("full_name", sa.Text, nullable=False, server_default=""),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("failed_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("last_login_at", TS),
        sa.CheckConstraint("role IN ('viewer','admin')", name="ck_users_role"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("ip", sa.Text),
        sa.Column("user_agent", sa.Text),
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires", "sessions", ["expires_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("username", sa.String(64)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.Text),
        sa.Column("detail", JSONB),
        sa.Column("ip", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_created", "audit_log", ["created_at"])


def downgrade() -> None:
    for t in ("audit_log", "sessions", "users", "shift_snapshots", "alert_occurrences", "alerts"):
        op.drop_table(t)
