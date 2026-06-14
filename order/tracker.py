"""체결 확인 — 미체결 주문 일괄 sync, 단일 조회, 요약."""

from database.order_repo import get_open_orders, get_order, update_order
from exchange.base import ExchangeBase
from utils.logger import get_logger
from utils.time_utils import now_kst_str

_log = get_logger("order.tracker")


def sync_open_orders(symbol: str, exchange: ExchangeBase) -> list[dict]:
    """DB 미체결 주문을 거래소 API로 동기화하고 새로 체결된 주문 목록을 반환한다."""
    open_orders = get_open_orders(symbol)
    filled = []

    for order in open_orders:
        oid = order["order_id"]
        try:
            info = exchange.get_order_status(oid)
        except Exception as exc:
            _log.error("주문 조회 실패 (%s): %s", oid, exc)
            continue

        if info["status"] == "filled":
            fp = info.get("filled_price") or info.get("price")
            update_order(oid, "filled", filled_price=fp, filled_at=now_kst_str())
            order = dict(order)
            order["status"]       = "filled"
            order["filled_price"] = fp
            filled.append(order)
            _log.info("체결 확인: %s %s (체결가=%s)", symbol, oid, fp)

        elif info["status"] == "cancelled":
            update_order(oid, "cancelled")
            _log.info("취소 확인: %s %s", symbol, oid)

    return filled


def check_order(order_id: str, exchange: ExchangeBase) -> dict:
    """단일 주문의 현재 상태를 조회하고 DB를 업데이트한 뒤 상태 dict를 반환한다."""
    try:
        info = exchange.get_order_status(order_id)
    except Exception as exc:
        _log.error("주문 조회 실패 (%s): %s", order_id, exc)
        return {"order_id": order_id, "status": "unknown"}

    if info["status"] == "filled":
        fp = info.get("filled_price") or info.get("price")
        update_order(order_id, "filled", filled_price=fp, filled_at=now_kst_str())

    elif info["status"] == "cancelled":
        update_order(order_id, "cancelled")

    return info


def get_summary(symbol: str) -> dict:
    """symbol의 미체결 주문 현황 요약을 반환한다."""
    open_orders = get_open_orders(symbol)
    return {
        "open_count":      len(open_orders),
        "open_buy_count":  sum(1 for o in open_orders if o["side"] == "buy"),
        "open_sell_count": sum(1 for o in open_orders if o["side"] == "sell"),
        "open_orders":     open_orders,
    }
