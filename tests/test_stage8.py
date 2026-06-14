"""8단계: BotManager 검증 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from database.connection import configure, get_connection, close_connection
from database.models import create_tables
from database.order_repo import save_order
from database.state_repo import upsert_state


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
def base_cfg():
    return {
        "pairs": {
            "ETH": {
                "ticker": "KRW-ETH",
                "enabled": True,
                "capital": 2_500_000,
                "order_amount": 50_000,
                "max_rounds": 50,
                "trigger_pct": 0.5,
            },
            "USDT": {
                "ticker": "KRW-USDT",
                "enabled": True,
                "capital": 2_500_000,
                "order_amount": 50_000,
                "max_rounds": 50,
                "trigger_pct": 0.5,
            },
        },
        "strategy": {
            "trigger_min_pct": 0.3,
            "trigger_max_pct": 1.0,
            "sell_extra_pct": 0.08,
            "zone_normal_pct": 40,
            "zone_defensive_pct": 40,
            "zone_emergency_pct": 20,
            "circuit_breaker_pct": 5.0,
            "circuit_breaker_window": 300,
            "order_timeout": 300,
            "price_poll_interval": 1,
        },
    }


@pytest.fixture
def mock_exchange():
    ex = MagicMock()
    ex.get_balance.return_value = 5_000_000.0
    ex.cancel_order.return_value = True
    return ex


def _make_mock_bot(symbol: str, ticker: str) -> MagicMock:
    """BotManager에 주입할 Mock MagicSplit 봇."""
    bot = MagicMock()
    bot.is_running = False
    bot.current_round = 0
    bot.reference_price = 0.0
    bot.symbol = symbol
    bot.ticker = ticker

    def do_start():
        bot.is_running = True

    def do_stop():
        bot.is_running = False

    bot.start.side_effect = do_start
    bot.stop.side_effect  = do_stop
    return bot


@pytest.fixture
def manager(mock_exchange, base_cfg):
    """MagicSplit을 Mock으로 대체한 BotManager."""
    eth_bot  = _make_mock_bot("ETH",  "KRW-ETH")
    usdt_bot = _make_mock_bot("USDT", "KRW-USDT")

    call_order = iter([eth_bot, usdt_bot])

    with patch("bot_manager.MagicSplit", side_effect=lambda *a, **kw: next(call_order)):
        from bot_manager import BotManager
        mgr = BotManager(mock_exchange, base_cfg)
        mgr._test_eth  = eth_bot
        mgr._test_usdt = usdt_bot
        yield mgr


# ── 초기화 ────────────────────────────────────────────────────────────────────

def test_init_creates_bots_for_enabled_pairs(manager):
    assert "ETH"  in manager.symbols
    assert "USDT" in manager.symbols


def test_bots_not_running_on_init(manager):
    assert manager.is_running("ETH")  is False
    assert manager.is_running("USDT") is False


def test_any_running_false_on_init(manager):
    assert manager.any_running() is False


def test_disabled_pair_not_created(mock_exchange):
    cfg = {
        "pairs": {
            "ETH":  {"ticker": "KRW-ETH",  "enabled": True,  "capital": 100, "order_amount": 10, "max_rounds": 5, "trigger_pct": 0.5},
            "USDT": {"ticker": "KRW-USDT", "enabled": False, "capital": 100, "order_amount": 10, "max_rounds": 5, "trigger_pct": 0.5},
        },
        "strategy": {
            "trigger_min_pct": 0.3, "trigger_max_pct": 1.0,
            "sell_extra_pct": 0.08,
            "zone_normal_pct": 40, "zone_defensive_pct": 40, "zone_emergency_pct": 20,
            "circuit_breaker_pct": 5.0, "circuit_breaker_window": 300,
            "order_timeout": 300, "price_poll_interval": 1,
        },
    }
    eth_bot = _make_mock_bot("ETH", "KRW-ETH")
    with patch("bot_manager.MagicSplit", return_value=eth_bot):
        from bot_manager import BotManager
        mgr = BotManager(mock_exchange, cfg)
    assert "ETH"  in mgr.symbols
    assert "USDT" not in mgr.symbols


# ── start() ───────────────────────────────────────────────────────────────────

def test_start_returns_true_on_success(manager):
    ok, msg = manager.start("ETH")
    assert ok is True
    assert "ETH" in msg


def test_start_sets_running(manager):
    manager.start("ETH")
    assert manager.is_running("ETH") is True
    assert manager.any_running() is True


def test_start_calls_bot_start(manager):
    manager.start("ETH")
    manager._test_eth.start.assert_called_once()


def test_start_unknown_symbol(manager):
    ok, msg = manager.start("BTC")
    assert ok is False
    assert "BTC" in msg


def test_start_already_running(manager):
    manager.start("ETH")
    ok, msg = manager.start("ETH")
    assert ok is False
    assert "이미 실행 중" in msg


def test_start_does_not_start_other_bot(manager):
    manager.start("ETH")
    assert manager.is_running("USDT") is False


# ── stop() ────────────────────────────────────────────────────────────────────

def test_stop_returns_true_on_success(manager):
    manager.start("ETH")
    ok, msg = manager.stop("ETH")
    assert ok is True


def test_stop_sets_not_running(manager):
    manager.start("ETH")
    manager.stop("ETH")
    assert manager.is_running("ETH") is False


def test_stop_calls_bot_stop(manager):
    manager.start("ETH")
    manager.stop("ETH")
    manager._test_eth.stop.assert_called_once()


def test_stop_cancels_open_orders(manager, mock_exchange):
    save_order("ETH", "o1", "buy", 2_985_000, 0.01, 29850, 1)
    manager.start("ETH")
    manager.stop("ETH")
    mock_exchange.cancel_order.assert_called()


def test_stop_not_running(manager):
    ok, msg = manager.stop("ETH")
    assert ok is False
    assert "실행 중이 아닙니다" in msg


def test_stop_unknown_symbol(manager):
    ok, msg = manager.stop("XRP")
    assert ok is False


def test_stop_includes_cancelled_count_in_message(manager, mock_exchange):
    save_order("ETH", "o2", "buy", 2_985_000, 0.01, 29850, 1)
    save_order("ETH", "o3", "buy", 2_975_000, 0.01, 29750, 2)
    manager.start("ETH")
    ok, msg = manager.stop("ETH")
    assert ok is True
    assert "2건" in msg


# ── stop_all() ────────────────────────────────────────────────────────────────

def test_stop_all_stops_running_bots(manager):
    manager.start("ETH")
    manager.start("USDT")
    stopped = manager.stop_all()
    assert set(stopped) == {"ETH", "USDT"}
    assert manager.any_running() is False


def test_stop_all_skips_already_stopped(manager):
    manager.start("ETH")
    stopped = manager.stop_all()
    assert "ETH" in stopped
    assert "USDT" not in stopped


# ── get_status() ─────────────────────────────────────────────────────────────

def test_get_status_not_running(manager):
    status = manager.get_status("ETH")
    assert status["symbol"]  == "ETH"
    assert status["running"] is False
    assert status["ticker"]  == "KRW-ETH"


def test_get_status_running(manager):
    manager.start("ETH")
    status = manager.get_status("ETH")
    assert status["running"] is True


def test_get_status_unknown(manager):
    status = manager.get_status("UNKNOWN")
    assert status["running"] is False
    assert "error" in status


def test_get_all_status_returns_all_symbols(manager):
    statuses = manager.get_all_status()
    assert set(statuses.keys()) == {"ETH", "USDT"}


def test_bots_property_returns_copy(manager):
    bots = manager.bots
    assert "ETH"  in bots
    assert "USDT" in bots
    # 반환된 dict 수정이 내부 상태에 영향 없음
    bots.clear()
    assert len(manager.symbols) == 2
