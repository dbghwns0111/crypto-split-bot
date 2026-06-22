"""APScheduler 기반 정기 알림 — 매 정각 현황 + 매일 00시 결산."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from database.pnl_repo import get_daily_summary
from portfolio.balance import take_snapshot
from utils.logger import get_logger
from utils.time_utils import now_kst, today_kst_str

_log = get_logger("notification.scheduler")


class NotificationScheduler:
    """봇 가동 여부와 무관하게 항상 동작하는 정기 알림 스케줄러."""

    def __init__(self, notifier, bot_manager, exchange, cfg: dict):
        self._notifier    = notifier
        self._bot_manager = bot_manager
        self._exchange    = exchange
        self._cfg         = cfg
        self._scheduler   = BackgroundScheduler(timezone="Asia/Seoul")
        self._register_jobs()

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """스케줄러를 시작한다."""
        if not self._scheduler.running:
            self._scheduler.start()
            _log.info("알림 스케줄러 시작 (매 정각 현황 + 매일 00시 결산)")

    def stop(self) -> None:
        """스케줄러를 종료한다."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            _log.info("알림 스케줄러 종료")

    @property
    def is_running(self) -> bool:
        return self._scheduler.running

    # ── 잡 등록 ───────────────────────────────────────────────────────────────

    def _register_jobs(self) -> None:
        report = self._cfg.get("report", {})
        r_hour   = int(report.get("hour",   0))
        r_minute = int(report.get("minute", 0))

        self._scheduler.add_job(
            self._job_hourly_status,
            CronTrigger(minute=0),
            id="hourly_status",
            replace_existing=True,
            misfire_grace_time=300,  # 최대 5분까지 지연 허용
        )
        self._scheduler.add_job(
            self._job_daily_report,
            CronTrigger(hour=r_hour, minute=r_minute),
            id="daily_report",
            replace_existing=True,
            misfire_grace_time=300,
        )

    # ── 잡 콜백 ───────────────────────────────────────────────────────────────

    def _job_hourly_status(self) -> None:
        """매 정각: 가동 현황 메시지 전송."""
        try:
            hour = now_kst().hour
            statuses = self._collect_statuses()
            self._notifier.send_hourly_status(hour, statuses)
            _log.debug("정각 현황 전송: %02d:00", hour)
        except Exception as exc:
            _log.error("정각 현황 전송 실패: %s", exc)

    def _job_daily_report(self) -> None:
        """매일 00시: 일일 결산 리포트 전송 (전날 데이터 집계)."""
        try:
            from datetime import timedelta
            from database.order_repo import get_today_trade_counts
            date        = (now_kst() - timedelta(days=1)).strftime("%Y-%m-%d")
            symbol_data = get_daily_summary(date)
            for row in symbol_data:
                counts = get_today_trade_counts(row["symbol"], date)
                row["buy_count"]  = counts["buy"]
                row["sell_count"] = counts["sell"]
            total_krw, balances, prices = self._collect_balances()
            self._notifier.send_daily_report(date, symbol_data, total_krw, balances, prices)
            _log.info("일일 결산 리포트 전송: %s", date)
        except Exception as exc:
            _log.error("일일 결산 전송 실패: %s", exc)

    # ── 헬퍼 ──────────────────────────────────────────────────────────────────

    def _collect_statuses(self) -> list[dict]:
        """각 봇의 현재 상태를 수집한다. 심볼별 오류를 격리해 부분 실패를 허용한다."""
        from database.order_repo import get_today_trade_counts
        from database.pnl_repo import get_daily_pnl

        statuses = []
        for sym in self._bot_manager.symbols:
            try:
                bot_status = self._bot_manager.get_status(sym)
                entry: dict = {"symbol": sym, "running": bot_status["running"]}

                if bot_status["running"]:
                    ticker = self._cfg["pairs"][sym]["ticker"]
                    try:
                        entry["current_price"] = self._exchange.get_current_price(ticker)
                    except Exception:
                        entry["current_price"] = 0
                    entry["current_round"]     = bot_status.get("current_round", 0)
                    entry["reference_price"]   = bot_status.get("reference_price", 0)
                    entry["start_price"]       = bot_status.get("start_price", 0)
                    entry["pending_buy_price"] = bot_status.get("pending_buy_price", 0)
                    entry["pending_sell_price"] = bot_status.get("pending_sell_price", 0)
                    entry["max_rounds"]        = self._cfg["pairs"][sym].get("max_rounds", 50)
                    try:
                        pnl = get_daily_pnl(sym) or {}
                        entry["today_pnl"] = float(pnl.get("realized_pnl", 0))
                    except Exception:
                        entry["today_pnl"] = 0
                    try:
                        counts = get_today_trade_counts(sym)
                        entry["buy_count"]  = counts["buy"]
                        entry["sell_count"] = counts["sell"]
                    except Exception:
                        entry["buy_count"]  = 0
                        entry["sell_count"] = 0

                statuses.append(entry)
            except Exception as exc:
                _log.warning("현황 수집 실패 (%s): %s — 중지 상태로 표시", sym, exc)
                statuses.append({"symbol": sym, "running": False})
        return statuses

    def _collect_balances(self) -> tuple[float, dict[str, float], dict[str, float]]:
        """KRW 잔고, 코인 잔고, 심볼별 현재가를 수집한다."""
        try:
            snap      = take_snapshot(self._exchange, self._bot_manager.symbols)
            total_krw = float(snap.get("KRW", 0))
            balances  = {s: snap.get(s, 0) for s in self._bot_manager.symbols}
        except Exception as exc:
            _log.warning("잔고 조회 실패: %s", exc)
            total_krw, balances = 0.0, {}

        prices: dict[str, float] = {}
        for sym in self._bot_manager.symbols:
            try:
                ticker     = self._cfg["pairs"][sym]["ticker"]
                prices[sym] = self._exchange.get_current_price(ticker)
            except Exception:
                prices[sym] = 0.0

        return total_krw, balances, prices
