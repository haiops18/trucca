"""Mock Alertmanager API v2 phục vụ demo/test TrucCa.

Chạy: python -m mock.mock_alertmanager  (cổng 9093)

Biến môi trường:
  MOCK_ALERTS=150         số cảnh báo active ban đầu
  MOCK_CHURN_SECONDS=30   chu kỳ thay đổi (sinh mới / resolve ngẫu nhiên)
  MOCK_SEED=42            seed ngẫu nhiên (tái lập được)
  MOCK_BASIC_USER / MOCK_BASIC_PASS   bật basic auth để thử cấu hình auth

Điều khiển lỗi để thử giao diện:
  POST /mock/fail?mode=500|401|timeout|garbage|empty|off
"""

from __future__ import annotations

import base64
import hashlib
import os
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .data import ALERTS, APPLICATIONS, OWNERS

UTC = UTC
rng = random.Random(int(os.environ.get("MOCK_SEED", "42")))
lock = threading.Lock()
active: dict[str, dict[str, Any]] = {}
fail_mode = "off"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def make_alert(started: datetime) -> dict[str, Any]:
    name, summary, severity, _ = rng.choices(ALERTS, weights=[a[3] for a in ALERTS])[0]
    app = rng.choice(APPLICATIONS)
    port = rng.choice([9100, 9104, 9182, 8080])
    inst = f"10.{rng.randint(200, 230)}.{rng.randint(1, 250)}.{rng.randint(2, 250)}:{port}"
    labels = {"alertname": name, "application": app, "severity": severity, "instance": inst, "job": "node"}
    owner = rng.choice(OWNERS)
    if owner:
        labels["owner"] = owner
    fp = hashlib.sha1("|".join(f"{k}={v}" for k, v in sorted(labels.items())).encode()).hexdigest()[:16]
    silenced = rng.random() < 0.03
    return {
        "fingerprint": fp,
        "labels": labels,
        "annotations": {"summary": summary.format(inst=inst), "description": f"{name} on {inst}"},
        "startsAt": iso(started),
        "endsAt": iso(datetime.now(UTC) + timedelta(minutes=5)),
        "updatedAt": iso(datetime.now(UTC)),
        "generatorURL": "http://prometheus:9090/graph",
        "receivers": [{"name": "noc"}],
        "status": {"state": "suppressed" if silenced else "active",
                   "silencedBy": ["mock-silence"] if silenced else [], "inhibitedBy": []},
    }


def seed(n: int) -> None:
    now = datetime.now(UTC)
    for _ in range(n):
        # Phân bố tuổi: nhiều cảnh báo mới, một số tồn lâu (để có mục bàn giao)
        hours = rng.choice([rng.uniform(0, 2), rng.uniform(0, 10), rng.uniform(5, 30), rng.uniform(24, 240)])
        a = make_alert(now - timedelta(hours=hours))
        active[a["fingerprint"]] = a


def churn_loop(period: int) -> None:
    while True:
        time.sleep(period)
        with lock:
            now = datetime.now(UTC)
            for fp in list(active):
                if rng.random() < 0.04:
                    del active[fp]
            for _ in range(rng.randint(0, 6)):
                a = make_alert(now - timedelta(seconds=rng.randint(0, period)))
                active[a["fingerprint"]] = a
            for a in active.values():
                a["endsAt"] = iso(now + timedelta(minutes=5))
                a["updatedAt"] = iso(now)


app = FastAPI(title="Mock Alertmanager", docs_url=None, redoc_url=None)


def _auth_ok(request: Request) -> bool:
    user, pw = os.environ.get("MOCK_BASIC_USER"), os.environ.get("MOCK_BASIC_PASS")
    if not user:
        return True
    expected = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    return request.headers.get("authorization") == expected


@app.get("/api/v2/alerts")
def alerts(request: Request) -> Response:
    if fail_mode == "500":
        return PlainTextResponse("internal error", status_code=500)
    if fail_mode == "401" or not _auth_ok(request):
        return PlainTextResponse("unauthorized", status_code=401)
    if fail_mode == "timeout":
        time.sleep(30)
    if fail_mode == "garbage":
        return PlainTextResponse("<html>not json</html>")
    if fail_mode == "empty":
        return JSONResponse([])
    with lock:
        return JSONResponse(list(active.values()))


@app.post("/mock/fail")
def set_fail(mode: str = "off") -> dict[str, str]:
    global fail_mode
    fail_mode = mode
    return {"fail_mode": fail_mode}


@app.get("/-/healthy")
def healthy() -> PlainTextResponse:
    return PlainTextResponse("OK")


def main() -> None:
    seed(int(os.environ.get("MOCK_ALERTS", "150")))
    threading.Thread(target=churn_loop, args=(int(os.environ.get("MOCK_CHURN_SECONDS", "30")),), daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("MOCK_PORT", "9093")), log_level="warning")


if __name__ == "__main__":
    main()
