"""CryptoSplit 자동매매 봇 — 진입점.

실행 시 봇은 자동시작되지 않는다. 시작/중지는 웹 대시보드에서만 가능하다.
텔레그램 알림 스케줄러는 자동으로 시작된다.
"""

import argparse
import atexit
import logging
import os
import signal
import sys

from bot_manager import BotManager
from config.loader import get_config
from database.connection import backup_database, check_integrity, close_connection
from database.connection import configure as db_configure
from database.connection import get_connection
from database.models import create_tables
from exchange.bithumb_client import BithumbClient
from exchange.retry import set_notifier_fn
from notification.scheduler import NotificationScheduler
from notification.telegram import TelegramNotifier
from notification.telegram_handler import TelegramCommandHandler
from utils.logger import get_logger
from utils.single_instance import acquire_single_instance_lock
from utils.time_sync import sync_windows_time
from web.app import create_app

_log = get_logger("main")


def build_components(cfg: dict):
    """Exchange, BotManager, Notifier, Scheduler, Flask app, CommandHandler를 생성한다.

    봇은 시작하지 않는다 — 시작은 웹 대시보드 또는 텔레그램 명령어로만 가능하다.
    """
    access_key = os.environ.get("BITHUMB_ACCESS_KEY", "")
    secret_key = os.environ.get("BITHUMB_SECRET_KEY", "")
    exchange   = BithumbClient(access_key, secret_key)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID",   "")
    notifier  = TelegramNotifier(bot_token, chat_id)
    set_notifier_fn(notifier.send_time_sync_alert)

    bot_manager = BotManager(exchange, cfg, notifier)

    sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
    app   = create_app(bot_manager, exchange, cfg, sched, notifier)

    cmd_handler = None
    if cfg.get("telegram", {}).get("enabled", True) and bot_token and chat_id:
        cmd_handler = TelegramCommandHandler(
            bot_manager, exchange, cfg, bot_token, chat_id, scheduler=sched
        )

    return exchange, bot_manager, notifier, sched, app, cmd_handler


def graceful_shutdown(
    bot_manager: BotManager,
    sched: NotificationScheduler,
    cmd_handler: TelegramCommandHandler | None = None,
) -> None:
    """모든 봇 중지 → 스케줄러 중지 → 명령어 핸들러 중지 → DB 연결 해제."""
    _log.info("종료 처리 시작")

    try:
        stopped = bot_manager.stop_all()
        if stopped:
            _log.info("봇 중지: %s", stopped)
    except Exception as exc:
        _log.error("봇 중지 실패: %s", exc)

    try:
        sched.stop()
    except Exception as exc:
        _log.error("스케줄러 중지 실패: %s", exc)

    if cmd_handler:
        try:
            cmd_handler.stop()
        except Exception as exc:
            _log.error("명령어 핸들러 종료 실패: %s", exc)

    close_connection()
    _log.info("종료 완료")


class _AccessLogFilter(logging.Filter):
    """Werkzeug HTTP 접속 로그 전체 차단 (설정 변경은 앱 로거에서 별도 기록)."""
    def filter(self, record: logging.LogRecord) -> bool:
        return "HTTP/" not in record.getMessage()


def _suppress_poll_routes() -> None:
    logging.getLogger("werkzeug").addFilter(_AccessLogFilter())


def main() -> None:
    parser = argparse.ArgumentParser(description="CryptoSplit 자동매매 봇")
    parser.add_argument("--port",  type=int,            help="웹 서버 포트 (기본: settings.yaml)")
    parser.add_argument("--debug", action="store_true",  help="Flask 디버그 모드")
    args = parser.parse_args()

    cfg = get_config()

    # 단일 인스턴스 보장 — 중복 실행 시 즉시 종료
    # (중복 프로세스가 텔레그램을 동시 polling 하면 현황이 가동·중지 섞여 발송됨)
    if not acquire_single_instance_lock():
        msg = "이미 다른 CryptoSplit 인스턴스가 실행 중입니다. 중복 실행을 막기 위해 종료합니다."
        _log.error(msg)
        print(msg, file=sys.stderr)
        sys.exit(1)

    # DB 초기화
    db_path = cfg.get("database", {}).get("path", "data/cryptosplit.db")
    db_configure(db_path)
    conn = get_connection()
    create_tables(conn)

    # DB 무결성 검사 + 시작 시 백업 1회 (데이터 손실 예방)
    integrity_ok = check_integrity()
    if not integrity_ok:
        _log.error("DB 무결성 검사 실패 — 데이터 손상 가능, 백업 확인 필요")
    backup_keep = int(cfg.get("system", {}).get("db_backup_keep", 14))
    backup_database(keep=backup_keep)

    # 시작 시 시계 동기화 1회 실행 (JWT 만료 예방)
    sync_windows_time()

    # 컴포넌트 초기화 — 봇 자동시작 없음
    exchange, bot_manager, notifier, sched, app, cmd_handler = build_components(cfg)

    # 무결성 실패 시 텔레그램 경고 (notifier 준비 후)
    if not integrity_ok:
        try:
            notifier.send_db_backup_alert(False, "시작 시 DB 무결성 검사에 실패했습니다. 최근 백업으로 복구를 검토하세요.")
        except Exception:
            pass

    # 종료 핸들러
    def _on_exit() -> None:
        graceful_shutdown(bot_manager, sched, cmd_handler)

    atexit.register(_on_exit)

    def _signal_handler(sig, frame) -> None:  # noqa: ANN001
        _log.info("시그널 수신: %s", sig)
        _on_exit()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 텔레그램 스케줄러 + 명령어 핸들러 자동 시작
    sched.start()
    _log.info("알림 스케줄러 시작")
    if cmd_handler:
        cmd_handler.start()
        _log.info("텔레그램 명령어 핸들러 시작")

    # 웹 서버 실행 (메인 스레드 블록)
    web_cfg = cfg.get("web", {})
    host    = web_cfg.get("host", "0.0.0.0")
    port    = args.port or web_cfg.get("port", 5000)

    _log.info("웹 서버 시작: http://%s:%d", host, port)

    _suppress_poll_routes()
    app.run(host=host, port=port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
