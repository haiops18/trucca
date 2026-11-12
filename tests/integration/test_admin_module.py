"""Module Quản trị: phân quyền admin / người dùng thường, redirect URL cũ, API đổi mật khẩu."""

import pytest

from .conftest import PASSWORD, login


def test_admin_home_redirects_by_role(admin, viewer):
    r = admin.get("/admin")
    assert r.status_code == 302 and r.headers["location"].endswith("/admin/users")
    r = viewer.get("/admin")
    assert r.status_code == 302 and r.headers["location"].endswith("/admin/password")


def test_admin_sees_three_sections(admin):
    for path in ("/admin/users", "/admin/system", "/admin/password"):
        r = admin.get(path)
        assert r.status_code == 200, path
        html = r.text
        for sec in ('data-section="users"', 'data-section="system"', 'data-section="password"'):
            assert sec in html, (path, sec)
        assert 'data-nav="admin" aria-current="page"' in html  # menu "Quản trị" đang active
        assert 'class="crumbs"' in html
    # Menu cũ rời rạc đã bỏ
    html = admin.get("/").text
    assert 'href="/admin/users"' not in html and 'href="/admin/system"' not in html
    assert 'href="/admin" data-nav="admin"' in html


def test_viewer_only_sees_password(viewer):
    r = viewer.get("/admin/password")
    assert r.status_code == 200
    assert 'data-section="users"' not in r.text and 'data-section="system"' not in r.text
    assert "Đổi mật khẩu" in r.text


@pytest.mark.parametrize("path", ["/admin/users", "/admin/system"])
def test_viewer_forbidden_admin_pages(viewer, path):
    assert viewer.get(path).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/users"), ("get", "/api/admin/system"), ("get", "/api/admin/audit"),
    ("post", "/api/admin/config/reload"), ("post", "/api/admin/users"),
    ("post", "/api/admin/users/1/reset-password"),
])
def test_viewer_forbidden_admin_api(viewer, method, path):
    kw = {"json": {}} if method == "post" else {}
    r = getattr(viewer, method)(path, **kw)
    assert r.status_code == 403, (path, r.text)
    assert r.json()["error"]["code"] == "forbidden"


def test_admin_api_allowed(admin):
    assert admin.get("/api/admin/users").status_code == 200
    assert admin.get("/api/admin/system").status_code == 200


def test_anonymous(app_client):
    from fastapi.testclient import TestClient

    anon = TestClient(app_client[0], follow_redirects=False)
    r = anon.get("/admin")
    assert r.status_code == 303 and r.headers["location"].endswith("/login")
    assert anon.post("/api/admin/password", json={"current_password": "x", "new_password": "y"}).status_code == 401


def test_legacy_account_url_redirects(viewer):
    r = viewer.get("/account")
    assert r.status_code == 301 and r.headers["location"].endswith("/admin/password")


def test_change_own_password_rules(app_client, users):
    from sqlalchemy import select

    from trucca.db import audit_log

    sess = login(app_client[0], users["pwuser"])
    # Yêu cầu đúng mật khẩu hiện tại
    r = sess.post("/api/admin/password", json={"current_password": "sai-mat-khau", "new_password": "Moi-Pass-123"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_current_password"
    # Kiểm tra độ mạnh theo quy tắc hiện tại
    r = sess.post("/api/admin/password", json={"current_password": PASSWORD, "new_password": "yeu"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "weak_password"
    # Thiếu CSRF -> 403
    r = sess.c.post("/api/admin/password", json={"current_password": PASSWORD, "new_password": "Moi-Pass-123"})
    assert r.status_code == 403
    # Đổi thành công qua API mới, rồi đổi lại qua API cũ (deprecated nhưng vẫn chạy)
    r = sess.post("/api/admin/password", json={"current_password": PASSWORD, "new_password": "Moi-Pass-123"})
    assert r.status_code == 200, r.text
    r = sess.post("/api/account/password", json={"current_password": "Moi-Pass-123", "new_password": PASSWORD})
    assert r.status_code == 200
    assert r.headers.get("deprecation") == "true" and "/api/admin/password" in r.headers.get("link", "")
    # Mật khẩu không xuất hiện trong nhật ký
    engine = app_client[0].state.trucca.engine
    with engine.connect() as c:
        dump = " ".join(str(tuple(row)) for row in c.execute(select(audit_log)))
    assert "Moi-Pass-123" not in dump and PASSWORD not in dump


def test_must_change_user_goes_to_admin_password(app_client):
    from fastapi.testclient import TestClient

    c = TestClient(app_client[0], follow_redirects=False)
    r = c.post("/auth/login", json={"username": "admin", "password": "admin"})  # admin mặc định, phải đổi mật khẩu
    assert r.status_code == 200 and r.json()["redirect"].endswith("/admin/password")
    r = c.get("/")
    assert r.status_code == 303 and r.headers["location"].endswith("/admin/password")
    page = c.get("/admin/password")
    assert page.status_code == 200
    assert 'data-section="users"' not in page.text  # chỉ thấy mục đổi mật khẩu cho tới khi đổi xong
    assert c.get("/api/admin/users").status_code == 403
