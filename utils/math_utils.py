"""가격 반올림 / ATR / 손익 / 트리거 가격 계산 유틸리티."""

from typing import Sequence

# ── 빗썸 멤버십 등급별 거래 포인트 적립률 (%) ─────────────────────────────────
BITHUMB_TIERS: dict[str, float] = {
    "화이트": 0.003,
    "블루":   0.005,
    "그린":   0.008,
    "퍼플":   0.01,
    "오렌지": 0.01,
    "블랙":   0.01,
}


def get_point_rate(membership: str) -> float:
    """멤버십 등급명으로 포인트 적립률(%)을 반환한다. 알 수 없는 등급은 화이트 기준."""
    return BITHUMB_TIERS.get(membership, 0.003)


# ── 호가 단위 테이블 (빗썸 KRW 마켓 기준) ─────────────────────────────────────
# 실제 호가 조회(KRW-USDT @~1,510원)로 확인: 1,000~10,000원 구간도 1원 단위
_TICK_TABLE = [
    (2_000_000, 1_000),
    (1_000_000,   500),
    (  500_000,   100),
    (  100_000,    50),
    (   10_000,    10),
    (        0,     1),
]


def round_price(price: float) -> int:
    """KRW 마켓 호가 단위로 가격을 반올림한다."""
    for threshold, unit in _TICK_TABLE:
        if price >= threshold:
            return int(round(price / unit) * unit)
    return int(round(price))


# ── ATR ────────────────────────────────────────────────────────────────────────

def calc_atr(
    high_prices: Sequence[float],
    low_prices: Sequence[float],
    close_prices: Sequence[float],
) -> float:
    """True Range 평균(ATR)을 계산한다.

    TR = max(H-L, |H-Cprev|, |L-Cprev|)
    데이터가 2개 미만이면 0.0을 반환한다.
    """
    n = min(len(high_prices), len(low_prices), len(close_prices))
    if n < 2:
        return 0.0

    trs = []
    for i in range(1, n):
        h, lo, cp = float(high_prices[i]), float(low_prices[i]), float(close_prices[i - 1])
        trs.append(max(h - lo, abs(h - cp), abs(lo - cp)))

    return sum(trs) / len(trs)


def atr_to_trigger_pct(
    atr: float,
    current_price: float,
    min_pct: float = 0.3,
    max_pct: float = 1.0,
) -> float:
    """ATR을 현재가 대비 % 로 변환한 뒤 [min_pct, max_pct] 범위로 클램핑한다."""
    if current_price <= 0 or atr <= 0:
        return min_pct
    pct = (atr / current_price) * 100.0
    return max(min_pct, min(max_pct, pct))


# ── 트리거 가격 ────────────────────────────────────────────────────────────────

def calc_buy_target(reference_price: float, trigger_pct: float) -> float:
    """매수 트리거 가격 = 기준가 × (1 - trigger_pct / 100)."""
    return reference_price * (1.0 - trigger_pct / 100.0)


def calc_sell_target(
    reference_price: float,
    trigger_pct: float,
    extra_pct: float = 0.0,
) -> float:
    """매도 트리거 가격 = 기준가 × (1 + (trigger_pct + extra_pct) / 100)."""
    return reference_price * (1.0 + (trigger_pct + extra_pct) / 100.0)


# ── 손익 ──────────────────────────────────────────────────────────────────────

def calc_pnl(
    buy_price: float,
    sell_price: float,
    quantity: float,
    fee_rate: float = 0.04,
    point_rate: float = 0.0,
) -> dict[str, float]:
    """단순 실현 손익을 계산한다.

    Returns:
        gross:  매도금액 - 매수금액
        fee:    수수료 합계 (매수 + 매도 각각 fee_rate%)
        points: 포인트 적립액 (매수 + 매도 합계 × point_rate%)
        net:    gross - fee + points
    """
    buy_total  = buy_price  * quantity
    sell_total = sell_price * quantity
    fee    = (buy_total + sell_total) * fee_rate   / 100.0
    points = (buy_total + sell_total) * point_rate / 100.0
    gross  = sell_total - buy_total
    return {
        "gross":  gross,
        "fee":    round(fee,    2),
        "points": round(points, 2),
        "net":    round(gross - fee + points, 2),
    }


def calc_quantity(order_amount: float, price: float) -> float:
    """주문금액과 가격으로 수량을 계산한다. 소수점 8자리까지."""
    if price <= 0:
        return 0.0
    return round(order_amount / price, 8)
