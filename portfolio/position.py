"""활성 포지션 분석 — 미실현 손익, 손익분기점, 차수별 상세."""

from database.connection import get_connection
from database.state_repo import get_state
from utils.logger import get_logger

_log = get_logger("portfolio.position")

_FEE_RATE = 0.05  # 빗썸 수수료 0.05%


def get_active_position(symbol: str) -> dict | None:
    """symbol의 현재 봇 실행 상태를 반환한다. 초기화 전이면 None."""
    state = get_state(symbol)
    if not state:
        return None
    return {
        "symbol":            symbol,
        "current_round":     int(state["current_round"] or 0),
        "reference_price":   float(state["reference_price"] or 0),
        "is_running":        bool(state["is_running"]),
        "circuit_breaker_on": bool(state["circuit_breaker_on"]),
    }


def get_active_buy_orders(symbol: str) -> list[dict]:
    """아직 매도되지 않은 체결 매수 주문을 oldest-first 로 반환한다.

    current_round 만큼의 최근 체결 매수 주문을 사용한다.
    """
    state = get_state(symbol)
    if not state or int(state.get("current_round") or 0) == 0:
        return []

    n = int(state["current_round"])
    rows = get_connection().execute(
        """
        SELECT * FROM orders
        WHERE symbol = ? AND side = 'buy' AND status = 'filled'
        ORDER BY filled_at DESC
        LIMIT ?
        """,
        (symbol, n),
    ).fetchall()
    # oldest-first 반환
    return list(reversed([dict(r) for r in rows]))


def get_total_quantity(symbol: str) -> float:
    """현재 보유 중인 코인 총 수량을 반환한다."""
    return sum(b["quantity"] for b in get_active_buy_orders(symbol))


def get_total_cost(symbol: str) -> float:
    """현재 보유 포지션의 총 매수 금액(원)을 반환한다."""
    buys = get_active_buy_orders(symbol)
    return sum(_fill_price(b) * b["quantity"] for b in buys)


def get_breakeven_price(symbol: str) -> float:
    """수수료를 포함한 손익분기점 가격을 반환한다.

    손익분기가 = 평균매수가 × (1 + 매수수수료% + 매도수수료%) / 100
    """
    buys = get_active_buy_orders(symbol)
    if not buys:
        return 0.0
    total_cost = sum(_fill_price(b) * b["quantity"] for b in buys)
    total_qty  = sum(b["quantity"] for b in buys)
    if total_qty <= 0:
        return 0.0
    avg_price = total_cost / total_qty
    return avg_price * (1 + _FEE_RATE * 2 / 100)


def get_unrealized_pnl(symbol: str, current_price: float) -> float:
    """수수료를 제외한 단순 미실현 손익(원)을 반환한다."""
    buys = get_active_buy_orders(symbol)
    if not buys:
        return 0.0
    total_cost  = sum(_fill_price(b) * b["quantity"] for b in buys)
    total_qty   = sum(b["quantity"] for b in buys)
    current_val = current_price * total_qty
    return current_val - total_cost


def get_position_summary(symbol: str, current_price: float) -> dict:
    """포지션 종합 요약을 반환한다."""
    state = get_state(symbol) or {}
    buys  = get_active_buy_orders(symbol)

    total_qty  = sum(b["quantity"] for b in buys)
    total_cost = sum(_fill_price(b) * b["quantity"] for b in buys)
    cur_value  = current_price * total_qty
    unrealized = cur_value - total_cost
    breakeven  = get_breakeven_price(symbol)
    avg_buy    = (total_cost / total_qty) if total_qty > 0 else 0.0

    return {
        "symbol":          symbol,
        "current_round":   int(state.get("current_round") or 0),
        "reference_price": float(state.get("reference_price") or 0),
        "current_price":   current_price,
        "total_quantity":  total_qty,
        "total_cost":      round(total_cost, 2),
        "current_value":   round(cur_value, 2),
        "unrealized_pnl":  round(unrealized, 2),
        "avg_buy_price":   round(avg_buy, 2),
        "breakeven_price": round(breakeven, 2),
        "is_running":      bool(state.get("is_running")),
        "is_profitable":   current_price >= breakeven if breakeven > 0 else False,
    }


def get_round_details(symbol: str) -> list[dict]:
    """차수별 매수 내역을 oldest-first 로 반환한다."""
    buys = get_active_buy_orders(symbol)
    return [
        {
            "round_no":  i + 1,
            "order_id":  b["order_id"],
            "buy_price": _fill_price(b),
            "quantity":  b["quantity"],
            "amount":    round(_fill_price(b) * b["quantity"], 2),
            "filled_at": b.get("filled_at"),
        }
        for i, b in enumerate(buys)
    ]


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _fill_price(order: dict) -> float:
    """체결가가 있으면 체결가, 없으면 주문가를 반환한다."""
    return float(order.get("filled_price") or order.get("price") or 0)
