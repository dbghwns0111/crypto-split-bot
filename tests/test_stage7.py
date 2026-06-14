"""7단계: 포트폴리오 모듈 검증 테스트."""

from unittest.mock import MagicMock

import pytest

from database.connection import configure, get_connection, close_connection
from database.models import create_tables
from database.order_repo import save_order, update_order
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
                "capital": 2_500_000,
                "order_amount": 50_000,
                "max_rounds": 50,
                "trigger_pct": 0.5,
            }
        },
        "strategy": {
            "zone_normal_pct":    40,
            "zone_defensive_pct": 40,
            "zone_emergency_pct": 20,
        },
    }


@pytest.fixture
def mock_exchange():
    ex = MagicMock()
    ex.get_balance.return_value = 1_000_000.0
    ex.get_balances.return_value = [
        {"currency": "KRW",  "balance": 512_000.0, "locked": 0},
        {"currency": "ETH",  "balance": 0.142,     "locked": 0},
        {"currency": "USDT", "balance": 1_850.0,   "locked": 0},
    ]
    return ex


def _add_filled_buy(symbol, oid, fill_price, qty, round_no, filled_at="2024-01-01 10:00:00"):
    """체결된 매수 주문을 DB에 삽입하는 헬퍼."""
    save_order(symbol, oid, "buy", fill_price, qty, round(fill_price * qty, 2), round_no)
    update_order(oid, "filled", filled_price=fill_price, filled_at=filled_at)


# ══════════════════════════════════════════════════════════════════════════════
# balance.py
# ══════════════════════════════════════════════════════════════════════════════

def test_get_available_krw(mock_exchange):
    from portfolio.balance import get_available_krw
    mock_exchange.get_balance.return_value = 512_000.0
    assert get_available_krw(mock_exchange) == pytest.approx(512_000.0)
    mock_exchange.get_balance.assert_called_with("KRW")


def test_get_coin_balance(mock_exchange):
    from portfolio.balance import get_coin_balance
    mock_exchange.get_balance.return_value = 0.142
    bal = get_coin_balance(mock_exchange, "ETH")
    assert bal == pytest.approx(0.142)
    mock_exchange.get_balance.assert_called_with("ETH")


def test_get_all_balances(mock_exchange):
    from portfolio.balance import get_all_balances
    balances = get_all_balances(mock_exchange)
    assert balances["KRW"]  == pytest.approx(512_000.0)
    assert balances["ETH"]  == pytest.approx(0.142)
    assert balances["USDT"] == pytest.approx(1_850.0)


def test_get_all_balances_exchange_error(mock_exchange):
    from portfolio.balance import get_all_balances
    mock_exchange.get_balances.side_effect = RuntimeError("API 오류")
    result = get_all_balances(mock_exchange)
    assert result == {}


def test_check_buy_feasible_ok(mock_exchange):
    from portfolio.balance import check_buy_feasible
    mock_exchange.get_balance.return_value = 200_000.0
    ok, reason = check_buy_feasible(mock_exchange, 50_000, current_round=3, max_rounds=50)
    assert ok is True
    assert reason == "ok"


def test_check_buy_feasible_max_round(mock_exchange):
    from portfolio.balance import check_buy_feasible
    ok, reason = check_buy_feasible(mock_exchange, 50_000, current_round=50, max_rounds=50)
    assert ok is False
    assert "최대 차수" in reason


def test_check_buy_feasible_insufficient_krw(mock_exchange):
    from portfolio.balance import check_buy_feasible
    mock_exchange.get_balance.return_value = 10_000.0
    ok, reason = check_buy_feasible(mock_exchange, 50_000, current_round=3, max_rounds=50)
    assert ok is False
    assert "잔고 부족" in reason


def test_check_sell_feasible_ok(mock_exchange):
    from portfolio.balance import check_sell_feasible
    mock_exchange.get_balance.return_value = 0.5
    ok, reason = check_sell_feasible(mock_exchange, "ETH", 0.01)
    assert ok is True


def test_check_sell_feasible_insufficient(mock_exchange):
    from portfolio.balance import check_sell_feasible
    mock_exchange.get_balance.return_value = 0.001
    ok, reason = check_sell_feasible(mock_exchange, "ETH", 0.1)
    assert ok is False
    assert "잔고 부족" in reason


def test_get_zone_capital(base_cfg):
    from portfolio.balance import get_zone_capital
    zones = get_zone_capital(base_cfg, "ETH")
    assert zones["total"]     == pytest.approx(2_500_000)
    assert zones["normal"]    == pytest.approx(1_000_000)
    assert zones["defensive"] == pytest.approx(1_000_000)
    assert zones["emergency"] == pytest.approx(500_000)
    assert zones["normal"] + zones["defensive"] + zones["emergency"] == pytest.approx(zones["total"])


def test_take_snapshot(mock_exchange):
    from portfolio.balance import take_snapshot
    snap = take_snapshot(mock_exchange, ["ETH", "USDT"])
    assert snap["KRW"]  == pytest.approx(512_000.0)
    assert snap["ETH"]  == pytest.approx(0.142)
    assert snap["USDT"] == pytest.approx(1_850.0)
    assert "timestamp" in snap


# ══════════════════════════════════════════════════════════════════════════════
# position.py
# ══════════════════════════════════════════════════════════════════════════════

def test_get_active_position_none_before_init():
    from portfolio.position import get_active_position
    assert get_active_position("ETH") is None


def test_get_active_position_after_upsert():
    from portfolio.position import get_active_position
    upsert_state("ETH", reference_price=3_000_000, current_round=2, is_running=True)
    pos = get_active_position("ETH")
    assert pos is not None
    assert pos["current_round"]   == 2
    assert pos["reference_price"] == pytest.approx(3_000_000)
    assert pos["is_running"] is True


def test_get_active_buy_orders_empty_when_no_state():
    from portfolio.position import get_active_buy_orders
    assert get_active_buy_orders("ETH") == []


def test_get_active_buy_orders_respects_round_count():
    from portfolio.position import get_active_buy_orders
    upsert_state("ETH", current_round=2)
    _add_filled_buy("ETH", "b1", 3_000_000, 0.01, 1, "2024-01-01 09:00:00")
    _add_filled_buy("ETH", "b2", 2_990_000, 0.01, 2, "2024-01-01 10:00:00")
    _add_filled_buy("ETH", "b3", 2_980_000, 0.01, 3, "2024-01-01 11:00:00")  # 3번째 (제외)
    buys = get_active_buy_orders("ETH")
    assert len(buys) == 2  # current_round=2이므로 최근 2개만


def test_get_active_buy_orders_oldest_first():
    from portfolio.position import get_active_buy_orders
    upsert_state("ETH", current_round=2)
    _add_filled_buy("ETH", "b1", 3_000_000, 0.01, 1, "2024-01-01 09:00:00")
    _add_filled_buy("ETH", "b2", 2_990_000, 0.01, 2, "2024-01-01 10:00:00")
    buys = get_active_buy_orders("ETH")
    assert buys[0]["filled_at"] < buys[1]["filled_at"]  # oldest first


def test_get_total_quantity():
    from portfolio.position import get_total_quantity
    upsert_state("ETH", current_round=2)
    _add_filled_buy("ETH", "bq1", 3_000_000, 0.01,  1, "2024-01-01 09:00:00")
    _add_filled_buy("ETH", "bq2", 2_990_000, 0.015, 2, "2024-01-01 10:00:00")
    qty = get_total_quantity("ETH")
    assert qty == pytest.approx(0.025)


def test_get_total_cost():
    from portfolio.position import get_total_cost
    upsert_state("ETH", current_round=2)
    _add_filled_buy("ETH", "bc1", 3_000_000, 0.01, 1, "2024-01-01 09:00:00")
    _add_filled_buy("ETH", "bc2", 2_900_000, 0.01, 2, "2024-01-01 10:00:00")
    cost = get_total_cost("ETH")
    # 3_000_000 × 0.01 + 2_900_000 × 0.01 = 30_000 + 29_000 = 59_000
    assert cost == pytest.approx(59_000.0)


def test_get_breakeven_price():
    from portfolio.position import get_breakeven_price
    upsert_state("ETH", current_round=1)
    _add_filled_buy("ETH", "bb1", 3_000_000, 0.01, 1)
    be = get_breakeven_price("ETH")
    # 3_000_000 × (1 + 0.001) = 3_003_000
    assert be == pytest.approx(3_003_000.0)


def test_get_breakeven_price_zero_when_no_position():
    from portfolio.position import get_breakeven_price
    upsert_state("ETH", current_round=0)
    assert get_breakeven_price("ETH") == pytest.approx(0.0)


def test_get_unrealized_pnl_profit():
    from portfolio.position import get_unrealized_pnl
    upsert_state("ETH", current_round=1)
    _add_filled_buy("ETH", "up1", 3_000_000, 0.01, 1)
    pnl = get_unrealized_pnl("ETH", current_price=3_100_000)
    # (3_100_000 - 3_000_000) × 0.01 = 1_000
    assert pnl == pytest.approx(1_000.0)


def test_get_unrealized_pnl_loss():
    from portfolio.position import get_unrealized_pnl
    upsert_state("ETH", current_round=1)
    _add_filled_buy("ETH", "ul1", 3_000_000, 0.01, 1)
    pnl = get_unrealized_pnl("ETH", current_price=2_900_000)
    assert pnl == pytest.approx(-1_000.0)


def test_get_position_summary_keys():
    from portfolio.position import get_position_summary
    upsert_state("ETH", reference_price=3_000_000, current_round=2, is_running=True)
    _add_filled_buy("ETH", "ps1", 3_000_000, 0.01, 1, "2024-01-01 09:00:00")
    _add_filled_buy("ETH", "ps2", 2_990_000, 0.01, 2, "2024-01-01 10:00:00")
    summary = get_position_summary("ETH", current_price=3_010_000)
    for key in ("symbol", "current_round", "reference_price", "current_price",
                "total_quantity", "total_cost", "current_value",
                "unrealized_pnl", "avg_buy_price", "breakeven_price",
                "is_running", "is_profitable"):
        assert key in summary, f"키 누락: {key}"


def test_get_position_summary_profitable_flag():
    from portfolio.position import get_position_summary
    upsert_state("ETH", current_round=1)
    _add_filled_buy("ETH", "pp1", 3_000_000, 0.01, 1)
    # breakeven ≈ 3_003_000
    above = get_position_summary("ETH", current_price=3_010_000)
    below = get_position_summary("ETH", current_price=2_990_000)
    assert above["is_profitable"] is True
    assert below["is_profitable"] is False


def test_get_round_details():
    from portfolio.position import get_round_details
    upsert_state("ETH", current_round=2)
    _add_filled_buy("ETH", "rd1", 3_000_000, 0.01, 1, "2024-01-01 09:00:00")
    _add_filled_buy("ETH", "rd2", 2_990_000, 0.01, 2, "2024-01-01 10:00:00")
    details = get_round_details("ETH")
    assert len(details) == 2
    assert details[0]["round_no"] == 1
    assert details[1]["round_no"] == 2
    assert details[0]["buy_price"] == pytest.approx(3_000_000.0)


def test_get_round_details_empty():
    from portfolio.position import get_round_details
    upsert_state("ETH", current_round=0)
    assert get_round_details("ETH") == []
