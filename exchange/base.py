"""거래소 클라이언트 추상 인터페이스."""

from abc import ABC, abstractmethod


class ExchangeBase(ABC):
    """모든 거래소 클라이언트가 구현해야 하는 인터페이스."""

    @abstractmethod
    def get_current_price(self, ticker: str) -> float:
        """현재가를 반환한다. (예: ticker="KRW-ETH")"""

    @abstractmethod
    def get_balance(self, currency: str) -> float:
        """currency의 주문 가능 잔고(float)를 반환한다. (예: currency="KRW")"""

    @abstractmethod
    def get_balances(self) -> list[dict]:
        """전체 잔고 목록을 반환한다. [{"currency": str, "balance": float, ...}, ...]"""

    @abstractmethod
    def buy_limit(self, ticker: str, price: float, quantity: float) -> dict:
        """지정가 매수 주문을 실행하고 표준 주문 dict를 반환한다.

        Returns:
            {"order_id": str, "status": "open", "price": float,
             "quantity": float, "side": "buy"}
        """

    @abstractmethod
    def sell_limit(self, ticker: str, price: float, quantity: float) -> dict:
        """지정가 매도 주문을 실행하고 표준 주문 dict를 반환한다."""

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """주문 상태를 조회하고 표준 주문 dict를 반환한다.

        Returns:
            {"order_id": str,
             "status": "open" | "filled" | "cancelled",
             "filled_price": float | None,
             "filled_quantity": float,
             "price": float,
             "quantity": float,
             "side": "buy" | "sell"}
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """주문을 취소한다. 성공 시 True, 실패 시 False."""

    @abstractmethod
    def get_open_orders(self, ticker: str) -> list[dict]:
        """ticker의 미체결 주문 목록을 반환한다."""

    @abstractmethod
    def get_ohlcv(self, ticker: str, interval: str = "minute60", count: int = 20) -> list[dict]:
        """OHLCV 캔들 데이터를 반환한다.

        Returns:
            [{"open": float, "high": float, "low": float,
              "close": float, "volume": float}, ...]
            최신 캔들이 마지막에 위치한다.
        """
