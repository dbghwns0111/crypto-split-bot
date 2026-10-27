"""Flask 앱 팩토리."""

import os

from flask import Flask, send_from_directory

_HERE        = os.path.dirname(os.path.abspath(__file__))
_IMAGES_DIR  = os.path.join(_HERE, "..", "images")


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
