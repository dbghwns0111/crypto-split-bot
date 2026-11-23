"""python-bithumb(python_bithumb) 래퍼 — ExchangeBase 구현."""

import functools
import json
import threading
import time
from typing import Optional

import requests

from exchange.base import ExchangeBase
from exchange.errors import BithumbResponseError
from exchange.retry import with_retry
from utils.logger import get_logger

_log = get_logger("exchange.bithumb")
# 실패한 호출의 빗썸 원본 응답(상태·헤더·본문) 기록 — 점검·장애 때 무엇이 왔는지 사후 확인용.
_bad_log = get_logger("exchange.bad_response")

# 재시도 대상 예외 — 지연 import 후 확정
_RETRY_EXC = (Exception,)

# python_bithumb은 requests 호출에 timeout을 전혀 주지 않아서, 네트워크가 잠깐
# 끊기면 요청이 응답 없이 무한정 매달린다. 웹 요청 스레드가 그렇게 묶이면 화면
# 로딩이 끝나지 않고 버튼도 반응하지 않는다. (connect, read) 초.
_PUBLIC_TIMEOUT      = (3.05, 5)
_PRIVATE_GET_TIMEOUT = (3.05, 10)
# 주문/취소(POST·DELETE)는 읽기 타임아웃을 걸지 않는다 — 요청이 거래소에 도달한
# 뒤 응답만 늦는 경우 타임아웃으로 끊으면 "주문은 들어갔는데 실패로 처리"되는
# 애매한 상태가 생긴다. 연결 단계 타임아웃은 요청이 아예 안 나간 것이라 안전하다.
_PRIVATE_WRITE_TIMEOUT = (3.05, None)


class _TimeoutRequests:
    """python_bithumb 모듈의 `requests` 자리에 끼워 기본 timeout을 주입하는 대리 객체.

    라이브러리 소스를 수정하지 않으므로 pip 업그레이드에 영향받지 않는다
    (utils/ntp_clock.py의 time 패치와 같은 방식). 호출자가 timeout을 직접 주면 그대로 쓴다.
    """

    def __init__(self, get_timeout, write_timeout):
        self._get_timeout   = get_timeout
        self._write_timeout = write_timeout

    def request(self, method, url, **kwargs):
        default = self._get_timeout if str(method).upper() == "GET" else self._write_timeout
        kwargs.setdefault("timeout", default)
        resp = requests.request(method, url, **kwargs)
        # 호출 스레드의 마지막 응답을 남겨 둔다 — 라이브러리가 응답을 해석하다 터지면
        # _explain_failures 가 이 원본을 기록·요약한다 (요청 헤더/JWT 는 보관하지 않음).
        _tls.last_response = resp
        return resp

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def __getattr__(self, name):
        return getattr(requests, name)


def _install_http_timeouts() -> None:
    """python_bithumb의 public/private API에 기본 HTTP timeout을 적용한다 (멱등)."""
    try:
        from python_bithumb import private_api, public_api
    except ImportError:
        return
    if not isinstance(public_api.requests, _TimeoutRequests):
        public_api.requests = _TimeoutRequests(_PUBLIC_TIMEOUT, _PUBLIC_TIMEOUT)
    if not isinstance(private_api.requests, _TimeoutRequests):
        private_api.requests = _TimeoutRequests(_PRIVATE_GET_TIMEOUT, _PRIVATE_WRITE_TIMEOUT)


# ── 실패 응답 기록 ────────────────────────────────────────────────────────────
# python_bithumb 은 응답 형식을 검증하지 않는다. 빗썸이 점검·장애 중 HTTP 200 에
# 평소와 다른 JSON 을 주면 `string indices must be integers` 같은 엉뚱한 오류만
# 남고 실제로 무엇이 왔는지는 사라진다(2026-11-23 01:00~07:30 장애). 그래서
# 래퍼 메서드가 실패하면 그 호출이 받은 원본 응답을 exchange.bad_response 로그에
# 남기고, 형식 문제면 응답 요약을 담은 BithumbResponseError 로 바꿔 던진다.

_tls = threading.local()

# 해석 실패로 보는 예외 — 응답이 기대한 구조가 아닐 때 라이브러리/래퍼에서 나는 것들
_PARSE_ERRORS = (TypeError, KeyError, AttributeError, IndexError,
                 json.JSONDecodeError, requests.exceptions.JSONDecodeError)

_SNIPPET_LEN   = 200    # 오류 메시지(텔레그램)에 붙이는 본문 길이
_DUMP_BODY_LEN = 4000   # 로그 파일에 남기는 본문 길이
_DUMP_REPEAT_SEC = 600  # 같은 응답이 반복되면 이 간격으로만 다시 전체 기록
_DUMP_HEADERS = ("Content-Type", "Content-Length", "Date", "Server",
                 "Retry-After", "Location", "CF-RAY")

_dump_lock = threading.Lock()
_dump_state = {"key": None, "at": 0.0, "suppressed": 0}


def _body_text(resp) -> str:
    try:
        return resp.text or ""
    except Exception as exc:  # 디코딩 실패 등
        return f"<본문 읽기 실패: {exc}>"


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _dump_response(func_name: str, resp, exc: BaseException) -> None:
    """실패한 호출의 원본 응답을 기록한다. 같은 응답 반복은 10분에 한 번만 전체 기록."""
    body = _body_text(resp)
    req = getattr(resp, "request", None)
    method = getattr(req, "method", "?")
    url = getattr(resp, "url", "?")
    key = (resp.status_code, body[:500])
    now = time.monotonic()
    with _dump_lock:
        st = _dump_state
        if st["key"] == key and now - st["at"] < _DUMP_REPEAT_SEC:
            st["suppressed"] += 1
            return
        suppressed = st["suppressed"]
        st.update(key=key, at=now, suppressed=0)
    headers = {h: resp.headers[h] for h in _DUMP_HEADERS if h in resp.headers}
    _bad_log.error(
        "%s 실패 — %s: %s\n  요청: %s %s\n  HTTP %s, 헤더: %s\n  본문(%d자): %s%s",
        func_name, type(exc).__name__, exc, method, url,
        resp.status_code, headers, len(body), body[:_DUMP_BODY_LEN],
        f"\n  (직전과 같은 응답 {suppressed}회는 기록 생략)" if suppressed else "",
    )


def _note_failure(func_name: str, exc: BaseException) -> BaseException:
    """실패 원인을 기록하고, 응답 형식 문제면 설명이 붙은 예외를 돌려준다."""
    if isinstance(exc, BithumbResponseError):
        return exc
    resp = getattr(_tls, "last_response", None)
    if resp is None:            # 응답 자체가 없음(연결 실패·타임아웃) — 원래 메시지로 충분
        return exc
    try:
        _dump_response(func_name, resp, exc)
    except Exception as log_exc:
        _log.warning("실패 응답 기록 중 오류: %s", log_exc)
    if isinstance(exc, _PARSE_ERRORS):
        snippet = _one_line(_body_text(resp), _SNIPPET_LEN)
        return BithumbResponseError(
            f"빗썸 응답 형식 이상(점검·장애 가능성) — HTTP {resp.status_code}, "
            f"응답: {snippet or '(빈 본문)'} [{type(exc).__name__}: {exc}]"
        )
    return exc


def _explain_failures(func):
    """래퍼 메서드용 — 실패 시 원본 응답 기록 + 형식 이상을 알아볼 수 있는 예외로 변환."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _tls.last_response = None
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            new = _note_failure(func.__name__, exc)
            if new is exc:
                raise
            raise new from exc
    return wrapper


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
        _install_http_timeouts()
        # 최근 조회된 시세 — 봇 루프가 1초마다 받아오는 값을 웹 화면이 재사용한다
        # (웹 요청마다 거래소를 다시 부르지 않도록). ticker → (price, monotonic_ts)
        self._last_prices: dict[str, tuple[float, float]] = {}
        self._last_prices_lock = threading.Lock()
        # JWT 타임스탬프 NTP 보정 재적용(멱등) — main.py 시작 시점에 라이브러리 import가
        # 아직 안 됐거나 실패했을 경우의 안전망. 보정이 꺼져 있으면 아무것도 하지 않는다.
        try:
            from utils import ntp_clock
            if ntp_clock.is_synced():
                ntp_clock.install_bithumb_time_patch()
        except Exception as exc:
            _log.warning("JWT 시각 보정 재적용 실패: %s", exc)
        self._private = self._pb.Bithumb(access_key, secret_key)
        if not self._has_credentials:
            _log.warning("API 키가 설정되지 않았습니다 — .env 파일에 BITHUMB_ACCESS_KEY / BITHUMB_SECRET_KEY를 입력하세요")
        else:
            _log.info("BithumbClient 초기화 완료")

    # ── 공개 API ──────────────────────────────────────────────────────────────

    @with_retry(max_retries=3, base_delay=1.0)
    @_explain_failures
    def get_current_price(self, ticker: str) -> float:
        """현재가를 반환한다."""
        price = self._pb.get_current_price(ticker)
        if price is None:
            raise ValueError(f"현재가 조회 실패: {ticker}")
        price = float(price)
        self._remember_prices({ticker: price})
        return price

    @_explain_failures
    def get_current_prices(self, tickers: list[str]) -> dict[str, float]:
        """여러 종목 현재가를 HTTP 1회로 조회한다 — 웹 화면용, 재시도 없이 빠르게 실패.

        Returns: {ticker: price}
        """
        raw = self._pb.get_current_price(list(tickers))
        if isinstance(raw, dict):
            prices = {t: float(p) for t, p in raw.items() if p is not None}
        elif raw is not None and len(tickers) == 1:
            prices = {tickers[0]: float(raw)}
        else:
            prices = {}
        if not prices:
            raise ValueError(f"현재가 일괄 조회 실패: {tickers}")
        self._remember_prices(prices)
        return prices

    def peek_price(self, ticker: str, max_age: float) -> Optional[float]:
        """max_age초 이내에 조회된 시세가 있으면 반환한다 (네트워크 호출 없음)."""
        with self._last_prices_lock:
            entry = self._last_prices.get(ticker)
        if entry and time.monotonic() - entry[1] < max_age:
            return entry[0]
        return None

    def _remember_prices(self, prices: dict[str, float]) -> None:
        now = time.monotonic()
        with self._last_prices_lock:
            for t, p in prices.items():
                self._last_prices[t] = (p, now)

    @with_retry(max_retries=3, base_delay=1.0)
    @_explain_failures
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
    @_explain_failures
    def get_balance(self, currency: str) -> float:
        """currency 주문 가능 잔고(float)를 반환한다."""
        self._require_credentials()
        return float(self._private.get_balance(currency))

    @with_retry(max_retries=3, base_delay=1.0)
    @_explain_failures
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
    @_explain_failures
    def buy_limit(self, ticker: str, price: float, quantity: float) -> dict:
        """지정가 매수 주문을 실행한다."""
        raw = self._private.buy_limit_order(ticker, price=price, volume=quantity)
        _log.info("매수 주문: %s 가격=%s 수량=%s → order_id=%s",
                  ticker, price, quantity, raw.get("uuid"))
        return _normalize_order(raw)

    @with_retry(max_retries=0)
    @_explain_failures
    def sell_limit(self, ticker: str, price: float, quantity: float) -> dict:
        """지정가 매도 주문을 실행한다."""
        raw = self._private.sell_limit_order(ticker, price=price, volume=quantity)
        _log.info("매도 주문: %s 가격=%s 수량=%s → order_id=%s",
                  ticker, price, quantity, raw.get("uuid"))
        return _normalize_order(raw)

    @with_retry(max_retries=3, base_delay=1.0)
    @_explain_failures
    def get_order_status(self, order_id: str) -> dict:
        """주문 상태를 조회한다."""
        raw = self._private.get_order(order_id)
        return _normalize_order(raw)

    @with_retry(max_retries=3, base_delay=1.0)
    def cancel_order(self, order_id: str) -> bool:
        """주문을 취소한다. 성공 시 True."""
        _tls.last_response = None
        try:
            raw = self._private.cancel_order(order_id)
            state = raw.get("state")
            # 빗썸 취소는 비동기 — 응답 state 가 아직 'wait'여도 실패가 아니라 "접수"다.
            # 최종 결과는 order.canceller.cancel_confirmed() 가 주문 조회로 확인한다.
            _log.info("주문 취소 요청: %s → %s", order_id,
                      "완료" if state == "cancel" else f"접수(state={state})")
            return state in ("cancel", "wait")
        except Exception as exc:
            exc = _note_failure("cancel_order", exc)
            _log.warning("주문 취소 실패 (order_id=%s): %s", order_id, exc)
            return False

    @with_retry(max_retries=3, base_delay=1.0)
    @_explain_failures
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
