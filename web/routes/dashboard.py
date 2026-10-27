"""HTML 페이지 라우트."""

import os

from flask import Blueprint, abort, current_app, render_template, send_from_directory

dashboard_bp = Blueprint("dashboard", __name__)

# 손익 탭 서브탭 — dashboard.js의 TRADES_TABS와 일치시킬 것
_TRADES_TABS = ("summary", "fills", "pnl", "assets")


# 탭마다 주소를 갖지만 화면은 한 페이지(SPA)다 — 어느 경로로 들어와도 같은 HTML을 주고,
# dashboard.js가 주소를 읽어 해당 탭을 연다. 탭 전환은 history.pushState로 주소만 바꾼다.
@dashboard_bp.route("/")
@dashboard_bp.route("/home")
@dashboard_bp.route("/strategy")
@dashboard_bp.route("/trades")
@dashboard_bp.route("/trades/<sub>")
@dashboard_bp.route("/settings")
def index(sub: str | None = None):
    if sub is not None and sub not in _TRADES_TABS:
        abort(404)
    bot_manager = current_app.config["BOT_MANAGER"]
    return render_template("dashboard.html", symbols=bot_manager.symbols)


@dashboard_bp.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(current_app.static_folder, "images"),
        "CryptoSplit.png",
        mimetype="image/png",
    )
