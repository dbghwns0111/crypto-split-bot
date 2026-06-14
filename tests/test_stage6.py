"""6단계: 주문 모듈 검증 테스트 (인메모리 DB + Mock 거래소)."""

from unittest.mock import MagicMock

import pytest

from database.connection import configure, get_connection, close_connection
from database.models import create_tables
from database.order_repo import get_order, get_open_orders, save_order, update_order
from order.executor import InsufficientBalanceError


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
def mock_exchange():
    ex = MagicMock()
    ex.get_balance.return_value = 5_000_000.0
    ex.buy_limit.return_value  = {"order_id": "buy-001",  "status": "open", "price": 2_985_000.0, "quantity": 0.0167}
    ex.sell_limit.return_value = {"order_id": "sell-001", "status": "open", "price": 3_017_400.0, "quantity": 0.0167}
    ex.cancel_order.return_value = True
    ex.get_order_status.return_value = {
        "order_id": "buy-001", "status": "open",
        "price": 2_985_000.0, "quantity": 0.0167,
        "filled_price": None, "filled_quantity": 0,
    }
    return ex


# ══════════════════════════════════════════════════════════════════════════════
# executor.py
# ══════════════════════════════════════════════════════════════════════════════

def test_execute_buy_returns_order_dict(mock_exchange):
    from order.executor import execute_buy
    result = execute_buy("ETH", "KRW-ETH", mock_exchange,
                         reference_price=3_000_000, trigger_pct=0.5,
                         order_amount=50_000, round_no=1)
    assert result["order_id"] == "buy-001"
    assert result["side"] == "buy"
    assert result["status"] == "open"
    assert result["price"] > 0
    assert result["quantity"] > 0


def test_execute_buy_saves_to_db(mock_exchange):
    from order.executor import execute_buy
    execute_buy("ETH", "KRW-ETH", mock_exchange,
                reference_price=3_000_000, trigger_pct=0.5,
                order_amount=50_000, round_no=1)
    order = get_order("buy-001")
    assert order is not None
    assert order["symbol"] == "ETH"
    assert order["side"] == "buy"
    assert order["status"] == "open"
    assert order["round_no"] == 1


def test_execute_buy_calls_exchange(mock_exchange):
    from order.executor import execute_buy
    execute_buy("ETH", "KRW-ETH", mock_exchange,
                reference_price=3_000_000, trigger_pct=0.5,
                order_amount=50_000, round_no=1)
    mock_exchange.buy_limit.assert_called_once()
    args = mock_exchange.buy_limit.call_args
    assert args[0][0] == "KRW-ETH"


def test_execute_buy_insufficient_balance(mock_exchange):
    from order.executor import execute_buy
    mock_exchange.get_balance.return_value = 100.0  # 잔고 부족
    with pytest.raises(InsufficientBalanceError):
        execute_buy("ETH", "KRW-ETH", mock_exchange,
                    reference_price=3_000_000, trigger_pct=0.5,
                    order_amount=50_000, round_no=1)
    mock_exchange.buy_limit.assert_not_called()


def test_execute_sell_returns_order_dict(mock_exchange):
    from order.executor import execute_sell
    result = execute_sell("ETH", "KRW-ETH", mock_exchange,
                          reference_price=3_000_000, trigger_pct=0.5,
                          quantity=0.0167, round_no=1)
    assert result["order_id"] == "sell-001"
    assert result["side"] == "sell"
    assert result["status"] == "open"


def test_execute_sell_saves_to_db(mock_exchange):
    from order.executor import execute_sell
    execute_sell("ETH", "KRW-ETH", mock_exchange,
                 reference_price=3_000_000, trigger_pct=0.5,
                 quantity=0.0167, round_no=1)
    order = get_order("sell-001")
    assert order is not None
    assert order["side"] == "sell"


def test_execute_sell_insufficient_coin_balance(mock_exchange):
    from order.executor import execute_sell
    mock_exchange.get_balance.return_value = 0.001  # ETH 잔고 부족
    with pytest.raises(InsufficientBalanceError):
        execute_sell("ETH", "KRW-ETH", mock_exchange,
                     reference_price=3_000_000, trigger_pct=0.5,
                     quantity=0.0167, round_no=1)
    mock_exchange.sell_limit.assert_not_called()


def test_execute_sell_checks_coin_currency(mock_exchange):
    from order.executor import execute_sell
    execute_sell("USDT", "KRW-USDT", mock_exchange,
                 reference_price=1_350, trigger_pct=0.5,
                 quantity=100.0, round_no=1)
    # get_balance는 "USDT"로 호출되어야 함
    calls = [str(c) for c in mock_exchange.get_balance.call_args_list]
    assert any("USDT" in c for c in calls)


# ══════════════════════════════════════════════════════════════════════════════
# tracker.py
# ══════════════════════════════════════════════════════════════════════════════

def test_sync_open_orders_fills_filled(mock_exchange):
    from order.tracker import sync_open_orders
    save_order("ETH", "buy-t1", "buy", 2_985_000, 0.0167, 49850.0, 1)
    mock_exchange.get_order_status.return_value = {
        "order_id": "buy-t1", "status": "filled",
        "filled_price": 2_985_000.0, "filled_quantity": 0.0167,
        "price": 2_985_000.0, "quantity": 0.0167,
    }
    filled = sync_open_orders("ETH", mock_exchange)
    assert len(filled) == 1
    assert filled[0]["order_id"] == "buy-t1"
    assert get_order("buy-t1")["status"] == "filled"


def test_sync_open_orders_updates_cancelled(mock_exchange):
    from order.tracker import sync_open_orders
    save_order("ETH", "buy-t2", "buy", 2_985_000, 0.0167, 49850.0, 1)
    mock_exchange.get_order_status.return_value = {
        "order_id": "buy-t2", "status": "cancelled",
        "price": 2_985_000.0, "quantity": 0.0167,
        "filled_price": None, "filled_quantity": 0,
    }
    filled = sync_open_orders("ETH", mock_exchange)
    assert len(filled) == 0
    assert get_order("buy-t2")["status"] == "cancelled"


def test_sync_open_orders_skips_api_error(mock_exchange):
    from order.tracker import sync_open_orders
    save_order("ETH", "buy-t3", "buy", 2_985_000, 0.0167, 49850.0, 1)
    mock_exchange.get_order_status.side_effect = RuntimeError("API 오류")
    filled = sync_open_orders("ETH", mock_exchange)
    assert filled == []
    # DB는 변경되지 않아야 함
    assert get_order("buy-t3")["status"] == "open"


def test_check_order_returns_status(mock_exchange):
    from order.tracker import check_order
    save_order("ETH", "buy-c1", "buy", 2_985_000, 0.0167, 49850.0, 1)
    mock_exchange.get_order_status.return_value = {
        "order_id": "buy-c1", "status": "open",
        "price": 2_985_000.0, "quantity": 0.0167,
        "filled_price": None, "filled_quantity": 0,
    }
    result = check_order("buy-c1", mock_exchange)
    assert result["status"] == "open"


def test_check_order_updates_db_on_fill(mock_exchange):
    from order.tracker import check_order
    save_order("ETH", "buy-c2", "buy", 2_985_000, 0.0167, 49850.0, 1)
    mock_exchange.get_order_status.return_value = {
        "order_id": "buy-c2", "status": "filled",
        "filled_price": 2_985_000.0, "filled_quantity": 0.0167,
        "price": 2_985_000.0,
    }
    check_order("buy-c2", mock_exchange)
    assert get_order("buy-c2")["status"] == "filled"
    assert get_order("buy-c2")["filled_price"] == pytest.approx(2_985_000.0)


def test_check_order_unknown_on_exception(mock_exchange):
    from order.tracker import check_order
    mock_exchange.get_order_status.side_effect = RuntimeError("오류")
    result = check_order("missing-id", mock_exchange)
    assert result["status"] == "unknown"


def test_get_summary_counts(mock_exchange):
    from order.tracker import get_summary
    save_order("ETH", "b1", "buy",  2_985_000, 0.01, 29850.0, 1)
    save_order("ETH", "b2", "buy",  2_975_000, 0.01, 29750.0, 2)
    save_order("ETH", "s1", "sell", 3_017_400, 0.01, 30174.0, 1)
    summary = get_summary("ETH")
    assert summary["open_count"]      == 3
    assert summary["open_buy_count"]  == 2
    assert summary["open_sell_count"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# canceller.py
# ══════════════════════════════════════════════════════════════════════════════

def _make_old_order(symbol, oid, side, minutes_ago=10):
    """minutes_ago분 전에 생성된 주문을 DB에 삽입한다."""
    from utils.time_utils import now_kst
    from datetime import timedelta
    old_ts = (now_kst() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    conn.execute(
        """INSERT INTO orders
           (symbol, order_id, side, price, quantity, amount,
            status, round_no, created_at, updated_at)
           VALUES (?, ?, ?, 2985000, 0.0167, 49850, 'open', 1, ?, ?)""",
        (symbol, oid, side, old_ts, old_ts),
    )
    conn.commit()


def test_cancel_timed_out_cancels_old_orders(mock_exchange):
    from order.canceller import cancel_timed_out
    _make_old_order("ETH", "old-1", "buy", minutes_ago=10)
    cancelled = cancel_timed_out("ETH", mock_exchange, timeout_seconds=300)
    assert "old-1" in cancelled
    assert get_order("old-1")["status"] == "cancelled"
    mock_exchange.cancel_order.assert_called_once_with("old-1")


def test_cancel_timed_out_skips_recent_orders(mock_exchange):
    from order.canceller import cancel_timed_out
    _make_old_order("ETH", "new-1", "buy", minutes_ago=1)  # 1분 전 = 60초
    cancelled = cancel_timed_out("ETH", mock_exchange, timeout_seconds=300)
    assert "new-1" not in cancelled
    assert get_order("new-1")["status"] == "open"


def test_cancel_timed_out_api_failure_still_updates_db(mock_exchange):
    from order.canceller import cancel_timed_out
    _make_old_order("ETH", "err-1", "buy", minutes_ago=10)
    mock_exchange.cancel_order.side_effect = RuntimeError("API 오류")
    cancelled = cancel_timed_out("ETH", mock_exchange, timeout_seconds=300)
    assert "err-1" in cancelled
    assert get_order("err-1")["status"] == "cancelled"


def test_cancel_all_cancels_all_open(mock_exchange):
    from order.canceller import cancel_all
    save_order("ETH", "a1", "buy",  2_985_000, 0.01, 29850.0, 1)
    save_order("ETH", "a2", "buy",  2_975_000, 0.01, 29750.0, 2)
    save_order("ETH", "a3", "sell", 3_017_400, 0.01, 30174.0, 1)
    cancelled = cancel_all("ETH", mock_exchange)
    assert set(cancelled) == {"a1", "a2", "a3"}
    assert mock_exchange.cancel_order.call_count == 3
    assert all(get_order(oid)["status"] == "cancelled" for oid in cancelled)


def test_cancel_all_empty(mock_exchange):
    from order.canceller import cancel_all
    cancelled = cancel_all("ETH", mock_exchange)
    assert cancelled == []
    mock_exchange.cancel_order.assert_not_called()


def test_cancel_all_api_failure_fallback(mock_exchange):
    from order.canceller import cancel_all
    save_order("ETH", "fa1", "buy", 2_985_000, 0.01, 29850.0, 1)
    mock_exchange.cancel_order.side_effect = RuntimeError("API 실패")
    cancelled = cancel_all("ETH", mock_exchange)
    assert "fa1" in cancelled
    assert get_order("fa1")["status"] == "cancelled"


def test_cancel_all_symbol_isolation(mock_exchange):
    from order.canceller import cancel_all
    save_order("ETH",  "e1", "buy", 2_985_000, 0.01, 29850.0, 1)
    save_order("USDT", "u1", "buy",     1_350, 100,  135000.0, 1)
    cancel_all("ETH", mock_exchange)
    assert get_order("u1")["status"] == "open"
