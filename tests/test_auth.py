"""웹 인증(web/auth.py) 테스트 — CSRF, IP 화이트리스트, 세션, 브루트포스."""
import re
import time
from unittest.mock import MagicMock, patch

import pytest

from database import auth_repo
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
        # bulk_enabled는 재인증(step-up) 대상이 아닌 순수 CSRF 시나리오용 엔드포인트
        resp = auth_client.post(
            "/api/bot/bulk_enabled",
            json={"symbol": "ETH", "enabled": True},
            headers={"X-CSRF-Token": token},
        )
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


# ═══════════════════════════════════════════════════════════════════════════════
# IP 화이트리스트 CIDR 지원
# ═══════════════════════════════════════════════════════════════════════════════

class TestIpAllowed:
    def test_empty_whitelist_matches_nothing(self):
        # "빈 화이트리스트 = 전체 허용"은 _guard()가 이 함수를 호출하기 전에
        # `if whitelist and ...`로 이미 걸러내는 상위 계층의 책임이다.
        from web.auth import _ip_allowed
        assert _ip_allowed("203.0.113.5", []) is False

    def test_local_ip_always_allowed(self):
        from web.auth import _ip_allowed
        assert _ip_allowed("127.0.0.1", ["10.0.0.0/24"]) is True

    def test_exact_ip_match(self):
        from web.auth import _ip_allowed
        assert _ip_allowed("10.73.56.28", ["10.73.56.28"]) is True
        assert _ip_allowed("10.73.56.29", ["10.73.56.28"]) is False

    def test_cidr_match(self):
        from web.auth import _ip_allowed
        assert _ip_allowed("10.73.56.100", ["10.73.56.0/24"]) is True
        assert _ip_allowed("10.73.57.1",   ["10.73.56.0/24"]) is False

    def test_invalid_entry_ignored_not_fatal(self):
        from web.auth import _ip_allowed
        assert _ip_allowed("10.73.56.28", ["not-an-ip", "10.73.56.0/24"]) is True

    def test_malformed_remote_rejected(self):
        from web.auth import _ip_allowed
        assert _ip_allowed("not-an-ip", ["10.73.56.0/24"]) is False


class TestIpWhitelistGuardIntegration:
    def test_request_inside_cidr_allowed(self, auth_app):
        auth_app.config["CFG"]["web"]["auth"]["ip_whitelist"] = ["10.73.56.0/24"]
        with auth_app.test_client() as c:
            resp = c.get("/login", environ_overrides={"REMOTE_ADDR": "10.73.56.99"})
            assert resp.status_code == 200

    def test_request_outside_cidr_blocked(self, auth_app):
        auth_app.config["CFG"]["web"]["auth"]["ip_whitelist"] = ["10.73.56.0/24"]
        with auth_app.test_client() as c:
            resp = c.get("/api/status", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
            assert resp.status_code == 403


class TestIpWhitelistSettingsValidation:
    def test_valid_cidr_saved(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.post(
            "/api/settings",
            json={"ip_whitelist": ["10.73.56.0/24", "127.0.0.1"]},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_invalid_entry_rejected(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.post(
            "/api/settings",
            json={"ip_whitelist": ["not-an-ip"]},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 세션 무효화 (비밀번호 변경 감지)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionInvalidation:
    def test_session_survives_when_password_unchanged(self, auth_client):
        _login(auth_client)
        resp = auth_client.get("/api/status")
        assert resp.status_code == 200

    def test_session_invalidated_after_password_change(self, auth_app):
        with auth_app.test_client() as c:
            _login(c)
            assert c.get("/api/status").status_code == 200

            # 비밀번호 변경 (설정 재로드 없이 라이브 cfg 직접 변경 — 실제 저장 경로와 동일한 딕셔너리 갱신)
            auth_app.config["CFG"]["web"]["auth"]["password"] = "changed-password"

            resp = c.get("/api/status")
            assert resp.status_code == 401  # 세션 무효화 → 재로그인 필요


# ═══════════════════════════════════════════════════════════════════════════════
# 민감 작업 재인증 (step-up)
# ═══════════════════════════════════════════════════════════════════════════════

class TestReauth:
    def test_reauth_not_required_when_auth_disabled(self):
        from web.app import create_app
        bm = _make_bot_manager()
        flask_app = create_app(bm, MagicMock(), {})  # 비밀번호 미설정 = 인증 비활성
        flask_app.config["TESTING"] = True
        with flask_app.test_client() as c:
            resp = c.post("/api/bot/stop_all", json={})  # _reauth_password 없이도 통과
            assert resp.status_code == 200

    def test_stop_all_without_reauth_password_rejected(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.post("/api/bot/stop_all", json={}, headers={"X-CSRF-Token": token})
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "reauth_required"

    def test_stop_all_with_wrong_reauth_password_rejected(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.post(
            "/api/bot/stop_all",
            json={"_reauth_password": "wrong"},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 403

    def test_stop_all_with_correct_reauth_password_allowed(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.post(
            "/api/bot/stop_all",
            json={"_reauth_password": AUTH_PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200

    def test_delete_range_without_reauth_password_rejected(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.delete(
            "/api/trades/orders",
            json={"start": "2026-01-01", "end": "2026-01-02", "symbol": "all"},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "reauth_required"

    def test_delete_range_with_correct_reauth_password_allowed(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.delete(
            "/api/trades/orders",
            json={"start": "2026-01-01", "end": "2026-01-02", "symbol": "all",
                  "_reauth_password": AUTH_PASSWORD},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200

    def test_delete_by_ids_does_not_require_reauth(self, auth_client):
        _login(auth_client)
        token = _csrf_token(auth_client)
        resp = auth_client.delete(
            "/api/trades/orders",
            json={"ids": []},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 브루트포스 방어 영속화 + 알림
# ═══════════════════════════════════════════════════════════════════════════════

ATTACKER_IP = "203.0.113.5"  # 127.0.0.1은 자기잠금 방지로 항상 예외 처리되므로 테스트엔 비-로컬 IP 사용


def _post_login(c, password, ip=ATTACKER_IP):
    return c.post("/login", data={"password": password}, environ_overrides={"REMOTE_ADDR": ip})


class TestBruteForcePersistence:
    def test_failed_login_recorded_in_db(self, auth_client):
        _post_login(auth_client, "wrong")
        rec = auth_repo.get_attempt(ATTACKER_IP)
        assert rec is not None
        assert rec["fail_count"] == 1

    def test_successful_login_clears_record(self, auth_client):
        _post_login(auth_client, "wrong")
        assert auth_repo.get_attempt(ATTACKER_IP) is not None
        _post_login(auth_client, AUTH_PASSWORD)
        assert auth_repo.get_attempt(ATTACKER_IP) is None

    def test_lock_after_max_fails(self, auth_client):
        for _ in range(5):
            _post_login(auth_client, "wrong")
        rec = auth_repo.get_attempt(ATTACKER_IP)
        assert rec["locked_until"] > time.time()
        assert rec["fail_count"] == 0  # 잠금 발동 시 카운트 리셋

        resp = _post_login(auth_client, AUTH_PASSWORD)
        assert resp.status_code == 429  # 잠금 중엔 올바른 비밀번호도 거부

    def test_lock_persists_with_fresh_flask_app_same_db(self, auth_app):
        # 같은 (스레드로컬) DB 커넥션을 공유하는 새 Flask 앱 인스턴스를 만들어
        # "앱 재시작 후에도 잠금 유지"를 검증한다 — 인메모리 dict였다면 새
        # 앱 인스턴스에서는 당연히 사라졌을 상태다.
        with auth_app.test_client() as c:
            for _ in range(5):
                _post_login(c, "wrong")

        from web.app import create_app
        bm2 = _make_bot_manager()
        app2 = create_app(bm2, MagicMock(), _auth_cfg())
        app2.config["TESTING"] = True
        with app2.test_client() as c2:
            resp = _post_login(c2, AUTH_PASSWORD)
            assert resp.status_code == 429

    def test_lock_triggers_telegram_notification(self, auth_client):
        cfg = auth_client.application.config["CFG"]
        cfg["telegram"] = {"bot_token": "test-token", "chat_id": "12345"}

        with patch("web.auth.requests.post") as mock_post:
            for _ in range(5):
                _post_login(auth_client, "wrong")
            time.sleep(0.3)  # 데몬 스레드(fire-and-forget) 완료 대기

        assert mock_post.called
        args, kwargs = mock_post.call_args
        assert "test-token" in args[0]
        assert kwargs["json"]["chat_id"] == "12345"

    def test_no_notification_when_telegram_not_configured(self, auth_client):
        with patch("web.auth.requests.post") as mock_post:
            for _ in range(5):
                _post_login(auth_client, "wrong")
            time.sleep(0.2)
        assert not mock_post.called
