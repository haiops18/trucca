"""Test tích hợp: chạy app thật (FastAPI + migrations) trên PostgreSQL thật.

Cần biến môi trường TRUCCA_TEST_DATABASE_URL trỏ tới một PostgreSQL dùng riêng cho test
(user có quyền CREATE DATABASE). Test tạo database mới `trucca_it` và xoá khi xong. Xem README mục "Kiểm thử".
Không đặt biến này thì các test tích hợp bị bỏ qua (báo rõ lý do), unit test vẫn chạy bình thường.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, insert, text
from sqlalchemy.engine import Engine, make_url

ADMIN_DB_URL = os.environ.get("TRUCCA_TEST_DATABASE_URL", "")
TEST_DB = "trucca_it"
PASSWORD = "It-Test-Pass-1!"  # noqa: S105 - mật khẩu của tài khoản test tạm, chỉ tồn tại trong DB test

pytestmark = pytest.mark.skipif(not ADMIN_DB_URL, reason="cần TRUCCA_TEST_DATABASE_URL (PostgreSQL cho test tích hợp)")


def _url_for(dbname: str) -> str:
    url = make_url(ADMIN_DB_URL).set(database=dbname)
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    if not ADMIN_DB_URL:
        pytest.skip("cần TRUCCA_TEST_DATABASE_URL")
    admin = create_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    yield _url_for(TEST_DB)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def app_client(db_url: str, tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from trucca.config import ConfigManager
    from trucca.main import create_app

    tmp = tmp_path_factory.mktemp("trucca")
    cfg = tmp / "config.yml"
    cfg.write_text(
        "alertmanager:\n  url: http://127.0.0.1:9\n  timeout_seconds: 1\n"
        f"report:\n  output_dir: {tmp / 'reports'}\n  auto_generate_at_shift_end: false\n",
        encoding="utf-8",
    )
    os.environ["DATABASE_URL"] = db_url
    os.environ.pop("TRUCCA_ADMIN_USERNAME", None)
    os.environ.pop("TRUCCA_ADMIN_PASSWORD", None)
    app = create_app(ConfigManager(str(cfg)))
    with TestClient(app, follow_redirects=False) as client:
        yield app, client


@pytest.fixture(scope="session")
def engine(app_client) -> Engine:  # type: ignore[no-untyped-def]
    app, _ = app_client
    return app.state.trucca.engine


@pytest.fixture(scope="session")
def users(engine: Engine) -> dict[str, str]:
    from trucca.auth.security import hash_password
    from trucca.db import users as users_t

    now = datetime.now(UTC)
    h = hash_password(PASSWORD)
    with engine.begin() as c:
        # it.pwuser: chỉ dùng cho test đổi mật khẩu (đổi mật khẩu đăng xuất các phiên khác của người đó)
        for name, role in (("it.admin", "admin"), ("it.viewer", "viewer"), ("it.pwuser", "viewer")):
            c.execute(insert(users_t).values(username=name, full_name=name, password_hash=h, role=role,
                                             is_active=True, must_change_password=False,
                                             created_at=now, updated_at=now))
    return {"admin": "it.admin", "viewer": "it.viewer", "pwuser": "it.pwuser"}


class Session:
    """Client đã đăng nhập (cookie riêng) + CSRF token để gọi API ghi."""

    def __init__(self, client, csrf: str):  # type: ignore[no-untyped-def]
        self.c = client
        self.csrf = csrf

    def get(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.c.get(url, **kw)

    def post(self, url: str, **kw):  # type: ignore[no-untyped-def]
        return self.c.post(url, headers={"X-CSRF-Token": self.csrf, **kw.pop("headers", {})}, **kw)


def login(app, username: str, password: str = PASSWORD) -> Session:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    client = TestClient(app, follow_redirects=False)
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    page = client.get("/admin/password")
    m = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert m, "không thấy CSRF token"
    return Session(client, m.group(1))


@pytest.fixture(scope="session")
def admin(app_client, users):  # type: ignore[no-untyped-def]
    return login(app_client[0], users["admin"])


@pytest.fixture(scope="session")
def viewer(app_client, users):  # type: ignore[no-untyped-def]
    return login(app_client[0], users["viewer"])


NOW = datetime.now(UTC).replace(microsecond=0)

# (fingerprint, severity gốc, số giờ trước) - đủ biến thể hoa/thường/khoảng trắng/giá trị lạ/thiếu
SEED = [
    ("it-w1", "Warning", 1), ("it-w2", "WARNING", 5), ("it-w3", "warning", 30), ("it-w4", " warning ", 3),
    ("it-c1", "Critical", 2), ("it-c2", "CRITICAL", 40), ("it-c3", "critical", 0.5),
    ("it-o1", "info", 4), ("it-o2", None, 8),
]


@pytest.fixture()
def seeded(engine: Engine) -> Iterator[list[tuple[str, str | None, float]]]:
    """Cảnh báo đang diễn ra với các biến thể severity. Ghi severity_normalized='other' rồi để renormalize tính."""
    from trucca.db import alerts
    from trucca.severity import renormalize

    with engine.begin() as c:
        c.execute(delete(alerts).where(alerts.c.fingerprint.like("it-%")))
        for fp, sev, hours in SEED:
            ts = NOW - timedelta(hours=hours)
            c.execute(insert(alerts).values(
                fingerprint=fp, alertname=f"Alert {fp}", summary=f"sum {fp}", severity=sev,
                severity_normalized="other", owner="noc", application="APP", instance=None,
                labels={"alertname": f"Alert {fp}", **({"severity": sev} if sev is not None else {})},
                annotations={}, starts_at=ts, first_seen_at=ts, last_seen_at=NOW, status="active"))
    renormalize(engine)
    yield SEED
    with engine.begin() as c:
        c.execute(delete(alerts).where(alerts.c.fingerprint.like("it-%")))
