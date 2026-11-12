"""Migration chuẩn hoá severity: chạy lặp lại không lỗi, không mất dữ liệu gốc."""

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from trucca.db import alerts
from trucca.severity import renormalize


def _cfg(url: str) -> Config:
    from pathlib import Path

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    cfg.attributes["sqlalchemy_url"] = url
    return cfg


def _snapshot(engine):
    with engine.connect() as c:
        return {r.fingerprint: (r.severity, r.severity_normalized, dict(r.labels))
                for r in c.execute(select(alerts.c.fingerprint, alerts.c.severity, alerts.c.severity_normalized,
                                          alerts.c.labels).where(alerts.c.fingerprint.like("it-%")))}


def test_migration_rerun_is_safe_and_keeps_raw(db_url, engine, seeded):
    before = _snapshot(engine)
    assert before["it-w2"] == ("WARNING", "warning", {"alertname": "Alert it-w2", "severity": "WARNING"})
    assert before["it-o2"][1] == "other"
    cfg = _cfg(db_url)
    # 1) upgrade head khi đã ở head: không làm gì, không lỗi
    command.upgrade(cfg, "head")
    # 2) Chạy LẠI 0003 trên DB đã có cột (mô phỏng chạy lặp): stamp lùi rồi upgrade
    command.stamp(cfg, "0002")
    command.upgrade(cfg, "head")
    command.stamp(cfg, "0002")
    command.upgrade(cfg, "head")
    assert _snapshot(engine) == before
    # 3) downgrade rồi upgrade: cột chuẩn hoá được tính lại từ giá trị gốc, giá trị gốc + labels giữ nguyên
    command.downgrade(cfg, "0002")
    command.upgrade(cfg, "head")
    assert _snapshot(engine) == before


def test_renormalize_idempotent_and_follows_mapping(engine, seeded):
    assert renormalize(engine) == 0  # đã đúng -> không đụng dòng nào
    mapping = {"critical": ["critical", "INFO"], "warning": ["warning"]}
    assert renormalize(engine, mapping) >= 1
    assert _snapshot(engine)["it-o1"][1] == "critical"
    assert renormalize(engine, mapping) == 0
    renormalize(engine)  # trả về mapping mặc định
    assert _snapshot(engine)["it-o1"][1] == "other"
