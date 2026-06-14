"""ATR 기반 동적 트리거 % 계산 — 1시간 캐시."""

import time

from utils.logger import get_logger
from utils.math_utils import calc_atr, atr_to_trigger_pct

_log = get_logger("strategy.volatility")
_CACHE_TTL = 3600.0  # 1시간


class VolatilityMeter:
    """OHLCV 캔들 데이터로 ATR을 계산해 트리거 %를 반환한다."""

    def __init__(self, exchange, symbol: str, ticker: str, cfg: dict):
        self._exchange = exchange
        self._symbol = symbol
        self._ticker = ticker
        self._cfg = cfg
        self._cached_pct: float | None = None
        self._cached_at: float = 0.0

    def get_trigger_pct(self) -> float:
        """트리거 %를 반환한다. TTL 이내면 캐시값을 사용한다."""
        now = time.monotonic()
        if self._cached_pct is not None and (now - self._cached_at) < _CACHE_TTL:
            return self._cached_pct

        pct = self._compute()
        if pct is None:
            pct = self._fallback()

        self._cached_pct = pct
        self._cached_at = now
        return pct

    def invalidate(self) -> None:
        """캐시를 무효화한다. 다음 호출에서 재계산된다."""
        self._cached_at = 0.0

    # ── 내부 ─────────────────────────────────────────────────────────────────

    def _compute(self) -> float | None:
        try:
            candles = self._exchange.get_ohlcv(self._ticker, "minute60", count=20)
            if len(candles) < 2:
                return None
            highs  = [c["high"]  for c in candles]
            lows   = [c["low"]   for c in candles]
            closes = [c["close"] for c in candles]
            atr = calc_atr(highs, lows, closes)
            min_pct = self._cfg["strategy"]["trigger_min_pct"]
            max_pct = self._cfg["strategy"]["trigger_max_pct"]
            return atr_to_trigger_pct(atr, closes[-1], min_pct, max_pct)
        except Exception as exc:
            _log.warning("ATR 계산 실패 (%s): %s", self._symbol, exc)
            return None

    def _fallback(self) -> float:
        return float(self._cfg["pairs"][self._symbol]["trigger_pct"])
