"""APScheduler 기반 정기 알림 — 매 정각 현황 + 매일 00시 결산."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from database.pnl_repo import get_daily_summary
from portfolio.balance import take_snapshot
from utils.logger import get_logger
from utils.time_utils import now_kst, today_kst_str

_log = get_logger("notification.scheduler")


def _hourly_status_hours(interval_hours: int) -> str:
    """0시를 제외한 N시간 간격 정각 목록을 콤마 문자열로 반환한다(예: 4 → '4,8,12,16,20').

    00시 정각 현황은 같은 시각에 오는 00시 결산 리포트와 중복돼 굳이 따로 필요 없다.
    """
    hours = [h for h in range(0, 24, interval_hours) if h != 0]
    return ",".join(str(h) for h in hours)


class NotificationScheduler:
    """봇 가동 여부와 무관하게 항상 동작하는 정기 알림 스케줄러."""

    def __init__(self, notifier, bot_manager, exchange, cfg: dict):
        self._notifier    = notifier
        self._bot_manager = bot_manager
        self._exchange    = exchange
        self._cfg         = cfg
        # coalesce=True: 밀린 실행을 1회로 합침, max_instances=1: 동시 중복 실행 차단
        # (NTP 시계 보정 등으로 동일 잡이 중복 발사되어 현황이 두 번 오는 것을 방지)
        self._scheduler   = BackgroundScheduler(
            timezone="Asia/Seoul",
            job_defaults={"coalesce": True, "max_instances": 1},
        )
        self._register_jobs()

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """스케줄러를 시작한다."""
        if not self._scheduler.running:
            self._scheduler.start()
            # 오늘 스냅샷이 없을 수 있으므로(신규 도입/재시작) 23:59까지 기다리지 않고 1회 즉시 기록
            self._job_asset_snapshot()
            _log.info("알림 스케줄러 시작 (매 정각 현황 + 매일 00시 결산)")

    def stop(self) -> None:
        """스케줄러를 종료한다."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            _log.info("알림 스케줄러 종료")

    @property
    def is_running(self) -> bool:
        return self._scheduler.running

    # ── 설정 탭에서 재시작 없이 반영 ──────────────────────────────────────────────

    def reschedule_hourly_status(self, interval_hours: int) -> None:
        """정각 현황 알림 발송 간격을 재설정한다 (봇 재시작 불필요)."""
        interval_hours = max(1, min(23, int(interval_hours)))
        self._scheduler.reschedule_job(
            "hourly_status", trigger=CronTrigger(hour=_hourly_status_hours(interval_hours), minute=0)
        )
        # reschedule_job은 next_run_time을 새로 계산해 넣으면서 일시정지 상태를 풀어버리므로,
        # 알림이 꺼져 있었다면(hourly_enabled=false) 다시 일시정지 상태로 되돌린다.
        if not self._cfg.get("report", {}).get("hourly_enabled", True):
            self._scheduler.pause_job("hourly_status")
        _log.info("정각 현황 알림 간격 변경: %d시간", interval_hours)

    def set_hourly_status_enabled(self, enabled: bool) -> None:
        """정각 현황 알림 사용 여부를 즉시 반영한다 (봇 재시작 불필요)."""
        if enabled:
            self._scheduler.resume_job("hourly_status")
        else:
            self._scheduler.pause_job("hourly_status")
        _log.info("정각 현황 알림 %s", "사용" if enabled else "중지")

    def reschedule_time_sync(self, interval_hours: int) -> None:
        """Windows 시계 동기화 간격을 재설정한다 (봇 재시작 불필요)."""
        interval_hours = max(1, min(23, int(interval_hours)))
        self._scheduler.reschedule_job(
            "time_sync", trigger=CronTrigger(hour=f"*/{interval_hours}")
        )
        _log.info("시계 동기화 간격 변경: %d시간", interval_hours)

    # ── 잡 등록 ───────────────────────────────────────────────────────────────

    def _register_jobs(self) -> None:
        report = self._cfg.get("report", {})
        r_hour   = int(report.get("hour",   0))
        r_minute = int(report.get("minute", 0))

        hourly_interval = max(1, min(23, int(report.get("hourly_interval_hours", 1))))
        self._scheduler.add_job(
            self._job_hourly_status,
            CronTrigger(hour=_hourly_status_hours(hourly_interval), minute=0),
            id="hourly_status",
            replace_existing=True,
            misfire_grace_time=300,  # 최대 5분까지 지연 허용
        )
        if not report.get("hourly_enabled", True):
            self._scheduler.pause_job("hourly_status")
        self._scheduler.add_job(
            self._job_daily_report,
            CronTrigger(hour=r_hour, minute=r_minute),
            id="daily_report",
            replace_existing=True,
            misfire_grace_time=300,
        )

        sync_interval = int(
            self._cfg.get("system", {}).get("time_sync_interval_hours", 6)
        )
        self._scheduler.add_job(
            self._job_time_sync,
            CronTrigger(hour=f"*/{sync_interval}"),
            id="time_sync",
            replace_existing=True,
            misfire_grace_time=300,
        )

        backup_interval = int(
            self._cfg.get("system", {}).get("db_backup_interval_hours", 6)
        )
        self._scheduler.add_job(
            self._job_db_backup,
            CronTrigger(hour=f"*/{backup_interval}"),
            id="db_backup",
            replace_existing=True,
            misfire_grace_time=300,
        )

        watchdog_interval = int(
            self._cfg.get("system", {}).get("watchdog_interval_min", 3)
        )
        self._scheduler.add_job(
            self._job_watchdog,
            CronTrigger(minute=f"*/{watchdog_interval}"),
            id="watchdog",
            replace_existing=True,
            misfire_grace_time=60,
        )

        self._scheduler.add_job(
            self._job_asset_snapshot,
            CronTrigger(hour=23, minute=59),
            id="asset_snapshot",
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

    def _job_time_sync(self) -> None:
        """주기적 Windows 시간 동기화 — 실패했을 때만 텔레그램으로 알림.

        성공 알림까지 매번 보내면 알림이 쌓여 정작 중요한 경고가 묻힌다.
        API 타임스탬프는 utils/ntp_clock 이 OS 시계와 무관하게 따로 보정하므로,
        이 잡의 성공 여부는 DB 기록 시각 정확도에만 영향을 준다.
        """
        try:
            from utils.time_sync import sync_windows_time
            ok = sync_windows_time()
            if not ok:
                self._notifier.send_time_sync_alert(False)
        except Exception as exc:
            _log.error("주기적 시간 동기화 실패: %s", exc)

    def _job_db_backup(self) -> None:
        """주기적 DB 백업 — 실패 시에만 텔레그램 알림 (성공은 무알림)."""
        try:
            from database.connection import backup_database
            keep = int(self._cfg.get("system", {}).get("db_backup_keep", 14))
            path = backup_database(keep=keep)
            if path is None:
                self._notifier.send_db_backup_alert(False, "주기적 DB 백업에 실패했습니다.")
            else:
                _log.debug("주기적 DB 백업 완료: %s", path)
        except Exception as exc:
            _log.error("주기적 DB 백업 실패: %s", exc)
            try:
                self._notifier.send_db_backup_alert(False, f"DB 백업 오류: {exc}")
            except Exception:
                pass

    def _job_asset_snapshot(self) -> None:
        """매일 23:59: 보유자산(KRW+코인 평가금액) 스냅샷 저장 — 원금 변동 추이 추적용."""
        try:
            from database.asset_repo import save_snapshot

            total_krw, balances, prices = self._collect_balances()
            holdings = {
                sym: {
                    "qty":   balances.get(sym, 0),
                    "price": prices.get(sym, 0),
                    "value": balances.get(sym, 0) * prices.get(sym, 0),
                }
                for sym in self._bot_manager.symbols
            }
            total_assets = total_krw + sum(h["value"] for h in holdings.values())
            save_snapshot(total_assets, total_krw, holdings)
            _log.info("보유자산 스냅샷 저장: %.0f원", total_assets)
        except Exception as exc:
            _log.error("보유자산 스냅샷 실패: %s", exc)

    def _job_watchdog(self) -> None:
        """주기적 봇 헬스 점검 — 죽은 스레드 자동 복구 + 텔레그램 알림."""
        try:
            results = self._bot_manager.check_and_recover_health()
            for r in results:
                self._notifier.send_watchdog_alert(r["symbol"], r["recovered"])
        except Exception as exc:
            _log.error("워치독 점검 실패: %s", exc)

    def _job_daily_report(self) -> None:
        """매일 00시: 일일 결산 리포트 전송 (전날 데이터 집계)."""
        from datetime import timedelta
        date = (now_kst() - timedelta(days=1)).strftime("%Y-%m-%d")
        self._send_report(date)

    def report_now(self) -> bool:
        """오늘 날짜 기준 결산 리포트를 즉시 전송한다 (/report 명령용)."""
        date = now_kst().strftime("%Y-%m-%d")
        return self._send_report(date)

    def _send_report(self, date: str) -> bool:
        """지정 날짜의 결산 리포트를 집계해 전송한다."""
        try:
            from database.order_repo import get_today_trade_counts
            symbol_data = get_daily_summary(date)
            for row in symbol_data:
                counts = get_today_trade_counts(row["symbol"], date)
                row["buy_count"]  = counts["buy"]
                row["sell_count"] = counts["sell"]
            total_krw, balances, prices = self._collect_balances()
            self._notifier.send_daily_report(date, symbol_data, total_krw, balances, prices)
            _log.info("결산 리포트 전송: %s", date)
            return True
        except Exception as exc:
            _log.error("결산 전송 실패 (%s): %s", date, exc)
            return False

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
