"""severity_normalized cho alerts / alert_occurrences + chuẩn hoá dữ liệu lịch sử

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-12

An toàn khi chạy lại: ADD COLUMN / CREATE INDEX dùng IF NOT EXISTS, câu UPDATE chỉ đụng các dòng chưa đúng.
Không xoá/sửa giá trị gốc (cột severity và labels giữ nguyên). Mapping mặc định ở đây là
critical -> critical, warning -> warning; khi ứng dụng khởi động sẽ tính lại theo severity_mapping trong config.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_EXPR = ("CASE lower(btrim(severity)) WHEN 'critical' THEN 'critical' WHEN 'warning' THEN 'warning' "
         "ELSE 'other' END")


def upgrade() -> None:
    for table in ("alerts", "alert_occurrences"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS severity_normalized varchar(16) "
                   "NOT NULL DEFAULT 'other'")
        # table / _EXPR là hằng số trong file này, không có dữ liệu từ người dùng
        op.execute(f"UPDATE {table} SET severity_normalized = {_EXPR} "  # noqa: S608
                   f"WHERE severity_normalized IS DISTINCT FROM {_EXPR}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_alerts_severity_norm ON alerts (severity_normalized)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_alerts_severity_norm")
    for table in ("alerts", "alert_occurrences"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS severity_normalized")
