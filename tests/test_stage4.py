"""4단계: 거래소 모듈 검증 테스트 (Mock 기반, 실 API 호출 없음)."""

import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from exchange.base import ExchangeBase
from exchange.bithumb_client import BithumbClient, _normalize_order


# ── 픽스처 ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_pb():
    """python_bithumb 패키지 전체를 Mock으로 대체한다."""
    with patch("exchange.bithumb_client._import_bithumb") as mock_import:
        pb = MagicMock()
        mock_import.return_value = pb
        yield pb


@pytest.fixture
def client(mock_pb):
    """BithumbClient 인스턴스 (private API도 Mock)."""
    mock_pb.Bithumb.return_value = MagicMock()
    c = BithumbClient("test_access", "test_secret")
    return c


# ── ExchangeBase 인터페이스 확인 ──────────────────────────────────────────────

def test_bithumb_client_is_exchange_base(client):
    assert isinstance(client, ExchangeBase)


def test_base_has_required_methods():
    import inspect
    methods = {
        "get_current_price", "get_balance", "get_balances",
        "buy_limit", "sell_limit", "get_order_status",
        "cancel_order", "get_open_orders", "get_ohlcv",
    }
    for m in methods:
        assert hasattr(ExchangeBase, m), f"ExchangeBase에 메서드 없음: {m}"


# ── get_current_price ─────────────────────────────────────────────────────────

def test_get_current_price(client, mock_pb):
    mock_pb.get_current_price.return_value = 3_245_000.0
    price = client.get_current_price("KRW-ETH")
    assert price == pytest.approx(3_245_000.0)
    mock_pb.get_current_price.assert_called_once_with("KRW-ETH")


def test_get_current_price_none_raises(client, mock_pb):
    mock_pb.get_current_price.return_value = None
    with pytest.raises(ValueError):
        client.get_current_price("KRW-ETH")


# ── get_balance / get_balances ────────────────────────────────────────────────

def test_get_balance(client):
    client._private.get_balance.return_value = 512_000.0
    bal = client.get_balance("KRW")
    assert bal == pytest.approx(512_000.0)


def test_get_balances(client):
    client._private.get_balances.return_value = [
        {"currency": "KRW",  "balance": "512000", "locked": "0"},
        {"currency": "ETH",  "balance": "0.5",    "locked": "0.01"},
        {"currency": "USDT", "balance": "1850",   "locked": "0"},
    ]
    balances = client.get_balances()
    assert len(balances) == 3
    krw = next(b for b in balances if b["currency"] == "KRW")
    assert krw["balance"] == pytest.approx(512_000.0)


# ── buy_limit ─────────────────────────────────────────────────────────────────

def test_buy_limit_returns_normalized(client):
    client._private.buy_limit_order.return_value = {
        "uuid": "uuid-buy-001",
        "side": "bid",
        "state": "wait",
        "price": "3000000",
        "volume": "0.01",
        "executed_volume": "0",
        "trades": [],
    }
    result = client.buy_limit("KRW-ETH", 3_000_000, 0.01)
    assert result["order_id"] == "uuid-buy-001"
    assert result["status"] == "open"
    assert result["side"] == "buy"
    assert result["price"] == pytest.approx(3_000_000.0)
    assert result["quantity"] == pytest.approx(0.01)


def test_buy_limit_calls_api(client):
    client._private.buy_limit_order.return_value = {
        "uuid": "x", "side": "bid", "state": "wait",
        "price": "3000000", "volume": "0.01", "executed_volume": "0", "trades": [],
    }
    client.buy_limit("KRW-ETH", 3_000_000, 0.01)
    client._private.buy_limit_order.assert_called_once_with(
        "KRW-ETH", price=3_000_000, volume=0.01
    )


# ── sell_limit ────────────────────────────────────────────────────────────────

def test_sell_limit_returns_normalized(client):
    client._private.sell_limit_order.return_value = {
        "uuid": "uuid-sell-001",
        "side": "ask",
        "state": "wait",
        "price": "3050000",
        "volume": "0.01",
        "executed_volume": "0",
        "trades": [],
    }
    result = client.sell_limit("KRW-ETH", 3_050_000, 0.01)
    assert result["order_id"] == "uuid-sell-001"
    assert result["side"] == "sell"
    assert result["status"] == "open"


# ── get_order_status ──────────────────────────────────────────────────────────

def test_get_order_status_open(client):
    client._private.get_order.return_value = {
        "uuid": "uuid-001", "side": "bid", "state": "wait",
        "price": "3000000", "volume": "0.01",
        "executed_volume": "0", "trades": [],
    }
    result = client.get_order_status("uuid-001")
    assert result["status"] == "open"
    assert result["filled_price"] is None


def test_get_order_status_filled_with_trades(client):
    client._private.get_order.return_value = {
        "uuid": "uuid-002", "side": "bid", "state": "done",
        "price": "3000000", "volume": "0.01",
        "executed_volume": "0.01",
        "trades": [{"price": "2990000", "volume": "0.01"}],
    }
    result = client.get_order_status("uuid-002")
    assert result["status"] == "filled"
    assert result["filled_price"] == pytest.approx(2_990_000.0)
    assert result["filled_quantity"] == pytest.approx(0.01)


def test_get_order_status_cancelled(client):
    client._private.get_order.return_value = {
        "uuid": "uuid-003", "side": "ask", "state": "cancel",
        "price": "3050000", "volume": "0.01",
        "executed_volume": "0", "trades": [],
    }
    result = client.get_order_status("uuid-003")
    assert result["status"] == "cancelled"


# ── cancel_order ──────────────────────────────────────────────────────────────

def test_cancel_order_success(client):
    client._private.cancel_order.return_value = {"uuid": "uuid-001", "state": "cancel"}
    assert client.cancel_order("uuid-001") is True


def test_cancel_order_failure_returns_false(client):
    client._private.cancel_order.side_effect = Exception("API error")
    assert client.cancel_order("uuid-001") is False


# ── get_open_orders ───────────────────────────────────────────────────────────

def test_get_open_orders(client):
    client._private.get_orders.return_value = [
        {"uuid": "u1", "side": "bid", "state": "wait",
         "price": "3000000", "volume": "0.01", "executed_volume": "0", "trades": []},
        {"uuid": "u2", "side": "bid", "state": "wait",
         "price": "2990000", "volume": "0.01", "executed_volume": "0", "trades": []},
    ]
    orders = client.get_open_orders("KRW-ETH")
    assert len(orders) == 2
    assert all(o["status"] == "open" for o in orders)


def test_get_open_orders_empty(client):
    client._private.get_orders.return_value = []
    assert client.get_open_orders("KRW-ETH") == []


# ── get_ohlcv ─────────────────────────────────────────────────────────────────

def test_get_ohlcv(client, mock_pb):
    import pandas as pd
    df = pd.DataFrame({
        "open":   [3_000_000, 3_010_000],
        "high":   [3_050_000, 3_060_000],
        "low":    [2_980_000, 2_990_000],
        "close":  [3_010_000, 3_020_000],
        "volume": [10.5, 12.3],
        "value":  [0, 0],
    })
    mock_pb.get_ohlcv.return_value = df
    candles = client.get_ohlcv("KRW-ETH", interval="minute60", count=2)
    assert len(candles) == 2
    assert set(candles[0].keys()) == {"open", "high", "low", "close", "volume"}
    assert candles[0]["close"] == pytest.approx(3_010_000.0)


def test_get_ohlcv_empty(client, mock_pb):
    import pandas as pd
    mock_pb.get_ohlcv.return_value = pd.DataFrame()
    assert client.get_ohlcv("KRW-ETH") == []


# ── retry 데코레이터 ──────────────────────────────────────────────────────────

def test_retry_succeeds_on_second_attempt():
    from exchange.retry import with_retry
    call_count = 0

    @with_retry(max_retries=2, base_delay=0.01)
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("일시적 오류")
        return "ok"

    result = flaky()
    assert result == "ok"
    assert call_count == 2


def test_retry_raises_after_max():
    from exchange.retry import with_retry

    @with_retry(max_retries=2, base_delay=0.01)
    def always_fail():
        raise ConnectionError("항상 실패")

    with pytest.raises(ConnectionError):
        always_fail()


def test_retry_exponential_delay():
    from exchange.retry import with_retry
    delays = []
    original_sleep = time.sleep

    def fake_sleep(s):
        delays.append(s)

    @with_retry(max_retries=2, base_delay=1.0)
    def fail():
        raise RuntimeError("실패")

    with patch("exchange.retry.time.sleep", side_effect=fake_sleep):
        with pytest.raises(RuntimeError):
            fail()

    assert delays == [1.0, 2.0]


# ── _normalize_order 단위 테스트 ──────────────────────────────────────────────

def test_normalize_wait_state():
    raw = {"uuid": "x", "side": "bid", "state": "wait",
           "price": "3000000", "volume": "0.1", "executed_volume": "0", "trades": []}
    result = _normalize_order(raw)
    assert result["status"] == "open"
    assert result["side"] == "buy"
    assert result["filled_price"] is None


def test_normalize_done_state_with_trades():
    raw = {
        "uuid": "y", "side": "ask", "state": "done",
        "price": "3050000", "volume": "0.1",
        "executed_volume": "0.1",
        "trades": [
            {"price": "3050000", "volume": "0.05"},
            {"price": "3060000", "volume": "0.05"},
        ],
    }
    result = _normalize_order(raw)
    assert result["status"] == "filled"
    assert result["side"] == "sell"
    # 평균 체결가 = (3050000×0.05 + 3060000×0.05) / 0.1 = 3055000
    assert result["filled_price"] == pytest.approx(3_055_000.0)


def test_normalize_cancel_state():
    raw = {"uuid": "z", "side": "bid", "state": "cancel",
           "price": "3000000", "volume": "0.1", "executed_volume": "0", "trades": []}
    result = _normalize_order(raw)
    assert result["status"] == "cancelled"
