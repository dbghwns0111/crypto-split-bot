"""Flask 앱 팩토리."""

import gzip
import os

from flask import Flask, request, send_from_directory

_HERE        = os.path.dirname(os.path.abspath(__file__))
_IMAGES_DIR  = os.path.join(_HERE, "..", "images")

# 응답 압축 대상 — API JSON과 페이지 HTML. 정적 파일(CSS/JS)은 버전 쿼리 + 30일 캐시라
# 다시 받을 일이 거의 없어서 제외한다(파일 응답은 스트리밍이라 압축하려면 통째로 읽어야 함).
_GZIP_MIMETYPES = ("application/json", "text/html")
_GZIP_MIN_BYTES = 1024   # 이보다 작으면 압축 이득보다 헤더·CPU 비용이 큼


def _gzip(response):
    """브라우저가 gzip을 받을 수 있으면 응답 본문을 압축한다.

    실현손익 전체기간처럼 수만 건짜리 JSON(수 MB)을 ZeroTier 경유로 폰에 내려보낼 때
    전송 시간이 병목이었다. 반복 구조의 JSON은 보통 1/10 안팎으로 줄어든다.
    브라우저가 자동으로 풀어주므로 프론트 코드는 그대로다.
    """
    if (response.status_code != 200
            or response.direct_passthrough
            or response.is_streamed
            or "Content-Encoding" in response.headers
            or response.mimetype not in _GZIP_MIMETYPES
            or "gzip" not in request.headers.get("Accept-Encoding", "").lower()):
        return response
    data = response.get_data()
    if len(data) < _GZIP_MIN_BYTES:
        return response
    response.set_data(gzip.compress(data, compresslevel=5))
    response.headers["Content-Encoding"] = "gzip"
    response.vary.add("Accept-Encoding")
    return response


def create_app(bot_manager, exchange=None, cfg=None, scheduler=None, notifier=None):
    """Flask 앱을 생성하고 blueprint를 등록한다."""
    app = Flask(
        __name__,
        template_folder=os.path.join(_HERE, "templates"),
        static_folder=os.path.join(_HERE, "static"),
    )
    app.config["BOT_MANAGER"] = bot_manager
    app.config["EXCHANGE"]    = exchange
    app.config["CFG"]         = cfg or {}
    app.config["SCHEDULER"]   = scheduler
    app.config["NOTIFIER"]    = notifier

    # 정적 파일 장기 캐시 + 파일 수정시각 버전 쿼리(?v=) — 기본값에선 페이지를 열 때마다
    # CSS/JS/아이콘마다 "바뀌었나?" 확인 요청이 VPN을 건너 나갔다. 파일이 바뀌면 URL이
    # 달라지므로 git pull 후 새 버전은 그대로 즉시 반영된다.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30

    @app.url_defaults
    def _static_version(endpoint, values):
        if endpoint != "static" or "v" in values:
            return
        filename = values.get("filename")
        if not filename:
            return
        try:
            values["v"] = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            pass

    @app.after_request
    def _gzip_response(response):
        return _gzip(response)

    @app.route("/images/<path:filename>")
    def serve_project_image(filename):
        return send_from_directory(_IMAGES_DIR, filename)

    from web.routes.api       import api_bp
    from web.routes.dashboard import dashboard_bp
    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_bp)

    # 접근 보안 (세션 비밀번호 로그인 + IP 화이트리스트)
    from web.auth import init_auth
    init_auth(app, cfg or {})

    return app
