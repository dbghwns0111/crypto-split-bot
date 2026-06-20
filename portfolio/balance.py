"""잔고 조회, 주문 가능 여부 검증, 구간별 자금 배분, 스냅샷."""

from exchange.base import ExchangeBase
from utils.logger import get_logger
from utils.time_utils import now_kst_str

_log = get_logger("portfolio.balance")


def get_available_krw(exchange: ExchangeBase) -> float:
    """주문 가능 KRW 잔고를 반환한다."""
    return exchange.get_balance("KRW")


def get_coin_balance(exchange: ExchangeBase, currency: str) -> float:
    """currency 주문 가능 잔고를 반환한다. (예: 'ETH', 'USDT')"""
    return exchange.get_balance(currency)


def get_all_balances(exchange: ExchangeBase) -> dict[str, float]:
    """전체 잔고를 {currency: total_balance} 형태로 반환한다. (가용 + 주문잠금 합산)"""
    try:
        raw = exchange.get_balances()
        return {
            b["currency"]: float(b.get("balance", 0) or 0) + float(b.get("locked", 0) or 0)
            for b in raw
            if b.get("currency")
        }
    except Exception as exc:
        _log.error("잔고 조회 실패: %s", exc)
        return {}


def check_buy_feasible(
    exchange: ExchangeBase,
    order_amount: float,
    current_round: int,
    max_rounds: int,
) -> tuple[bool, str]:
    """매수 주문 가능 여부를 확인한다.

    Returns:
        (feasible: bool, reason: str)
    """
    if current_round >= max_rounds:
        return False, f"최대 차수 도달 ({current_round}/{max_rounds}차)"

    try:
        krw = exchange.get_balance("KRW")
    except Exception as exc:
        return False, f"잔고 조회 실패: {exc}"

    if krw < order_amount:
        return False, f"KRW 잔고 부족 ({krw:,.0f}원 < {order_amount:,.0f}원)"

    return True, "ok"


def check_sell_feasible(
    exchange: ExchangeBase,
    currency: str,
    quantity: float,
) -> tuple[bool, str]:
    """매도 주문 가능 여부를 확인한다."""
    try:
        balance = exchange.get_balance(currency)
    except Exception as exc:
        return False, f"잔고 조회 실패: {exc}"

    if balance < quantity:
        return False, f"{currency} 잔고 부족 ({balance} < {quantity})"

    return True, "ok"


def get_zone_capital(cfg: dict, symbol: str) -> dict[str, float]:
    """구간별 배분 자금액을 반환한다.

    Returns:
        {"normal": float, "defensive": float, "emergency": float, "total": float}
    """
    capital = float(cfg["pairs"][symbol]["capital"])
    s = cfg["strategy"]
    return {
        "normal":    capital * s["zone_normal_pct"]    / 100,
        "defensive": capital * s["zone_defensive_pct"] / 100,
        "emergency": capital * s["zone_emergency_pct"] / 100,
        "total":     capital,
    }


def take_snapshot(exchange: ExchangeBase, symbols: list[str]) -> dict:
    """현재 시점의 잔고 스냅샷을 반환한다.

    Returns:
        {"KRW": float, "<SYMBOL>": float, ..., "timestamp": str}
    """
    balances = get_all_balances(exchange)
    result: dict = {
        "KRW":       balances.get("KRW", 0.0),
        "timestamp": now_kst_str(),
    }
    for sym in symbols:
        result[sym] = balances.get(sym, 0.0)
    return result
