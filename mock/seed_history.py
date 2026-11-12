"""Sinh lịch sử cảnh báo giả (đã resolve) vào DB để demo báo cáo ca / xu thế 7 ngày.

Chạy trong container:  docker compose exec trucca python -m mock.seed_history --days 8
Chỉ thêm occurrence có fingerprint bắt đầu bằng 'seed-'; chạy lại sẽ xoá dữ liệu seed cũ trước.
"""

from __future__ import annotations

import argparse
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from trucca.config import load_config
from trucca.db import init_engine, occurrences, run_migrations

from .data import ALERTS, APPLICATIONS

UTC = UTC


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--per-day", type=int, default=150, help="số phát sinh trung bình mỗi ngày")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    cfg = load_config()
    url = cfg.database.url.get_secret_value()
    run_migrations(url)
    engine = init_engine(url)
    rng = random.Random(args.seed)
    now = datetime.now(UTC)
    rows = []
    app_weights = [rng.uniform(0.3, 3) for _ in APPLICATIONS]
    for d in range(args.days, 0, -1):
        n = max(10, int(rng.gauss(args.per_day, args.per_day * 0.35)))
        day_start = now - timedelta(days=d)
        for i in range(n):
            starts = day_start + timedelta(seconds=rng.randint(0, 86400))
            if starts >= now - timedelta(minutes=30):
                continue
            name, summary, severity, _ = rng.choices(ALERTS, weights=[a[3] for a in ALERTS])[0]
            app = rng.choices(APPLICATIONS, weights=app_weights)[0]
            inst = f"10.{rng.randint(200, 230)}.{rng.randint(1, 250)}.{rng.randint(2, 250)}:9100"
            resolved = starts + timedelta(minutes=rng.choice([5, 15, 40, 90, 240, 600]) * rng.uniform(0.5, 1.5))
            if resolved >= now:
                resolved = now - timedelta(minutes=1)
            rows.append({
                "fingerprint": f"seed-{d}-{i}", "alertname": name, "summary": summary.format(inst=inst),
                "severity": severity, "owner": None, "application": app, "instance": inst,
                "labels": {"alertname": name, "application": app, "severity": severity, "instance": inst},
                "starts_at": starts, "first_seen_at": starts, "resolved_at": resolved, "resolve_source": "api_missing",
            })
    with engine.begin() as conn:
        conn.execute(delete(occurrences).where(occurrences.c.fingerprint.like("seed-%")))
        conn.execute(occurrences.insert(), rows)
    print(f"Đã thêm {len(rows)} occurrence lịch sử ({args.days} ngày).")


if __name__ == "__main__":
    main()
