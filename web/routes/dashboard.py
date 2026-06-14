"""HTML 페이지 라우트."""

from flask import Blueprint, current_app, render_template

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    bot_manager = current_app.config["BOT_MANAGER"]
    return render_template("dashboard.html", symbols=bot_manager.symbols)
