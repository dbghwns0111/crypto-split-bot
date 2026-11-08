"""미체결 주문 자동 취소 — 타임아웃 초과 및 전체 취소.

★ 취소는 반드시 cancel_confirmed() 로 "거래소에서 실제로 취소(또는 체결) 확정"을
확인한 뒤에만 DB에 반영한다. 예전에는 취소 API 호출 결과와 무관하게 DB를
'cancelled'로 바꾸고 다음 주문을 냈는데, 취소 요청이 타임아웃/JWT 만료로 실패하면
옛 주문이 거래소에 살아남아 미체결 주문이 여러 개 쌓였고, 그중 상당수가 나중에
체결됐는데도 DB에는 '취소'로 남아 차수·손익에서 누락됐다(2026-06~11 261건).
빗썸 취소 API는 비동기라 응답 state가 'wait'일 수 있으므로 응답값이 아니라
주문 조회로 최종 상태를 확인한다.
"""

import time
from typing import Optional

from database.order_repo import get_open_orders, update_order
from exchange.base import ExchangeBase
from utils.logger import get_logger
from utils.time_utils import elapsed_seconds, parse_kst

_log = get_logger("order.canceller")

# 테스트에서 대기 없이 돌리기 위해 교체 가능하게 둔다 (tests/conftest.py)
_sleep = time.sleep


def cancel_timed_out(
    symbol: str,
    exchange: ExchangeBase,
    timeout_seconds: int = 300,
) -> list[str]:
    """timeout_seconds 초과 미체결 주문을 취소하고 취소된 order_id 목록을 반환한다.

    취소가 확정된 주문만 DB 'cancelled' 처리하고 목록에 포함한다.
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
        if _cancel_one(oid, exchange):
            cancelled.append(oid)
        _log.info("타임아웃 취소: %s %s (%.0f초 경과)", symbol, oid, elapsed)

    return cancelled


def cancel_all(symbol: str, exchange: ExchangeBase) -> list[str]:
    """symbol의 미체결 주문 전체를 취소하고 취소된 order_id 목록을 반환한다.

    취소가 확정된 주문만 DB 'cancelled' 처리하고 목록에 포함한다.
    """
    open_orders = get_open_orders(symbol)
    cancelled: list[str] = []

    for order in open_orders:
        oid = order["order_id"]
        if _cancel_one(oid, exchange):
            cancelled.append(oid)

    if cancelled:
        _log.info("전체 취소: %s → %d건", symbol, len(cancelled))
    unresolved = len(open_orders) - len(cancelled)
    if unresolved:
        _log.warning("전체 취소: %s → %d건은 체결됐거나 취소 미확정(다음 시작 시 정산)",
                     symbol, unresolved)

    return cancelled


def cancel_confirmed(
    exchange: ExchangeBase,
    order_id: str,
    attempts: int = 6,
    interval: float = 1.0,
) -> Optional[dict]:
    """주문을 취소하고 거래소 최종 상태를 확인한다.

    Returns:
        최종 상태가 확정되면 get_order_status() 결과 dict
        (status == "cancelled" 또는 "filled" — 취소 직전에 체결됐을 수 있음,
        cancelled 여도 filled_quantity > 0 이면 부분 체결분이 있다).
        attempts 회 확인해도 아직 'open'이거나 조회 자체가 실패하면 None —
        이때 호출자는 주문이 아직 살아 있다고 간주해야 한다(DB를 cancelled로 바꾸거나
        새 주문을 내면 안 됨).
    """
    for attempt in range(attempts):
        try:
            info = exchange.get_order_status(order_id)
        except Exception as exc:
            _log.warning("취소 확인 조회 실패 (%s): %s", order_id, exc)
            info = None
        if info and info.get("status") in ("cancelled", "filled"):
            return info
        # 첫 시도 + 이후 2회마다 취소 요청 재전송 (요청 유실 대비, 중복 요청은 무해)
        if attempt % 2 == 0:
            try:
                exchange.cancel_order(order_id)
            except Exception as exc:
                _log.warning("취소 요청 실패 (%s): %s", order_id, exc)
        if interval > 0:
            _sleep(interval)
    try:
        info = exchange.get_order_status(order_id)
        if info and info.get("status") in ("cancelled", "filled"):
            return info
    except Exception as exc:
        _log.warning("취소 확인 조회 실패 (%s): %s", order_id, exc)
    _log.error("주문 취소 미확정 — 거래소에 아직 살아 있을 수 있음: %s", order_id)
    return None


def _cancel_one(order_id: str, exchange: ExchangeBase) -> bool:
    """단일 주문을 취소한다.

    체결 없이 취소가 확정된 경우에만 DB를 'cancelled'로 바꾸고 True를 반환한다.
    취소 직전 (부분)체결됐거나 취소가 확정되지 않으면 DB를 'open'으로 남겨둔다 —
    다음 봇 시작 시 MagicSplit._reconcile_open_orders()가 거래소 상태를 다시 조회해
    체결분을 차수·손익에 반영하고 남은 주문은 다시 취소한다.
    """
    info = cancel_confirmed(exchange, order_id)
    if info is None:
        _log.error("취소 미확정 — DB open 유지(다음 시작 시 재처리): %s", order_id)
        return False
    filled_qty = float(info.get("filled_quantity") or 0)
    if info["status"] == "cancelled" and filled_qty <= 0:
        update_order(order_id, "cancelled")
        return True
    _log.warning("취소 전 체결 발생(status=%s, 체결수량=%s) — DB open 유지, 다음 시작 시 정산: %s",
                 info["status"], filled_qty, order_id)
    return False
