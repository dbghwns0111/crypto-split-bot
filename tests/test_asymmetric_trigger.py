"""매수/매도 트리거 간격 독립 설정 테스트."""

from unittest.mock import MagicMock

import pytest

from database.connection import configure, get_connection, close_connection
from database.models import create_tables
from strategy.magic_split import MagicSplit


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
                "trigger_mode": "krw",
                "trigger_krw": 100.0,
                "trigger_pct": 0.5,
            }
        },
        "strategy": {
            "trigger_min_pct": 0.3,
            "trigger_max_pct": 1.0,
            "sell_extra_pct": 0.0,
            "circuit_breaker_pct": 5.0,
            "circuit_breaker_window": 300,
            "order_timeout": 300,
            "price_poll_interval": 1,
        },
    }


@pytest.fixture
def mock_exchange():
    ex = MagicMock()
    ex.get_current_price.return_value = 10_000.0
    ex.get_ohlcv.return_value = []
    return ex


def _make_bot(base_cfg, mock_exchange, reference_price=10_000.0, current_round=1):
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    bot._reference_price = reference_price
    bot._current_round = current_round
    return bot


# ── KRW 모드: 매도 간격을 매수와 다르게 설정 ────────────────────────────────────

def test_krw_sell_trigger_defaults_to_buy_when_unset(base_cfg, mock_exchange):
    """매도 간격 미설정 시 매수 간격(100원)과 동일해야 한다(기존 대칭 동작 호환)."""
    bot = _make_bot(base_cfg, mock_exchange)
    assert bot.pending_sell_price == 10_100  # 10000 + 100
    assert bot.pending_buy_price  == 9_900   # 10000 - 100 (매수는 항상 매수 간격 사용)


def test_krw_sell_trigger_independent_when_set(base_cfg, mock_exchange):
    """사용자 예시: 기준가 10,000원, 매수간격 100원, 매도간격 200원 → 매도트리거 10,200원."""
    base_cfg["pairs"]["ETH"]["trigger_krw_sell"] = 200.0
    bot = _make_bot(base_cfg, mock_exchange)
    assert bot.pending_sell_price == 10_200  # 10000 + 200 (매도 전용 간격)
    assert bot.pending_buy_price  == 9_900   # 매수 간격은 그대로 100원


def test_krw_sell_trigger_zero_falls_back_to_buy(base_cfg, mock_exchange):
    """매도 간격을 0으로 저장해도(빈 입력 전송 등) 매수 간격으로 대체되어야 한다."""
    base_cfg["pairs"]["ETH"]["trigger_krw_sell"] = 0.0
    bot = _make_bot(base_cfg, mock_exchange)
    assert bot.pending_sell_price == 10_100


# ── PCT 모드: ATR 폴백(정적 trigger_pct) 기준 비대칭 ────────────────────────────

def test_pct_sell_trigger_defaults_to_buy_dynamic_value(base_cfg, mock_exchange):
    """ATR 계산 실패 시 정적 trigger_pct(0.5%)로 폴백 — 매도 간격 미설정이면 동일 값 사용."""
    base_cfg["pairs"]["ETH"]["trigger_mode"] = "pct"
    bot = _make_bot(base_cfg, mock_exchange)
    assert bot.pending_sell_price == round(10_000 * 1.005)  # +0.5%
    assert bot.pending_buy_price  == round(10_000 * 0.995)  # -0.5%


def test_pct_sell_trigger_independent_when_set(base_cfg, mock_exchange):
    base_cfg["pairs"]["ETH"]["trigger_mode"] = "pct"
    base_cfg["pairs"]["ETH"]["trigger_pct_sell"] = 1.0
    bot = _make_bot(base_cfg, mock_exchange)
    assert bot.pending_sell_price == round(10_000 * 1.01)   # 매도 전용 1.0%
    assert bot.pending_buy_price  == round(10_000 * 0.995)  # 매수는 그대로 0.5%


# ── 헬퍼 메서드 직접 검증 ────────────────────────────────────────────────────────

def test_sell_trigger_krw_helper_fallback(base_cfg, mock_exchange):
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    assert bot._sell_trigger_krw({}, 100.0) == 100.0
    assert bot._sell_trigger_krw({"trigger_krw_sell": 0}, 100.0) == 100.0
    assert bot._sell_trigger_krw({"trigger_krw_sell": 200.0}, 100.0) == 200.0


def test_sell_trigger_pct_helper_fallback(base_cfg, mock_exchange):
    bot = MagicSplit("ETH", mock_exchange, base_cfg)
    assert bot._sell_trigger_pct({}, 0.5) == 0.5
    assert bot._sell_trigger_pct({"trigger_pct_sell": 0}, 0.5) == 0.5
    assert bot._sell_trigger_pct({"trigger_pct_sell": 1.0}, 0.5) == 1.0
