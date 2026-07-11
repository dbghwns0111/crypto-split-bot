"""5단계: 전략 모듈 검증 테스트."""

import time
from unittest.mock import MagicMock, patch, call

import pytest

from database.connection import configure, get_connection, close_connection
from database.models import create_tables
from database.state_repo import upsert_state, get_state


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
                "max_rounds": 10,
                "trigger_pct": 0.5,
            }
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
    ex.get_current_price.return_value = 3_000_000.0
    ex.buy_limit.return_value  = {"order_id": "buy-001",  "status": "open"}
    ex.sell_limit.return_value = {"order_id": "sell-001", "status": "open"}
    ex.get_order_status.return_value = {"status": "open", "filled_price": None,
                                         "filled_quantity": 0, "price": 3_000_000.0}
    ex.cancel_order.return_value = True
    ex.get_ohlcv.return_value = []
    return ex


# ══════════════════════════════════════════════════════════════════════════════
# trigger.py
# ══════════════════════════════════════════════════════════════════════════════

def test_get_zone_normal(base_cfg):
    from strategy.trigger import get_zone, ZONE_NORMAL
    assert get_zone(2, 10, base_cfg) == ZONE_NORMAL


def test_get_zone_defensive(base_cfg):
    from strategy.trigger import get_zone, ZONE_DEFENSIVE
    assert get_zone(5, 10, base_cfg) == ZONE_DEFENSIVE


def test_get_zone_emergency(base_cfg):
    from strategy.trigger import get_zone, ZONE_EMERGENCY
    assert get_zone(9, 10, base_cfg) == ZONE_EMERGENCY


def test_should_buy_true():
    from strategy.trigger import should_buy
    # 3,000,000 × (1 - 0.5/100) = 2,985,000 → 2,984,000 ≤ 2,985,000
    assert should_buy(2_984_000, 3_000_000, 0.5) is True


def test_should_buy_false():
    from strategy.trigger import should_buy
    assert should_buy(3_000_000, 3_000_000, 0.5) is False


def test_should_buy_zero_reference():
    from strategy.trigger import should_buy
    assert should_buy(1000, 0, 0.5) is False


def test_should_sell_true():
    from strategy.trigger import should_sell
    # 3,000,000 × (1 + 0.58/100) = 3,017,400 → 3,020,000 ≥ 3,017,400
    assert should_sell(3_020_000, 3_000_000, 0.5, 0.08) is True


def test_should_sell_false():
    from strategy.trigger import should_sell
    assert should_sell(3_000_000, 3_000_000, 0.5, 0.08) is False


def test_buy_order_price():
    from strategy.trigger import buy_order_price
    assert buy_order_price(3_000_000, 0.5) == pytest.approx(2_985_000.0)


def test_sell_order_price():
    from strategy.trigger import sell_order_price
    assert sell_order_price(3_000_000, 0.5, 0.08) == pytest.approx(3_017_400.0)


def test_sell_price_always_above_buy_price():
    from strategy.trigger import buy_order_price, sell_order_price
    ref = 3_000_000
    buy = buy_order_price(ref, 0.5)
    sell = sell_order_price(ref, 0.5)
    assert sell > buy


# ══════════════════════════════════════════════════════════════════════════════
# volatility.py
# ══════════════════════════════════════════════════════════════════════════════

def test_volatility_uses_fallback_on_empty_candles(base_cfg):
    from strategy.volatility import VolatilityMeter
    ex = MagicMock()
    ex.get_ohlcv.return_value = []
    vm = VolatilityMeter(ex, "ETH", "KRW-ETH", base_cfg)
    pct = vm.get_trigger_pct()
    assert pct == pytest.approx(0.5)  # config fallback


def test_volatility_uses_atr(base_cfg):
    from strategy.volatility import VolatilityMeter
    ex = MagicMock()
    # ATR이 0이 아닌 캔들 데이터 (ATR ~ 15,000 / 3,000,000 = 0.5%)
    candles = [
        {"open": 3_000_000, "high": 3_015_000, "low": 2_985_000, "close": 3_000_000, "volume": 1},
        {"open": 3_000_000, "high": 3_015_000, "low": 2_985_000, "close": 3_000_000, "volume": 1},
    ]
    ex.get_ohlcv.return_value = candles
    vm = VolatilityMeter(ex, "ETH", "KRW-ETH", base_cfg)
    pct = vm.get_trigger_pct()
    assert 0.3 <= pct <= 1.0


def test_volatility_cache(base_cfg):
    from strategy.volatility import VolatilityMeter
    ex = MagicMock()
    ex.get_ohlcv.return_value = []
    vm = VolatilityMeter(ex, "ETH", "KRW-ETH", base_cfg)
    vm.get_trigger_pct()
    vm.get_trigger_pct()
    # 두 번 호출했지만 exchange는 한 번만 호출 (캐시)
    assert ex.get_ohlcv.call_count == 1


def test_volatility_invalidate_forces_recompute(base_cfg):
    from strategy.volatility import VolatilityMeter
    ex = MagicMock()
    ex.get_ohlcv.return_value = []
    vm = VolatilityMeter(ex, "ETH", "KRW-ETH", base_cfg)
    vm.get_trigger_pct()
    vm.invalidate()
    vm.get_trigger_pct()
    assert ex.get_ohlcv.call_count == 2


def test_volatility_fallback_on_exception(base_cfg):
    from strategy.volatility import VolatilityMeter
    ex = MagicMock()
    ex.get_ohlcv.side_effect = RuntimeError("API 오류")
    vm = VolatilityMeter(ex, "ETH", "KRW-ETH", base_cfg)
    pct = vm.get_trigger_pct()
    assert pct == pytest.approx(0.5)


# ══════════════════════════════════════════════════════════════════════════════
# circuit_breaker.py
# ══════════════════════════════════════════════════════════════════════════════

def test_circuit_breaker_not_active_on_small_change(base_cfg):
    from strategy.circuit_breaker import CircuitBreaker
    upsert_state("ETH")
    cb = CircuitBreaker("ETH", base_cfg)
    for p in [3_000_000, 3_010_000, 3_020_000]:  # ~0.7% 변화
        active = cb.update(p)
    assert active is False


def test_circuit_breaker_activates_on_large_change(base_cfg):
    from strategy.circuit_breaker import CircuitBreaker
    upsert_state("ETH")
    cb = CircuitBreaker("ETH", base_cfg)
    # 5% 초과 변화
    cb.update(3_000_000)
    active = cb.update(3_200_000)  # +6.67%
    assert active is True


def test_circuit_breaker_reset(base_cfg):
    from strategy.circuit_breaker import CircuitBreaker
    upsert_state("ETH")
    cb = CircuitBreaker("ETH", base_cfg)
    cb.update(3_000_000)
    cb.update(3_200_000)
    assert cb.is_active() is True
    cb.reset()
    assert cb.is_active() is False


def test_circuit_breaker_persists_to_db(base_cfg):
    from strategy.circuit_breaker import CircuitBreaker
    upsert_state("ETH")
    cb = CircuitBreaker("ETH", base_cfg)
    cb.update(3_000_000)
    cb.update(3_200_000)
    state = get_state("ETH")
    assert state["circuit_breaker_on"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# magic_split.py
# ══════════════════════════════════════════════════════════════════════════════

def test_magic_split_init(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    assert bot.symbol == "ETH"
    assert bot.ticker == "KRW-ETH"
    assert bot.is_running is False


def test_tick_initializes_reference_price(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    upsert_state("ETH")
    mock_exchange.get_current_price.return_value = 3_000_000.0
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    bot._tick()
    assert bot.reference_price == pytest.approx(3_000_000.0)


def test_tick_places_buy_on_trigger(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    upsert_state("ETH", reference_price=3_000_000.0, current_round=0)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    # 기준가 3,000,000 → 매수 트리거: 2,985,000 이하
    mock_exchange.get_current_price.return_value = 2_980_000.0
    bot._tick()
    mock_exchange.buy_limit.assert_called_once()


def test_tick_no_buy_on_max_round(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    upsert_state("ETH", reference_price=3_000_000.0, current_round=10)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    mock_exchange.get_current_price.return_value = 2_980_000.0
    bot._tick()
    mock_exchange.buy_limit.assert_not_called()


def test_current_max_rounds_reads_live_from_cfg(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    assert bot._current_max_rounds == 10
    base_cfg["pairs"]["ETH"]["max_rounds"] = 25
    assert bot._current_max_rounds == 25


def test_max_rounds_increase_allows_buy_without_restart(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    mock_exchange.get_balance.return_value = 10_000_000.0
    upsert_state("ETH", reference_price=3_000_000.0, current_round=10)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    mock_exchange.get_current_price.return_value = 2_980_000.0
    bot._tick()
    mock_exchange.buy_limit.assert_not_called()  # 최대차수(10) 도달 상태라 매수 안 함

    # 가동 중에 cfg만 수정(POST /strategy/settings가 하는 것과 동일) — 재시작 없이
    base_cfg["pairs"]["ETH"]["max_rounds"] = 20
    bot._tick()
    mock_exchange.buy_limit.assert_called_once()  # 여유가 생겨 매수 재개


def test_tick_no_double_buy(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    upsert_state("ETH", reference_price=3_000_000.0, current_round=0)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    mock_exchange.get_current_price.return_value = 2_980_000.0
    bot._tick()
    bot._tick()  # 두 번째 tick에서 이미 pending_buy가 있음
    assert mock_exchange.buy_limit.call_count == 1


def test_set_start_price_applies_immediately_at_round_zero(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    from database.state_repo import set_start_price
    upsert_state("ETH", reference_price=0.0, current_round=0)
    set_start_price("ETH", 3_000_000.0)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    applied = bot.set_start_price(3_100_000.0)
    assert applied is True
    assert bot.start_price == pytest.approx(3_100_000.0)
    assert bot.reference_price == pytest.approx(3_100_000.0)
    # DB에도 반영됐는지 확인
    assert get_state("ETH")["start_price"] == pytest.approx(3_100_000.0)


def test_set_start_price_deferred_when_round_active(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    from database.state_repo import set_start_price
    upsert_state("ETH", reference_price=2_900_000.0, current_round=3)
    set_start_price("ETH", 3_000_000.0)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    applied = bot.set_start_price(3_200_000.0)
    assert applied is False
    # 진행 중인 그리드(기준가/차수)는 그대로 — 실거래 원가 보호
    assert bot.reference_price == pytest.approx(2_900_000.0)
    assert bot.current_round == 3
    # 새 시작가는 다음 0차 복귀 시 쓰이도록 DB에는 저장됨
    assert get_state("ETH")["start_price"] == pytest.approx(3_200_000.0)


def test_on_buy_filled_updates_state(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    upsert_state("ETH", reference_price=3_000_000.0, current_round=0)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    bot._on_buy_filled(2_985_000.0, 0.0167)
    assert bot.current_round == 1
    assert bot.reference_price == pytest.approx(2_985_000.0)


def test_on_sell_filled_decrements_round(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    from database.order_repo import save_order, update_order
    upsert_state("ETH", reference_price=2_985_000.0, current_round=1)
    # DB에 체결된 매수 주문 추가
    save_order("ETH", "b001", "buy", 2_985_000, 0.0167, 49850.0, 1)
    update_order("b001", "filled", filled_price=2_985_000, filled_at="2024-01-01 10:00:00")

    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    bot._on_sell_filled(3_003_000.0, 0.0167)
    assert bot.current_round == 0
    assert bot.reference_price == pytest.approx(3_003_000.0)


def test_tick_no_buy_when_circuit_breaker_active(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    upsert_state("ETH", reference_price=3_000_000.0, current_round=0)
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    # 서킷브레이커 강제 발동
    bot._circuit_breaker._active = True
    bot._circuit_breaker._active_until = time.monotonic() + 300
    mock_exchange.get_current_price.return_value = 2_980_000.0
    bot._tick()
    mock_exchange.buy_limit.assert_not_called()


def test_timeout_cancels_pending_buy(base_cfg, mock_exchange):
    from strategy.magic_split import MagicSplit
    from database.order_repo import save_order
    upsert_state("ETH", reference_price=3_000_000.0, current_round=0)
    save_order("ETH", "buy-t1", "buy", 2_985_000, 0.0167, 49850.0, 1)

    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._restore_state()
    # 타임아웃이 이미 지난 것처럼 설정
    bot._pending_buy = {
        "order_id": "buy-t1",
        "placed_at": time.monotonic() - 400,  # 400초 전 (timeout=300)
    }
    bot._handle_timeouts()
    mock_exchange.cancel_order.assert_called_once_with("buy-t1")
    assert bot._pending_buy is None
