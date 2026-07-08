"""python-bithumb(python_bithumb) 래퍼 — ExchangeBase 구현."""

from typing import Optional

from exchange.base import ExchangeBase
from exchange.retry import with_retry
from utils.logger import get_logger

_log = get_logger("exchange.bithumb")

# 재시도 대상 예외 — 지연 import 후 확정
_RETRY_EXC = (Exception,)


def _import_bithumb():
    """python_bithumb 패키지를 지연 import한다."""
    try:
        import python_bithumb as pb
        return pb
    except ImportError as e:
        raise ImportError(
            "python-bithumb 패키지를 찾을 수 없습니다. "
            "pip install python-bithumb 로 설치하세요."
        ) from e


class BithumbClient(ExchangeBase):
    """python_bithumb 래퍼 클라이언트."""

    def __init__(self, access_key: str, secret_key: str):
        self._access_key = access_key
        self._secret_key = secret_key
        self._has_credentials = bool(access_key and secret_key)
        self._pb = _import_bithumb()
        self._private = self._pb.Bithumb(access_key, secret_key)
        if not self._has_credentials:
            _log.warning("API 키가 설정되지 않았습니다 — .env 파일에 BITHUMB_ACCESS_KEY / BITHUMB_SECRET_KEY를 입력하세요")
        else:
            _log.info("BithumbClient 초기화 완료")

    # ── 공개 API ──────────────────────────────────────────────────────────────

    @with_retry(max_retries=3, base_delay=1.0)
    def get_current_price(self, ticker: str) -> float:
        """현재가를 반환한다."""
        price = self._pb.get_current_price(ticker)
        if price is None:
            raise ValueError(f"현재가 조회 실패: {ticker}")
        return float(price)

    @with_retry(max_retries=3, base_delay=1.0)
    def get_ohlcv(self, ticker: str, interval: str = "minute60", count: int = 20) -> list[dict]:
        """OHLCV 캔들 데이터를 반환한다. 최신 캔들이 마지막 원소."""
        df = self._pb.get_ohlcv(ticker, interval=interval, count=count)
        if df is None or df.empty:
            return []
        return [
            {
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row["volume"]),
            }
            for _, row in df.iterrows()
        ]

    def _require_credentials(self) -> None:
        if not self._has_credentials:
            raise ValueError(
                "API 키가 설정되지 않았습니다. "
                "프로젝트 루트의 .env 파일에 BITHUMB_ACCESS_KEY와 BITHUMB_SECRET_KEY를 입력하세요."
            )

    # ── 사설 API ──────────────────────────────────────────────────────────────

    @with_retry(max_retries=3, base_delay=1.0)
    def get_balance(self, currency: str) -> float:
        """currency 주문 가능 잔고(float)를 반환한다."""
        self._require_credentials()
        return float(self._private.get_balance(currency))

    @with_retry(max_retries=3, base_delay=1.0)
    def get_balances(self) -> list[dict]:
        """전체 잔고 목록을 반환한다."""
        self._require_credentials()
        raw = self._private.get_balances()
        return [
            {
                "currency": b.get("currency", ""),
                "balance":  float(b.get("balance", 0) or 0),
                "locked":   float(b.get("locked", 0) or 0),
            }
            for b in (raw or [])
            if b.get("currency")
        ]

    # 주문 배치는 재시도하지 않는다 — 타임아웃 등 애매한 실패 시 자동
    # 재시도하면 실제로는 접수된 주문에 중복 주문을 낼 위험이 있다.
    # (인증 오류는 with_retry 내부에서 여전히 즉시 raise된다)
    @with_retry(max_retries=0)
    def buy_limit(self, ticker: str, price: float, quantity: float) -> dict:
        """지정가 매수 주문을 실행한다."""
        raw = self._private.buy_limit_order(ticker, price=price, volume=quantity)
        _log.info("매수 주문: %s 가격=%s 수량=%s → order_id=%s",
                  ticker, price, quantity, raw.get("uuid"))
        return _normalize_order(raw)

    @with_retry(max_retries=0)
    def sell_limit(self, ticker: str, price: float, quantity: float) -> dict:
        """지정가 매도 주문을 실행한다."""
        raw = self._private.sell_limit_order(ticker, price=price, volume=quantity)
        _log.info("매도 주문: %s 가격=%s 수량=%s → order_id=%s",
                  ticker, price, quantity, raw.get("uuid"))
        return _normalize_order(raw)

    @with_retry(max_retries=3, base_delay=1.0)
    def get_order_status(self, order_id: str) -> dict:
        """주문 상태를 조회한다."""
        raw = self._private.get_order(order_id)
        return _normalize_order(raw)

    @with_retry(max_retries=3, base_delay=1.0)
    def cancel_order(self, order_id: str) -> bool:
        """주문을 취소한다. 성공 시 True."""
        try:
            raw = self._private.cancel_order(order_id)
            cancelled = raw.get("state") == "cancel"
            _log.info("주문 취소: %s → %s", order_id, "성공" if cancelled else "실패")
            return cancelled
        except Exception as exc:
            _log.warning("주문 취소 실패 (order_id=%s): %s", order_id, exc)
            return False

    @with_retry(max_retries=3, base_delay=1.0)
    def get_open_orders(self, ticker: str) -> list[dict]:
        """ticker의 미체결 주문 목록을 반환한다."""
        raw_list = self._private.get_orders(market=ticker, state="wait")
        return [_normalize_order(r) for r in (raw_list or [])]


# ── 응답 정규화 헬퍼 ───────────────────────────────────────────────────────────

def _normalize_order(raw: dict) -> dict:
    """API 응답 dict를 표준 주문 dict로 변환한다."""
    state = raw.get("state", "wait")
    if state == "done":
        status = "filled"
    elif state == "cancel":
        status = "cancelled"
    else:
        status = "open"

    executed_vol = float(raw.get("executed_volume", 0) or 0)
    filled_price: Optional[float] = None

    trades = raw.get("trades") or []
    if trades and executed_vol > 0:
        total_paid = sum(float(t.get("price", 0)) * float(t.get("volume", 0)) for t in trades)
        filled_price = total_paid / executed_vol
    elif status == "filled":
        filled_price = float(raw.get("price", 0) or 0)

    side_raw = raw.get("side", "bid")
    side = "buy" if side_raw == "bid" else "sell"

    return {
        "order_id":        raw.get("uuid", ""),
        "status":          status,
        "side":            side,
        "price":           float(raw.get("price", 0) or 0),
        "quantity":        float(raw.get("volume", 0) or 0),
        "filled_quantity": executed_vol,
        "filled_price":    filled_price,
    }
