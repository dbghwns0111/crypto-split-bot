"""3단계: DB 모듈 검증 테스트 (인메모리 SQLite)."""

import sqlite3
import pytest

from database.connection import configure, get_connection, close_connection
from database.models import create_tables


# ── 공유 픽스처 ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    """매 테스트마다 인메모리 DB를 새로 생성한다."""
    configure(":memory:")
    conn = get_connection()
    create_tables(conn)
    yield conn
    close_connection()
    configure(None)


# ── connection / models ───────────────────────────────────────────────────────

def test_get_connection_returns_connection(fresh_db):
    assert isinstance(fresh_db, sqlite3.Connection)


def test_tables_created(fresh_db):
    tables = {r[0] for r in fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"orders", "bot_state", "daily_pnl"}.issubset(tables)


def test_indexes_created(fresh_db):
    indexes = {r[0] for r in fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    assert "idx_orders_symbol_status" in indexes
    assert "idx_daily_pnl_date" in indexes


# ── order_repo ────────────────────────────────────────────────────────────────

def test_save_order_returns_id():
    from database.order_repo import save_order
    row_id = save_order("ETH", "ORD001", "buy", 3_000_000, 0.01, 30_000, 1)
    assert row_id == 1


def test_get_order_after_save():
    from database.order_repo import save_order, get_order
    save_order("ETH", "ORD002", "buy", 3_000_000, 0.01, 30_000, 1)
    order = get_order("ORD002")
    assert order is not None
    assert order["symbol"] == "ETH"
    assert order["side"] == "buy"
    assert order["status"] == "open"
    assert order["round_no"] == 1


def test_get_order_not_found():
    from database.order_repo import get_order
    assert get_order("NONEXISTENT") is None


def test_update_order_status():
    from database.order_repo import save_order, update_order, get_order
    save_order("ETH", "ORD003", "buy", 3_000_000, 0.01, 30_000, 1)
    update_order("ORD003", "filled", filled_price=2_990_000, filled_at="2024-01-01 10:00:00")
    order = get_order("ORD003")
    assert order["status"] == "filled"
    assert order["filled_price"] == pytest.approx(2_990_000)


def test_get_open_orders():
    from database.order_repo import save_order, update_order, get_open_orders
    save_order("ETH", "ORD004", "buy", 3_000_000, 0.01, 30_000, 1)
    save_order("ETH", "ORD005", "buy", 2_990_000, 0.01, 30_000, 2)
    update_order("ORD004", "filled", 2_990_000, "2024-01-01 10:00:01")
    open_orders = get_open_orders("ETH")
    assert len(open_orders) == 1
    assert open_orders[0]["order_id"] == "ORD005"


def test_get_open_orders_empty():
    from database.order_repo import get_open_orders
    assert get_open_orders("ETH") == []


def test_get_active_buy_count():
    from database.order_repo import save_order, update_order, get_active_buy_count
    save_order("ETH", "B001", "buy", 3_000_000, 0.01, 30_000, 1)
    save_order("ETH", "B002", "buy", 2_990_000, 0.01, 30_000, 2)
    update_order("B001", "filled", 3_000_000, "2024-01-01 10:00:00")
    update_order("B002", "filled", 2_990_000, "2024-01-01 10:00:01")
    assert get_active_buy_count("ETH") == 2


def test_get_latest_filled_buy():
    from database.order_repo import save_order, update_order, get_latest_filled_buy
    save_order("ETH", "B010", "buy", 3_000_000, 0.01, 30_000, 1)
    save_order("ETH", "B011", "buy", 2_990_000, 0.01, 30_000, 2)
    update_order("B010", "filled", 3_000_000, "2024-01-01 09:00:00")
    update_order("B011", "filled", 2_990_000, "2024-01-01 10:00:00")
    latest = get_latest_filled_buy("ETH")
    assert latest["order_id"] == "B011"


def test_get_orders_symbol_isolation():
    from database.order_repo import save_order, get_open_orders
    save_order("ETH",  "E001", "buy", 3_000_000, 0.01, 30_000, 1)
    save_order("USDT", "U001", "buy", 1_350,     10,    13_500, 1)
    assert len(get_open_orders("ETH"))  == 1
    assert len(get_open_orders("USDT")) == 1


# ── state_repo ────────────────────────────────────────────────────────────────

def test_upsert_state_insert():
    from database.state_repo import upsert_state, get_state
    upsert_state("ETH", reference_price=3_000_000, current_round=2, is_running=True)
    state = get_state("ETH")
    assert state is not None
    assert state["reference_price"] == pytest.approx(3_000_000)
    assert state["current_round"] == 2
    assert state["is_running"] == 1


def test_upsert_state_update():
    from database.state_repo import upsert_state, get_state
    upsert_state("ETH", reference_price=3_000_000)
    upsert_state("ETH", reference_price=3_100_000, current_round=3)
    state = get_state("ETH")
    assert state["reference_price"] == pytest.approx(3_100_000)
    assert state["current_round"] == 3


def test_get_state_none():
    from database.state_repo import get_state
    assert get_state("NONEXISTENT") is None


def test_set_reference_price():
    from database.state_repo import upsert_state, set_reference_price, get_state
    upsert_state("ETH", reference_price=3_000_000)
    set_reference_price("ETH", 3_050_000)
    assert get_state("ETH")["reference_price"] == pytest.approx(3_050_000)


def test_set_running():
    from database.state_repo import upsert_state, set_running, get_state
    upsert_state("ETH", is_running=False)
    set_running("ETH", True)
    assert get_state("ETH")["is_running"] == 1


def test_set_circuit_breaker():
    from database.state_repo import upsert_state, set_circuit_breaker, get_state
    upsert_state("ETH")
    set_circuit_breaker("ETH", True, "2024-01-01 10:05:00")
    state = get_state("ETH")
    assert state["circuit_breaker_on"] == 1
    assert state["cb_until"] == "2024-01-01 10:05:00"


def test_increment_round():
    from database.state_repo import upsert_state, increment_round
    upsert_state("ETH", current_round=2)
    new_round = increment_round("ETH")
    assert new_round == 3


def test_decrement_round():
    from database.state_repo import upsert_state, decrement_round
    upsert_state("ETH", current_round=3)
    new_round = decrement_round("ETH")
    assert new_round == 2


def test_decrement_round_floor_zero():
    from database.state_repo import upsert_state, decrement_round
    upsert_state("ETH", current_round=0)
    new_round = decrement_round("ETH")
    assert new_round == 0


# ── pnl_repo ──────────────────────────────────────────────────────────────────

def test_add_trade_pnl_insert():
    from database.pnl_repo import add_trade_pnl, get_daily_pnl
    add_trade_pnl("ETH", 1000.0, 50.0, "2024-01-01")
    pnl = get_daily_pnl("ETH", "2024-01-01")
    assert pnl is not None
    assert pnl["realized_pnl"] == pytest.approx(1000.0)
    assert pnl["trade_count"] == 1
    assert pnl["fee_total"] == pytest.approx(50.0)


def test_add_trade_pnl_accumulate():
    from database.pnl_repo import add_trade_pnl, get_daily_pnl
    add_trade_pnl("ETH", 1000.0, 50.0, "2024-01-01")
    add_trade_pnl("ETH",  500.0, 25.0, "2024-01-01")
    pnl = get_daily_pnl("ETH", "2024-01-01")
    assert pnl["realized_pnl"] == pytest.approx(1500.0)
    assert pnl["trade_count"] == 2
    assert pnl["fee_total"] == pytest.approx(75.0)


def test_get_daily_pnl_not_found():
    from database.pnl_repo import get_daily_pnl
    assert get_daily_pnl("ETH", "1999-01-01") is None


def test_get_daily_summary():
    from database.pnl_repo import add_trade_pnl, get_daily_summary
    add_trade_pnl("ETH",  2000.0, 100.0, "2024-01-02")
    add_trade_pnl("USDT", 500.0,   25.0, "2024-01-02")
    summary = get_daily_summary("2024-01-02")
    assert len(summary) == 2
    symbols = {s["symbol"] for s in summary}
    assert symbols == {"ETH", "USDT"}


def test_get_period_pnl():
    from database.pnl_repo import add_trade_pnl, get_period_pnl
    add_trade_pnl("ETH", 100.0, 5.0, "2024-01-01")
    add_trade_pnl("ETH", 200.0, 10.0, "2024-01-02")
    add_trade_pnl("ETH", 300.0, 15.0, "2024-01-03")
    rows = get_period_pnl("ETH", "2024-01-01", "2024-01-02")
    assert len(rows) == 2


def test_pnl_symbol_isolation():
    from database.pnl_repo import add_trade_pnl, get_daily_pnl
    add_trade_pnl("ETH",  1000.0, 50.0, "2024-01-01")
    add_trade_pnl("USDT",  200.0, 10.0, "2024-01-01")
    eth_pnl  = get_daily_pnl("ETH",  "2024-01-01")
    usdt_pnl = get_daily_pnl("USDT", "2024-01-01")
    assert eth_pnl["realized_pnl"]  == pytest.approx(1000.0)
    assert usdt_pnl["realized_pnl"] == pytest.approx(200.0)
