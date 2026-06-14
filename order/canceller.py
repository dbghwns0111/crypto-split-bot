"""미체결 주문 자동 취소 — 타임아웃 초과 및 전체 취소."""

from database.order_repo import get_open_orders, update_order
from exchange.base import ExchangeBase
from utils.logger import get_logger
from utils.time_utils import elapsed_seconds, parse_kst

_log = get_logger("order.canceller")


def cancel_timed_out(
    symbol: str,
    exchange: ExchangeBase,
    timeout_seconds: int = 300,
) -> list[str]:
    """timeout_seconds 초과 미체결 주문을 취소하고 취소된 order_id 목록을 반환한다.

    API 취소 실패 시에도 DB는 'cancelled' 로 처리한다 (fallback).
    """
    open_orders = get_open_orders(symbol)
    cancelled: list[str] = []

    for order in open_orders:
        try:
            created = parse_kst(order["created_at"])
        except Exception:
            continue

        elapsed = elapsed_seconds(created)
        if elapsed <= timeout_seconds:
            continue

        oid = order["order_id"]
        _cancel_one(oid, exchange)
        cancelled.append(oid)
        _log.info("타임아웃 취소: %s %s (%.0f초 경과)", symbol, oid, elapsed)

    return cancelled


def cancel_all(symbol: str, exchange: ExchangeBase) -> list[str]:
    """symbol의 미체결 주문 전체를 취소하고 취소된 order_id 목록을 반환한다.

    API 취소 실패 시에도 DB는 'cancelled' 로 처리한다 (fallback).
    """
    open_orders = get_open_orders(symbol)
    cancelled: list[str] = []

    for order in open_orders:
        oid = order["order_id"]
        _cancel_one(oid, exchange)
        cancelled.append(oid)

    if cancelled:
        _log.info("전체 취소: %s → %d건", symbol, len(cancelled))

    return cancelled


def _cancel_one(order_id: str, exchange: ExchangeBase) -> None:
    """단일 주문을 취소한다. API 실패 여부와 무관하게 DB를 갱신한다."""
    try:
        exchange.cancel_order(order_id)
    except Exception as exc:
        _log.warning("취소 API 실패 (DB만 처리): %s — %s", order_id, exc)
    update_order(order_id, "cancelled")
