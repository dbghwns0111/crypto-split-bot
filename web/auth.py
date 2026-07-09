"""웹 대시보드 접근 보안 — 세션 비밀번호 로그인 + IP 화이트리스트.

설계 원칙:
- 비밀번호는 .env 의 WEB_PASSWORD 에서만 로드한다 (하드코딩 금지).
- 비밀번호가 설정되지 않았으면 인증을 자동 비활성한다 (fail-open) —
  운영 중인 봇이 업데이트 pull 후 로그인 불가로 잠기는 것을 방지한다.
  보안은 사용자가 비밀번호를 설정한 시점부터만 활성화된다.
- IP 화이트리스트는 비밀번호와 독립적인 별도 방어선이다.
  목록이 비어 있으면 모든 IP 를 허용한다. 자기 잠금 방지를 위해
  127.0.0.1(로컬)은 항상 허용한다.
- 인증 설정(비밀번호/화이트리스트)은 매 요청 시 cfg 에서 다시 읽으므로
  설정 탭에서 변경하면 앱 재시작 없이 즉시 반영된다.
"""

import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from utils.logger import get_logger

_log = get_logger("web.auth")

auth_bp = Blueprint("auth", __name__)

# 인증 없이 항상 접근 가능한 엔드포인트 (로그인 페이지/정적파일 등)
_EXEMPT_ENDPOINTS = {"auth.login", "auth.logout", "static"}

_LOCAL_IPS = {"127.0.0.1", "::1", "localhost"}

# CSRF 보호 대상 HTTP 메서드 (상태 변경 요청)
_CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# ── 로그인 무차별 대입(brute-force) 방어 ──────────────────────────────────────
# 인터넷에 노출된 로그인 화면 보호용. 외부 의존성 없는 인메모리 IP별 시도 제한.
_LOGIN_MAX_FAILS = 5       # 잠금 발동까지 허용하는 연속 실패 횟수
_LOGIN_WINDOW    = 900     # 실패 횟수 집계 윈도우 (초) — 15분
_LOGIN_LOCK      = 600     # 초과 시 잠금 지속 시간 (초) — 10분

_login_lock = threading.Lock()
# ip -> {"fails": [타임스탬프...], "locked_until": float}
_login_attempts: dict[str, dict] = {}


def _login_locked_remaining(ip: str) -> int:
    """해당 IP가 잠금 상태면 남은 초를, 아니면 0을 반환한다."""
    if ip in _LOCAL_IPS:
        return 0
    now = time.monotonic()
    with _login_lock:
        rec = _login_attempts.get(ip)
        if not rec:
            return 0
        locked_until = rec.get("locked_until", 0)
        if locked_until > now:
            return int(locked_until - now)
        return 0


def _login_record_fail(ip: str) -> None:
    """로그인 실패를 기록하고, 윈도우 내 임계치 초과 시 잠금을 건다."""
    if ip in _LOCAL_IPS:
        return
    now = time.monotonic()
    with _login_lock:
        rec = _login_attempts.setdefault(ip, {"fails": [], "locked_until": 0})
        # 윈도우 밖의 오래된 실패 기록 제거
        rec["fails"] = [t for t in rec["fails"] if now - t < _LOGIN_WINDOW]
        rec["fails"].append(now)
        if len(rec["fails"]) >= _LOGIN_MAX_FAILS:
            rec["locked_until"] = now + _LOGIN_LOCK
            rec["fails"] = []
            _log.warning("로그인 잠금 발동: %s (%d초)", ip, _LOGIN_LOCK)


def _login_clear(ip: str) -> None:
    """로그인 성공 시 해당 IP의 실패/잠금 기록을 초기화한다."""
    with _login_lock:
        _login_attempts.pop(ip, None)


def _auth_cfg() -> dict:
    """현재 앱 cfg 에서 web.auth 설정을 반환한다 (매 요청 시 최신값)."""
    cfg = current_app.config.get("CFG", {}) or {}
    return (cfg.get("web", {}) or {}).get("auth", {}) or {}


def _password() -> str:
    return str(_auth_cfg().get("password", "") or "")


def _ip_whitelist() -> list[str]:
    wl = _auth_cfg().get("ip_whitelist", []) or []
    return [str(ip).strip() for ip in wl if str(ip).strip()]


def _ip_allowed(remote: str, whitelist: list[str]) -> bool:
    """remote가 화이트리스트에 포함되는지 확인한다. 항목은 단일 IP 또는 CIDR(a.b.c.d/n) 모두 허용."""
    if remote in _LOCAL_IPS:
        return True
    try:
        addr = ipaddress.ip_address(remote)
    except ValueError:
        return False
    for entry in whitelist:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            _log.warning("잘못된 형식의 화이트리스트 항목 무시: %s", entry)
    return False


def _auth_active() -> bool:
    """비밀번호 인증이 실제로 활성 상태인지.

    enabled=True 이고 비밀번호가 설정된 경우에만 활성.
    """
    if not _auth_cfg().get("enabled", True):
        return False
    return bool(_password())


def _csrf_token() -> str:
    """현재 세션의 CSRF 토큰을 반환한다 (없으면 새로 생성해 세션에 저장)."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["csrf_token"] = token
    return token


def reauth_ok(supplied: str) -> bool:
    """민감 작업(전체 시작/중지, 기간 전체 삭제 등) 재인증 통과 여부.

    비밀번호 인증 자체가 비활성이면(검사 대상이 없으므로) 통과시킨다 — 기존
    fail-open 철학과 동일. 인증이 활성 상태면 비밀번호가 정확히 일치해야 통과.
    """
    if not _auth_active():
        return True
    return hmac.compare_digest(str(supplied or ""), _password())


def _pw_fingerprint() -> str:
    """현재 비밀번호의 짧은 지문 — 세션에 저장해 비밀번호 변경 시 세션 무효화에 사용."""
    return hashlib.sha256(_password().encode("utf-8")).hexdigest()[:16]


def init_auth(app, cfg: dict) -> None:
    """Flask 앱에 인증 가드와 로그인/로그아웃 라우트를 설치한다."""
    auth = (cfg.get("web", {}) or {}).get("auth", {}) or {}

    # 세션 서명 키 — WEB_SECRET_KEY 가 있으면 재시작 후에도 세션 유지,
    # 없으면 매 실행 시 랜덤 생성 (재시작 시 재로그인 필요).
    secret = os.getenv("WEB_SECRET_KEY", "") or auth.get("secret_key", "")
    if not secret:
        secret = os.urandom(32).hex()
        _log.warning(
            "WEB_SECRET_KEY 미설정 — 임의 키 생성 (재시작 시 재로그인 필요). "
            ".env 에 WEB_SECRET_KEY 설정 권장."
        )
    app.secret_key = secret

    session_days = int(auth.get("session_days", 30) or 30)
    from datetime import timedelta
    app.permanent_session_lifetime = timedelta(days=session_days)

    # 세션 쿠키 보안 속성 — SameSite로 크로스사이트 요청에 쿠키가 실리지 않게 한다.
    # Secure는 강제하지 않는다: 현재 서버가 평문 HTTP라 강제 시 쿠키가 아예
    # 전송되지 않아 로그인이 깨진다(HTTPS 도입 시 별도로 True 전환 필요).
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    app.register_blueprint(auth_bp)
    app.before_request(_guard)
    app.context_processor(lambda: {"csrf_token": _csrf_token()})

    if auth.get("enabled", True) and not (
        os.getenv("WEB_PASSWORD", "") or auth.get("password", "")
    ):
        _log.warning(
            "웹 인증 활성(enabled=true) 이지만 WEB_PASSWORD 미설정 — "
            "인증 비활성 상태로 동작합니다. 설정 탭에서 비밀번호를 지정하세요."
        )


def _guard():
    """모든 요청 전에 실행되는 접근 제어 가드."""
    remote = (request.remote_addr or "").strip()

    # ── 1. IP 화이트리스트 (비밀번호와 독립) ───────────────────────────────
    whitelist = _ip_whitelist()
    if whitelist and not _ip_allowed(remote, whitelist):
        _log.warning("화이트리스트 외 IP 접근 차단: %s", remote)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "forbidden"}), 403
        return ("접근이 허용되지 않은 IP 입니다.", 403)

    # ── 2. 비밀번호 인증 ───────────────────────────────────────────────────
    if not _auth_active():
        return None  # 인증 비활성 — 통과

    if request.endpoint in _EXEMPT_ENDPOINTS:
        return None
    if request.endpoint == "serve_project_image":
        return None
    if session.get("authenticated"):
        # ── 3. 비밀번호 변경 감지 — 지문 불일치 시 세션 무효화 ───────────────
        if session.get("pw_fp") != _pw_fingerprint():
            _log.info("비밀번호 변경 감지 — 세션 무효화: %s", remote)
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "unauthorized"}), 401
            return redirect(url_for("auth.login", next=request.path))

        # ── 4. CSRF 토큰 (상태 변경 API 요청만) ─────────────────────────────
        if request.method in _CSRF_METHODS and request.path.startswith("/api/"):
            supplied = request.headers.get("X-CSRF-Token", "")
            if not supplied or not hmac.compare_digest(supplied, str(session.get("csrf_token", ""))):
                _log.warning("CSRF 토큰 불일치/누락: %s %s (%s)", request.method, request.path, remote)
                return jsonify({"ok": False, "error": "csrf_failed"}), 403
        return None

    # 미인증 — API 는 401, 페이지는 로그인으로 리다이렉트
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return redirect(url_for("auth.login", next=request.path))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # 인증 비활성이면 로그인 페이지 불필요 — 홈으로
    if not _auth_active():
        return redirect("/")

    remote = (request.remote_addr or "").strip()

    # 잠금 중인 IP는 비밀번호 검사 없이 차단
    remaining = _login_locked_remaining(remote)
    if remaining > 0:
        mins = (remaining + 59) // 60
        _log.warning("잠금된 IP의 로그인 시도 차단: %s (%d초 남음)", remote, remaining)
        return render_template(
            "login.html",
            error=f"로그인 시도가 너무 많습니다. 약 {mins}분 후 다시 시도하세요.",
        ), 429

    if request.method == "POST":
        pwd = request.form.get("password", "")
        if hmac.compare_digest(pwd, _password()):
            _login_clear(remote)
            session.permanent = True
            session["authenticated"] = True
            session["csrf_token"] = secrets.token_hex(32)  # 로그인 시 토큰 회전
            session["pw_fp"] = _pw_fingerprint()
            _log.info("웹 로그인 성공: %s", remote)
            nxt = request.args.get("next") or "/"
            # 오픈 리다이렉트 방지 — 내부 경로만 허용
            if not nxt.startswith("/"):
                nxt = "/"
            return redirect(nxt)
        _login_record_fail(remote)
        _log.warning("웹 로그인 실패: %s", remote)
        return render_template("login.html", error="비밀번호가 올바르지 않습니다."), 401

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
