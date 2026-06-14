"""2단계: utils 모듈 검증 테스트."""

import time
from datetime import datetime, timezone, timedelta

import pytest

# ── logger ────────────────────────────────────────────────────────────────────

def test_get_logger_returns_logger():
    from utils.logger import get_logger
    import logging
    logger = get_logger("test_logger")
    assert isinstance(logger, logging.Logger)


def test_get_logger_singleton():
    from utils.logger import get_logger
    a = get_logger("singleton_test")
    b = get_logger("singleton_test")
    assert a is b


def test_get_logger_has_handlers():
    from utils.logger import get_logger
    logger = get_logger("handler_test")
    assert len(logger.handlers) >= 1


def test_logger_writes_without_error(caplog):
    from utils.logger import get_logger
    import logging
    logger = get_logger("write_test")
    with caplog.at_level(logging.DEBUG):
        logger.info("테스트 메시지")


# ── time_utils ────────────────────────────────────────────────────────────────

def test_now_kst_is_aware():
    from utils.time_utils import now_kst, KST
    dt = now_kst()
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(hours=9)


def test_now_kst_str_format():
    from utils.time_utils import now_kst_str
    s = now_kst_str()
    # "YYYY-MM-DD HH:MM:SS" 형식
    assert len(s) == 19
    assert s[4] == "-" and s[7] == "-"


def test_today_kst_str_format():
    from utils.time_utils import today_kst_str
    s = today_kst_str()
    assert len(s) == 10
    assert s[4] == "-" and s[7] == "-"


def test_elapsed_seconds_aware():
    from utils.time_utils import elapsed_seconds, now_kst
    start = now_kst()
    time.sleep(0.05)
    elapsed = elapsed_seconds(start)
    assert elapsed >= 0.04
    assert elapsed < 5.0


def test_elapsed_seconds_naive():
    from utils.time_utils import elapsed_seconds
    # naive datetime은 KST로 간주
    naive = datetime.now()
    time.sleep(0.02)
    elapsed = elapsed_seconds(naive)
    assert elapsed >= 0.0


def test_parse_kst_round_trip():
    from utils.time_utils import now_kst_str, parse_kst, KST
    s = now_kst_str()
    dt = parse_kst(s)
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(hours=9)
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == s


def test_to_kst_converts():
    from utils.time_utils import to_kst, KST
    utc_dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    kst_dt = to_kst(utc_dt)
    assert kst_dt.hour == 9
    assert kst_dt.tzinfo == KST


# ── math_utils: round_price ───────────────────────────────────────────────────

@pytest.mark.parametrize("price,expected", [
    (3_245_678, 3_246_000),   # 2M 이상 → 1,000 단위
    (1_500_300, 1_500_500),   # 1M 이상 → 500 단위
    (750_123,   750_100),     # 500K 이상 → 100 단위
    (150_075,   150_100),     # 100K 이상 → 50 단위
    (15_007,    15_010),      # 10K 이상 → 10 단위
    (1_353,     1_355),       # 1K 이상 → 5 단위
    (500,       500),         # 1K 미만 → 1 단위
])
def test_round_price(price, expected):
    from utils.math_utils import round_price
    assert round_price(price) == expected


def test_round_price_returns_int():
    from utils.math_utils import round_price
    assert isinstance(round_price(1234567.8), int)


# ── math_utils: calc_atr ──────────────────────────────────────────────────────

def test_calc_atr_basic():
    from utils.math_utils import calc_atr
    highs  = [100, 110, 105, 108]
    lows   = [ 90,  95, 100, 100]
    closes = [ 95, 105, 103, 106]
    atr = calc_atr(highs, lows, closes)
    assert atr > 0


def test_calc_atr_too_few_data():
    from utils.math_utils import calc_atr
    assert calc_atr([100], [90], [95]) == 0.0


def test_calc_atr_two_candles():
    from utils.math_utils import calc_atr
    # TR = max(110-95, |110-95|, |95-95|) = 15
    atr = calc_atr([100, 110], [90, 95], [95, 95])
    assert atr == pytest.approx(15.0)


# ── math_utils: atr_to_trigger_pct ───────────────────────────────────────────

def test_atr_trigger_clamp_min():
    from utils.math_utils import atr_to_trigger_pct
    # ATR 1 / price 10000 = 0.01% → min_pct=0.3
    assert atr_to_trigger_pct(1, 10000, 0.3, 1.0) == pytest.approx(0.3)


def test_atr_trigger_clamp_max():
    from utils.math_utils import atr_to_trigger_pct
    # ATR 5000 / price 10000 = 50% → max_pct=1.0
    assert atr_to_trigger_pct(5000, 10000, 0.3, 1.0) == pytest.approx(1.0)


def test_atr_trigger_in_range():
    from utils.math_utils import atr_to_trigger_pct
    # ATR 50 / price 10000 = 0.5%
    result = atr_to_trigger_pct(50, 10000, 0.3, 1.0)
    assert result == pytest.approx(0.5)


def test_atr_trigger_zero_price():
    from utils.math_utils import atr_to_trigger_pct
    assert atr_to_trigger_pct(100, 0) == pytest.approx(0.3)


# ── math_utils: calc_buy_target / calc_sell_target ───────────────────────────

def test_calc_buy_target():
    from utils.math_utils import calc_buy_target
    # 기준가 1,000,000 × (1 - 0.5/100) = 995,000
    assert calc_buy_target(1_000_000, 0.5) == pytest.approx(995_000.0)


def test_calc_sell_target():
    from utils.math_utils import calc_sell_target
    # 기준가 1,000,000 × (1 + (0.5+0.08)/100) = 1,005,800
    assert calc_sell_target(1_000_000, 0.5, 0.08) == pytest.approx(1_005_800.0)


def test_sell_target_always_above_buy_target():
    from utils.math_utils import calc_buy_target, calc_sell_target
    ref = 3_000_000
    trig = 0.5
    buy = calc_buy_target(ref, trig)
    sell = calc_sell_target(ref, trig)
    assert sell > buy


# ── math_utils: calc_pnl ─────────────────────────────────────────────────────

def test_calc_pnl_profit():
    from utils.math_utils import calc_pnl
    result = calc_pnl(buy_price=1_000_000, sell_price=1_010_000, quantity=0.01)
    assert result["gross"] == pytest.approx(100.0)
    assert result["fee"] > 0
    assert result["net"] < result["gross"]


def test_calc_pnl_loss():
    from utils.math_utils import calc_pnl
    result = calc_pnl(buy_price=1_010_000, sell_price=1_000_000, quantity=0.01)
    assert result["gross"] < 0
    assert result["net"] < result["gross"]


def test_calc_pnl_keys():
    from utils.math_utils import calc_pnl
    result = calc_pnl(1000, 1100, 1.0)
    assert set(result.keys()) == {"gross", "fee", "net"}


# ── math_utils: calc_quantity ────────────────────────────────────────────────

def test_calc_quantity_basic():
    from utils.math_utils import calc_quantity
    qty = calc_quantity(order_amount=50_000, price=3_000_000)
    assert qty == pytest.approx(50_000 / 3_000_000, rel=1e-6)


def test_calc_quantity_zero_price():
    from utils.math_utils import calc_quantity
    assert calc_quantity(50_000, 0) == 0.0
