"""주문 실행 — 잔고 확인 → 가격 정규화 → API 주문 → DB 저장."""

from database.order_repo import save_order
from exchange.base import ExchangeBase
from strategy.trigger import buy_order_price, sell_order_price
from utils.logger import get_logger
from utils.math_utils import calc_quantity, round_price

_log = get_logger("order.executor")


class InsufficientBalanceError(Exception):
    """잔고 부족 예외."""


def execute_buy(
    symbol: str,
    ticker: str,
    exchange: ExchangeBase,
    reference_price: float,
    trigger_pct: float,
    order_amount: float,
    round_no: int,
) -> dict:
    """잔고 확인 → 매수 지정가 주문 실행 → DB 저장.

    Returns:
        {"order_id", "price", "quantity", "amount", "side", "status"}
    """
    price    = round_price(buy_order_price(reference_price, trigger_pct))
    quantity = calc_quantity(order_amount, price)

    krw_balance = exchange.get_balance("KRW")
    required = price * quantity
    if krw_balance < required:
        raise InsufficientBalanceError(
            f"KRW 잔고 부족: 필요 {required:,.0f}원, 보유 {krw_balance:,.0f}원"
        )

    result   = exchange.buy_limit(ticker, price, quantity)
    order_id = result["order_id"]
    amount   = round(price * quantity, 2)

    save_order(symbol, order_id, "buy", price, quantity, amount, round_no)
    _log.info("매수 주문 완료: %s %s 가격=%d 수량=%s 차수=%d",
              symbol, order_id, price, quantity, round_no)

    return {"order_id": order_id, "price": price, "quantity": quantity,
            "amount": amount, "side": "buy", "status": "open"}


def execute_sell(
    symbol: str,
    ticker: str,
    exchange: ExchangeBase,
    reference_price: float,
    trigger_pct: float,
    quantity: float,
    round_no: int,
    sell_extra_pct: float = 0.08,
) -> dict:
    """잔고 확인 → 매도 지정가 주문 실행 → DB 저장.

    Returns:
        {"order_id", "price", "quantity", "amount", "side", "status"}
    """
    price    = round_price(sell_order_price(reference_price, trigger_pct, sell_extra_pct))
    currency = ticker.split("-")[1]   # "KRW-ETH" → "ETH"

    coin_balance = exchange.get_balance(currency)
    if coin_balance < quantity:
        raise InsufficientBalanceError(
            f"{currency} 잔고 부족: 필요 {quantity}, 보유 {coin_balance}"
        )

    result   = exchange.sell_limit(ticker, price, quantity)
    order_id = result["order_id"]
    amount   = round(price * quantity, 2)

    save_order(symbol, order_id, "sell", price, quantity, amount, round_no)
    _log.info("매도 주문 완료: %s %s 가격=%d 수량=%s 차수=%d",
              symbol, order_id, price, quantity, round_no)

    return {"order_id": order_id, "price": price, "quantity": quantity,
            "amount": amount, "side": "sell", "status": "open"}
