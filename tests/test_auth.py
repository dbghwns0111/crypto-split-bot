"""웹 인증(web/auth.py) 테스트 — CSRF, IP 화이트리스트, 세션, 브루트포스."""
import re
from unittest.mock import MagicMock

import pytest

from database.connection import close_connection, configure, get_connection
from database.models import create_tables


# ── 픽스처 ────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    configure(":memory:")
    conn = get_connection()
    create_tables(conn)
    yield conn
    close_connection()
    configure(None)


def _make_bot_manager():
    bm = MagicMock()
    bm.symbols = ["ETH", "USDT"]
    bm.stop_all.return_value = ["ETH"]
    bm.get_all_status.side_effect = lambda: {s: {"running": False} for s in bm.symbols}
    return bm


AUTH_PASSWORD = "testpass123"


def _auth_cfg(**overrides):
    auth = {"enabled": True, "password": AUTH_PASSWORD, "ip_whitelist": []}
    auth.update(overrides)
    return {"web": {"auth": auth}}


@pytest.fixture
def auth_app():
    from web.app import create_app
    bm = _make_bot_manager()
    flask_app = create_app(bm, MagicMock(), _auth_cfg())
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def auth_client(auth_app):
    with auth_app.test_client() as c:
        yield c


def _login(c, password=AUTH_PASSWORD):
    return c.post("/login", data={"password": password})


def _csrf_token(c):
    html = c.get("/").data.decode()
    m = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html)
    assert m, "csrf-token meta 태그를 찾을 수 없음"
    return m.group(1)


# ═══════════════════════════════════════════════════════════════════════════════
# CSRF 보호
# ═══════════════════════════════════════════════════════════════════════════════

class TestCsrf:
    def test_post_without_csrf_token_rejected(self, auth_client):
        _login(auth_client)
        resp = auth_client.post("/api/bot/stop_all", json={})
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "csrf_failed"

    def test_post_with_wrong_csrf_token_rejected(self, auth_client):
        _login(auth_client)
        resp = auth_client.post("/api/bot/stop_all", json={}, headers={"X-CSRF-Token": "wrong"})
        assert resp.status_code == 403

    def test_post_with_valid_csrf_token_allowed(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.post("/api/bot/stop_all", json={}, headers={"X-CSRF-Token": token})
        assert resp.status_code == 200

    def test_get_requests_not_csrf_protected(self, auth_client):
        _login(auth_client)
        resp = auth_client.get("/api/status")
        assert resp.status_code == 200

    def test_csrf_not_enforced_when_auth_disabled(self):
        from web.app import create_app
        bm = _make_bot_manager()
        flask_app = create_app(bm, MagicMock(), {})  # 비밀번호 미설정 = 인증 비활성
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as c:
            resp = c.post("/api/bot/stop_all", json={})
            assert resp.status_code == 200
