"""Chuẩn hoá mức độ (label severity) về 3 nhóm: critical | warning | other.

So khớp không phân biệt hoa thường và bỏ khoảng trắng hai đầu: "Warning", " WARNING ", "warning" -> warning.
Giá trị gốc vẫn được giữ ở cột `severity` và trong labels. Mapping lấy từ config (`severity_mapping`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import case, func, literal, or_, update
from sqlalchemy.engine import Connection, Engine

from .db import alerts, occurrences

LEVELS: tuple[str, ...] = ("critical", "warning", "other")
LABELS: dict[str, str] = {"critical": "Critical", "warning": "Warning", "other": "Khác"}
# Thứ tự khi sắp xếp theo mức độ (giảm dần = nghiêm trọng nhất trước)
RANK: dict[str, int] = {"critical": 2, "warning": 1, "other": 0}
DEFAULT_MAPPING: dict[str, list[str]] = {"critical": ["critical"], "warning": ["warning"]}

SeverityMapping = Mapping[str, Sequence[str]]


def clean(value: object) -> str:
    """Khoá so khớp: bỏ khoảng trắng hai đầu, chữ thường."""
    return str(value).strip().casefold() if value is not None else ""


def normalize_severity(raw: object, mapping: SeverityMapping | None = None) -> str:
    key = clean(raw)
    if not key:
        return "other"
    for level, values in (mapping or DEFAULT_MAPPING).items():
        if key in {clean(v) for v in values}:
            return level
    return "other"


def parse_level(value: str) -> str | None:
    """Tham số lọc từ API/giao diện -> nhóm chuẩn. Nhận cả nhãn ('Khác') và biến thể hoa/thường."""
    key = clean(value)
    if key in LEVELS:
        return key
    for level, label in LABELS.items():
        if key == clean(label):
            return level
    return None


def _case_expr(col, mapping: SeverityMapping):  # type: ignore[no-untyped-def]
    key = func.lower(func.btrim(col))
    whens = [(key.in_([clean(v) for v in values]), literal(level))
             for level, values in mapping.items() if values]
    return case(*whens, else_=literal("other")) if whens else literal("other")


def renormalize(conn_or_engine: Engine | Connection, mapping: SeverityMapping | None = None) -> int:
    """Tính lại severity_normalized cho toàn bộ dữ liệu (đang diễn ra + lịch sử) theo mapping hiện hành.

    Idempotent: chỉ cập nhật các dòng có giá trị khác kết quả mới, chạy lại bao nhiêu lần cũng không đổi gì thêm.
    Dùng khi khởi động và khi nạp lại cấu hình có đổi severity_mapping.
    """
    mapping = mapping or DEFAULT_MAPPING

    def run(conn: Connection) -> int:
        changed = 0
        for table in (alerts, occurrences):
            expr = _case_expr(table.c.severity, mapping)
            stmt = (update(table).values(severity_normalized=expr)
                    .where(or_(table.c.severity_normalized.is_(None), table.c.severity_normalized != expr)))
            changed += conn.execute(stmt).rowcount or 0
        return changed

    if isinstance(conn_or_engine, Engine):
        with conn_or_engine.begin() as conn:
            return run(conn)
    return run(conn_or_engine)
