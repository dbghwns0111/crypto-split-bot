"""HTML 페이지 라우트."""

import os

from flask import Blueprint, current_app, render_template, send_from_directory

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    bot_manager = current_app.config["BOT_MANAGER"]
    return render_template("dashboard.html", symbols=bot_manager.symbols)


@dashboard_bp.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(current_app.static_folder, "images"),
        "CryptoSplit.png",
        mimetype="image/png",
    )
