"""9단계: 텔레그램 알림 모듈 테스트."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from database.connection import close_connection, configure, get_connection
from database.models import create_tables
from database.pnl_repo import add_trade_pnl

from notification.message_builder import (
    ICON_BUY,
    ICON_RUNNING,
    ICON_SELL,
    ICON_STOPPED,
    build_api_error,
    build_buy_filled,
    build_daily_report,
    build_hourly_status,
    build_max_round,
    build_order_cancelled,
    build_sell_filled,
)
from notification.telegram import TelegramNotifier


# ── 공유 픽스처 ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    configure(":memory:")
    conn = get_connection()
    create_tables(conn)
    yield conn
    close_connection()
    configure(None)


@pytest.fixture
def notifier():
    return TelegramNotifier("fake_token", "12345")


@pytest.fixture
def disabled_notifier():
    return TelegramNotifier("", "")


# ═══════════════════════════════════════════════════════════════════════════════
# message_builder — 순수 함수
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildBuyFilled:
    def test_contains_symbol(self):
        msg = build_buy_filled("ETH", 3_245_000, 0.015, 3)
        assert "ETH" in msg

    def test_contains_price(self):
        msg = build_buy_filled("ETH", 3_245_000, 0.015, 3)
        assert "3,245,000" in msg

    def test_contains_quantity(self):
        msg = build_buy_filled("ETH", 3_245_000, 0.01500000, 3)
        assert "0.01500000" in msg

    def test_contains_round_no(self):
        msg = build_buy_filled("ETH", 3_245_000, 0.015, 3)
        assert "3차" in msg

    def test_emoji_present(self):
        # 매수는 웹 대시보드의 매수 트리거와 같은 빨강(.cs-negative)
        msg = build_buy_filled("ETH", 3_245_000, 0.015, 3)
        assert ICON_BUY in msg


class TestBuildSellFilled:
    def test_positive_pnl_has_plus_sign(self):
        msg = build_sell_filled("ETH", 3_300_000, 0.015, 2, net_pnl=5_000)
        assert "+5,000" in msg

    def test_negative_pnl_no_plus_sign(self):
        msg = build_sell_filled("ETH", 3_000_000, 0.015, 2, net_pnl=-1_000)
        assert "-1,000" in msg
        assert "+-1,000" not in msg

    def test_contains_symbol_and_price(self):
        msg = build_sell_filled("ETH", 3_300_000, 0.015, 2, net_pnl=0)
        assert "ETH" in msg
        assert "3,300,000" in msg

    def test_zero_pnl_has_plus_sign(self):
        msg = build_sell_filled("ETH", 3_000_000, 0.01, 1, net_pnl=0)
        assert "+0" in msg

    def test_emoji_present(self):
        # 매도는 웹 대시보드의 매도 트리거와 같은 초록(.cs-positive)
        msg = build_sell_filled("ETH", 3_000_000, 0.01, 1)
        assert ICON_SELL in msg


class TestBuildMaxRound:
    def test_contains_round_fraction(self):
        msg = build_max_round("ETH", 50, 50)
        assert "50/50" in msg

    def test_default_max_rounds(self):
        msg = build_max_round("USDT", 50)
        assert "50" in msg

    def test_contains_symbol(self):
        msg = build_max_round("ETH", 50)
        assert "ETH" in msg

    def test_emoji_present(self):
        msg = build_max_round("ETH", 50)
        assert "⚠️" in msg


class TestBuildOrderCancelled:
    def test_buy_side_korean(self):
        msg = build_order_cancelled("ETH", "abcdefghij", "buy", 3_000_000, 305)
        assert "매수" in msg

    def test_sell_side_korean(self):
        msg = build_order_cancelled("ETH", "abcdefghij", "ask", 3_000_000, 305)
        assert "매도" in msg

    def test_long_order_id_truncated(self):
        long_id = "a" * 36
        msg = build_order_cancelled("ETH", long_id, "buy", 3_000_000, 305)
        assert long_id not in msg
        assert "..." in msg

    def test_short_order_id_not_truncated(self):
        short_id = "abc123"
        msg = build_order_cancelled("ETH", short_id, "buy", 3_000_000, 305)
        assert "abc123" in msg
        assert "..." not in msg

    def test_elapsed_seconds(self):
        msg = build_order_cancelled("ETH", "x" * 10, "buy", 3_000_000, 305)
        assert "305" in msg

    def test_emoji_present(self):
        msg = build_order_cancelled("ETH", "oid", "buy", 3_000_000, 300)
        assert "⏰" in msg


class TestBuildApiError:
    def test_contains_symbol_and_error(self):
        msg = build_api_error("ETH", "Connection timeout")
        assert "ETH" in msg
        assert "Connection timeout" in msg

    def test_emoji_present(self):
        msg = build_api_error("ETH", "error")
        assert "🚨" in msg


class TestBuildHourlyStatus:
    def _make_running(self, symbol="ETH", price=3_245_000, rnd=3, pnl=12_400, trades=24):
        return {
            "symbol": symbol,
            "running": True,
            "current_price": price,
            "current_round": rnd,
            "today_pnl": pnl,
            "today_trades": trades,
        }

    def _make_stopped(self, symbol="ETH"):
        return {"symbol": symbol, "running": False}

    def test_all_running_uses_running_icon(self):
        statuses = [self._make_running("ETH"), self._make_running("USDT", 1_385, 1, 3_200, 8)]
        msg = build_hourly_status(10, statuses)
        assert ICON_RUNNING in msg

    def test_all_stopped_uses_stopped_icon(self):
        statuses = [self._make_stopped("ETH"), self._make_stopped("USDT")]
        msg = build_hourly_status(10, statuses)
        assert ICON_STOPPED in msg

    def test_any_running_gives_running_icon(self):
        statuses = [self._make_running("ETH"), self._make_stopped("USDT")]
        msg = build_hourly_status(10, statuses)
        assert ICON_RUNNING in msg

    def test_hour_formatted_two_digits(self):
        msg = build_hourly_status(9, [self._make_running()])
        assert "09:00" in msg

    def test_hour_in_header(self):
        msg = build_hourly_status(14, [self._make_running()])
        assert "14:00" in msg

    def test_running_symbol_shows_price(self):
        msg = build_hourly_status(10, [self._make_running("ETH", price=3_245_000)])
        assert "3,245,000" in msg

    def test_running_symbol_shows_round(self):
        msg = build_hourly_status(10, [self._make_running("ETH", rnd=5)])
        assert "5/50차" in msg

    def test_positive_pnl_has_plus(self):
        msg = build_hourly_status(10, [self._make_running(pnl=12_400)])
        assert "+12,400" in msg

    def test_negative_pnl_no_plus(self):
        msg = build_hourly_status(10, [self._make_running(pnl=-500)])
        assert "-500" in msg

    def test_stopped_shows_중지됨(self):
        msg = build_hourly_status(10, [self._make_stopped("ETH")])
        assert "중지됨" in msg

    def test_last_line_uses_end_bracket(self):
        statuses = [self._make_running("ETH"), self._make_running("USDT", 1_385, 1, 3_200, 8)]
        msg = build_hourly_status(10, statuses)
        lines = msg.splitlines()
        assert lines[-1].startswith("└")

    def test_non_last_line_uses_branch(self):
        statuses = [self._make_running("ETH"), self._make_running("USDT", 1_385, 1, 3_200, 8)]
        msg = build_hourly_status(10, statuses)
        lines = msg.splitlines()
        # 헤더 제외 첫 번째 서브라인은 ├ 로 시작
        assert lines[1].startswith("├")

    def test_empty_statuses(self):
        msg = build_hourly_status(10, [])
        assert ICON_STOPPED in msg   # 실행 중인 봇 없음
        assert "10:00" in msg


class TestBuildDailyReport:
    def _make_symbol_data(self, symbol="ETH", realized=38_200, trades=87, fee=4_350):
        return {"symbol": symbol, "realized_pnl": realized, "trade_count": trades, "fee_total": fee}

    def test_header_contains_date(self):
        msg = build_daily_report("2026-01-15", [], 0, {})
        assert "2026-01-15" in msg

    def test_contains_symbol_row(self):
        msg = build_daily_report("2026-01-15", [self._make_symbol_data()], 0, {})
        assert "ETH" in msg

    def test_realized_pnl_formatted(self):
        msg = build_daily_report("2026-01-15", [self._make_symbol_data(realized=38_200)], 0, {})
        assert "38,200" in msg

    def test_fee_displayed(self):
        msg = build_daily_report("2026-01-15", [self._make_symbol_data(fee=4_350)], 0, {})
        assert "4,350" in msg

    def test_grand_net_is_realized_pnl_as_is(self):
        # realized_pnl 은 pnl_repo 단계에서 이미 수수료가 차감된 순손익이다.
        # 여기서 수수료를 또 빼면 이중 차감 — 과거에 실제로 있었던 버그라
        # 이 테스트가 그 회귀를 막는다.
        data = self._make_symbol_data(realized=38_200, fee=4_350)
        msg = build_daily_report("2026-01-15", [data], 0, {})
        assert "38,200" in msg
        assert "33,850" not in msg

    def test_total_net_sums_multiple_symbols(self):
        eth  = self._make_symbol_data("ETH",  realized=38_200, fee=4_350)
        usdt = self._make_symbol_data("USDT", realized=12_400, fee=1_240)
        msg = build_daily_report("2026-01-15", [eth, usdt], 0, {})
        assert "50,600" in msg   # 38,200 + 12,400 (수수료 재차감 없음)

    def test_krw_balance(self):
        msg = build_daily_report("2026-01-15", [], 512_000, {})
        assert "512,000" in msg

    def test_coin_balances(self):
        msg = build_daily_report("2026-01-15", [], 0, {"ETH": 0.142, "USDT": 1850})
        assert "ETH" in msg
        assert "USDT" in msg

    def test_total_rule_present_for_multiple_symbols(self):
        # 합계선은 종목이 2개 이상일 때만 그린다 (1종목이면 합계 = 그 종목)
        msg = build_daily_report("2026-01-15", [
            self._make_symbol_data("ETH"), self._make_symbol_data("BTC"),
        ], 0, {})
        assert "─" in msg
        assert "합계" in msg

    def test_positive_grand_net_has_plus(self):
        data = self._make_symbol_data(realized=10_000, fee=1_000)
        msg = build_daily_report("2026-01-15", [data], 0, {})
        assert "+10,000" in msg

    def test_emoji_present(self):
        msg = build_daily_report("2026-01-15", [], 0, {})
        assert "📊" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# TelegramNotifier
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelegramNotifierDisabled:
    def test_disabled_returns_false(self, disabled_notifier):
        assert disabled_notifier.send("hello") is False

    def test_disabled_convenience_methods_return_false(self, disabled_notifier):
        assert disabled_notifier.send_buy_filled("ETH") is False
        assert disabled_notifier.send_api_error("ETH", "err") is False

    def test_disabled_does_not_call_requests(self, disabled_notifier):
        with patch("notification.telegram.requests.post") as mock_post:
            disabled_notifier.send("test")
            mock_post.assert_not_called()


class TestTelegramNotifierSend:
    def _ok_response(self):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    def _fail_response(self, code=400):
        resp = MagicMock()
        resp.status_code = code
        resp.text = "Bad Request"
        return resp

    def test_successful_send_returns_true(self, notifier):
        with patch("notification.telegram.requests.post", return_value=self._ok_response()):
            with patch("notification.telegram.time.sleep"):
                assert notifier.send("hello") is True

    def test_send_calls_correct_url(self, notifier):
        with patch("notification.telegram.requests.post", return_value=self._ok_response()) as mock_post:
            with patch("notification.telegram.time.sleep"):
                notifier.send("hi")
                url = mock_post.call_args[0][0]
                assert "fake_token" in url
                assert "sendMessage" in url

    def test_send_includes_chat_id(self, notifier):
        with patch("notification.telegram.requests.post", return_value=self._ok_response()) as mock_post:
            with patch("notification.telegram.time.sleep"):
                notifier.send("hi")
                payload = mock_post.call_args[1]["json"]
                assert payload["chat_id"] == "12345"

    def test_send_includes_text(self, notifier):
        with patch("notification.telegram.requests.post", return_value=self._ok_response()) as mock_post:
            with patch("notification.telegram.time.sleep"):
                notifier.send("hello world")
                payload = mock_post.call_args[1]["json"]
                assert payload["text"] == "hello world"

    def test_retry_on_http_error(self, notifier):
        responses = [self._fail_response(500), self._fail_response(500), self._ok_response()]
        with patch("notification.telegram.requests.post", side_effect=responses) as mock_post:
            with patch("notification.telegram.time.sleep"):
                result = notifier.send("test")
                assert result is True
                assert mock_post.call_count == 3

    def test_all_retries_fail_returns_false(self, notifier):
        with patch("notification.telegram.requests.post", return_value=self._fail_response()):
            with patch("notification.telegram.time.sleep"):
                assert notifier.send("fail") is False

    def test_retry_on_request_exception(self, notifier):
        responses = [
            requests.ConnectionError("timeout"),
            requests.ConnectionError("timeout"),
            self._ok_response(),
        ]
        with patch("notification.telegram.requests.post", side_effect=responses) as mock_post:
            with patch("notification.telegram.time.sleep"):
                result = notifier.send("test")
                assert result is True
                assert mock_post.call_count == 3

    def test_max_3_attempts(self, notifier):
        with patch("notification.telegram.requests.post", side_effect=requests.ConnectionError()) as mock_post:
            with patch("notification.telegram.time.sleep"):
                notifier.send("fail")
                assert mock_post.call_count == 3


class TestTelegramNotifierConvenience:
    def _ok_response(self):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    def test_send_buy_filled_calls_send(self, notifier):
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_buy_filled("ETH", price=3_000_000, qty=0.01, round_no=1)
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            assert "ETH" in text
            assert "3,000,000" in text

    def test_send_sell_filled_calls_send(self, notifier):
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_sell_filled("ETH", price=3_100_000, qty=0.01, round_no=0, net_pnl=5_000)
            text = mock_send.call_args[0][0]
            assert "+5,000" in text

    def test_send_max_round_calls_send(self, notifier):
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_max_round("ETH", round_no=50, max_rounds=50)
            text = mock_send.call_args[0][0]
            assert "50/50" in text

    def test_send_order_cancelled_calls_send(self, notifier):
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_order_cancelled("ETH", order_id="abcdefghij", side="buy", price=3_000_000, elapsed=305)
            text = mock_send.call_args[0][0]
            assert "매수" in text

    def test_send_api_error_calls_send(self, notifier):
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_api_error("ETH", error="timeout")
            text = mock_send.call_args[0][0]
            assert "timeout" in text

    def test_send_hourly_status_calls_send(self, notifier):
        statuses = [{"symbol": "ETH", "running": True, "current_price": 3_000_000,
                     "current_round": 2, "today_pnl": 1_000, "today_trades": 5}]
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_hourly_status(10, statuses)
            mock_send.assert_called_once()
            assert "ETH" in mock_send.call_args[0][0]

    def test_send_daily_report_calls_send(self, notifier):
        data = [{"symbol": "ETH", "realized_pnl": 10_000, "trade_count": 20, "fee_total": 1_000}]
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_daily_report("2026-01-15", data, 500_000, {"ETH": 0.1})
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            assert "2026-01-15" in text

    def test_send_buy_filled_extra_kwargs_ignored(self, notifier):
        with patch.object(notifier, "send", return_value=True):
            # extra kwargs should not raise
            notifier.send_buy_filled("ETH", price=3_000_000, qty=0.01, round_no=1, extra="ignored")

    def test_send_daily_report_none_balances(self, notifier):
        with patch.object(notifier, "send", return_value=True) as mock_send:
            notifier.send_daily_report("2026-01-15", [], 0, None)
            mock_send.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# NotificationScheduler
# ═══════════════════════════════════════════════════════════════════════════════

def _make_scheduler_deps(cfg=None):
    """NotificationScheduler 의존성 팩토리."""
    if cfg is None:
        cfg = {"pairs": {"ETH": {"ticker": "KRW-ETH"}, "USDT": {"ticker": "KRW-USDT"}},
               "report": {"hour": 0, "minute": 0}}
    notifier    = MagicMock()
    bot_manager = MagicMock()
    bot_manager.symbols = ["ETH", "USDT"]
    exchange    = MagicMock()
    return notifier, bot_manager, exchange, cfg


class TestNotificationScheduler:
    def test_start_and_stop(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)

        sched.start()
        assert sched.is_running is True

        sched.stop()
        assert sched.is_running is False

    def test_double_start_safe(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        sched.start()
        sched.start()   # 두 번째 start는 무시되어야 함
        sched.stop()

    def test_double_stop_safe(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        sched.start()
        sched.stop()
        sched.stop()    # 두 번째 stop은 무시되어야 함

    def test_jobs_registered(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        job_ids = [j.id for j in sched._scheduler.get_jobs()]
        assert "hourly_status" in job_ids
        assert "daily_report" in job_ids

    def test_daily_report_uses_config_hour(self):
        from notification.scheduler import NotificationScheduler
        cfg = {"pairs": {"ETH": {"ticker": "KRW-ETH"}},
               "report": {"hour": 1, "minute": 30}}
        notifier, bot_manager, exchange, _ = _make_scheduler_deps()
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        job = sched._scheduler.get_job("daily_report")
        # CronTrigger field 값 확인
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["hour"]   == "1"
        assert fields["minute"] == "30"


class TestSchedulerCallbacks:
    """실제 APScheduler를 사용하지 않고 콜백 메서드를 직접 테스트."""

    def _make_sched(self, cfg=None):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, base_cfg = _make_scheduler_deps(cfg)

        # bot_manager mock 세팅
        def fake_get_status(sym):
            return {"running": True, "current_round": 2, "ticker": f"KRW-{sym}"}
        bot_manager.get_status.side_effect = fake_get_status
        exchange.get_current_price.return_value = 3_000_000.0

        sched = NotificationScheduler(notifier, bot_manager, exchange, base_cfg)
        return sched, notifier, bot_manager

    def test_job_hourly_calls_send_hourly_status(self):
        sched, notifier, _ = self._make_sched()
        with patch("notification.scheduler.now_kst") as mock_now:
            mock_now.return_value.hour = 10
            sched._job_hourly_status()
        notifier.send_hourly_status.assert_called_once()
        hour, statuses = notifier.send_hourly_status.call_args[0]
        assert hour == 10
        assert len(statuses) == 2

    def test_job_hourly_status_structure(self):
        sched, notifier, _ = self._make_sched()
        with patch("notification.scheduler.now_kst") as mock_now:
            mock_now.return_value.hour = 14
            sched._job_hourly_status()
        _, statuses = notifier.send_hourly_status.call_args[0]
        symbols = {s["symbol"] for s in statuses}
        assert symbols == {"ETH", "USDT"}

    def test_job_hourly_stopped_bot(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        bot_manager.get_status.return_value = {"running": False}
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        with patch("notification.scheduler.now_kst") as mock_now:
            mock_now.return_value.hour = 10
            sched._job_hourly_status()
        _, statuses = notifier.send_hourly_status.call_args[0]
        assert all(not s["running"] for s in statuses)

    def test_job_hourly_price_error_graceful(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        bot_manager.get_status.return_value = {"running": True, "current_round": 1}
        exchange.get_current_price.side_effect = RuntimeError("API down")
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        with patch("notification.scheduler.now_kst") as mock_now:
            mock_now.return_value.hour = 10
            sched._job_hourly_status()   # should NOT raise
        notifier.send_hourly_status.assert_called_once()
        _, statuses = notifier.send_hourly_status.call_args[0]
        assert statuses[0]["current_price"] == 0

    def test_job_daily_calls_send_daily_report(self):
        sched, notifier, _ = self._make_sched()
        add_trade_pnl("ETH", 5_000, 500)
        with patch("notification.scheduler.today_kst_str", return_value="2026-01-15"):
            with patch("notification.scheduler.take_snapshot", return_value={"KRW": 500_000, "ETH": 0.1}):
                sched._job_daily_report()
        notifier.send_daily_report.assert_called_once()
        call_args = notifier.send_daily_report.call_args
        assert call_args[0][0] == "2026-01-15"   # date
        assert call_args[0][2] == 500_000        # total_krw

    def test_job_daily_balance_error_graceful(self):
        sched, notifier, _ = self._make_sched()
        with patch("notification.scheduler.today_kst_str", return_value="2026-01-15"):
            with patch("notification.scheduler.take_snapshot", side_effect=RuntimeError("fail")):
                sched._job_daily_report()   # should NOT raise
        notifier.send_daily_report.assert_called_once()
        call_args = notifier.send_daily_report.call_args
        assert call_args[0][2] == 0    # total_krw fallback

    def test_job_hourly_exception_does_not_propagate(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        notifier.send_hourly_status.side_effect = RuntimeError("boom")
        bot_manager.get_status.return_value = {"running": False}
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        with patch("notification.scheduler.now_kst") as mock_now:
            mock_now.return_value.hour = 10
            sched._job_hourly_status()   # should NOT raise

    def test_job_daily_exception_does_not_propagate(self):
        from notification.scheduler import NotificationScheduler
        notifier, bot_manager, exchange, cfg = _make_scheduler_deps()
        notifier.send_daily_report.side_effect = RuntimeError("boom")
        sched = NotificationScheduler(notifier, bot_manager, exchange, cfg)
        with patch("notification.scheduler.today_kst_str", return_value="2026-01-15"):
            with patch("notification.scheduler.take_snapshot", return_value={}):
                sched._job_daily_report()   # should NOT raise
