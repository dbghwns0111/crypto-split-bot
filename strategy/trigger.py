"""트리거 조건 판단 및 가격 계산 — 순수 함수 모음."""

from utils.math_utils import calc_buy_target, calc_sell_target

ZONE_NORMAL    = "normal"
ZONE_DEFENSIVE = "defensive"
ZONE_EMERGENCY = "emergency"


def get_zone(current_round: int, max_rounds: int, cfg: dict) -> str:
    """현재 차수가 어느 구간에 해당하는지 반환한다."""
    s = cfg["strategy"]
    normal_limit     = max_rounds * s["zone_normal_pct"] / 100
    defensive_limit  = max_rounds * (s["zone_normal_pct"] + s["zone_defensive_pct"]) / 100

    if current_round <= normal_limit:
        return ZONE_NORMAL
    if current_round <= defensive_limit:
        return ZONE_DEFENSIVE
    return ZONE_EMERGENCY


def should_buy(
    current_price: float,
    reference_price: float,
    trigger_pct: float,
) -> bool:
    """매수 트리거 조건: current_price ≤ reference × (1 - trigger_pct/100)."""
    if reference_price <= 0:
        return False
    return current_price <= calc_buy_target(reference_price, trigger_pct)


def should_sell(
    current_price: float,
    reference_price: float,
    trigger_pct: float,
    extra_pct: float = 0.0,
) -> bool:
    """매도 트리거 조건: current_price ≥ reference × (1 + trigger_pct/100)."""
    if reference_price <= 0:
        return False
    return current_price >= calc_sell_target(reference_price, trigger_pct, extra_pct)


def buy_order_price(reference_price: float, trigger_pct: float) -> float:
    """매수 지정가를 반환한다."""
    return calc_buy_target(reference_price, trigger_pct)


def sell_order_price(
    reference_price: float,
    trigger_pct: float,
    extra_pct: float = 0.0,
) -> float:
    """매도 지정가를 반환한다."""
    return calc_sell_target(reference_price, trigger_pct, extra_pct)


# ── KRW 단위 모드 ─────────────────────────────────────────────────────────────

def should_buy_krw(
    current_price: float,
    reference_price: float,
    trigger_krw: float,
) -> bool:
    """KRW 모드 매수 트리거: current_price ≤ reference − trigger_krw."""
    if reference_price <= 0:
        return False
    return current_price <= reference_price - trigger_krw


def should_sell_krw(
    current_price: float,
    reference_price: float,
    trigger_krw: float,
) -> bool:
    """KRW 모드 매도 트리거: current_price ≥ reference + trigger_krw."""
    if reference_price <= 0:
        return False
    return current_price >= reference_price + trigger_krw


def buy_order_price_krw(reference_price: float, trigger_krw: float) -> float:
    """KRW 모드 매수 지정가."""
    return reference_price - trigger_krw


def sell_order_price_krw(reference_price: float, trigger_krw: float) -> float:
    """KRW 모드 매도 지정가."""
    return reference_price + trigger_krw
