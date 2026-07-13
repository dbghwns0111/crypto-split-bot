"""Flask 앱 팩토리."""

import os

from flask import Flask, send_from_directory

_HERE        = os.path.dirname(os.path.abspath(__file__))
_IMAGES_DIR  = os.path.join(_HERE, "..", "images")


def create_app(bot_manager, exchange=None, cfg=None, scheduler=None):
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
