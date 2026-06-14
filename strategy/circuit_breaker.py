"""슬라이딩 윈도우 기반 서킷브레이커 — 급변 감지 시 매수 중단."""

import time
from collections import deque
from typing import Optional

from database.state_repo import get_state, set_circuit_breaker
from utils.logger import get_logger

_log = get_logger("strategy.circuit_breaker")

_CB_COOLDOWN = 300.0  # 서킷브레이커 유지 시간 (초)


class CircuitBreaker:
    """±threshold% 급변을 window초 슬라이딩 윈도우로 감지한다."""

    def __init__(self, symbol: str, cfg: dict):
        self._symbol = symbol
        self._threshold = float(cfg["strategy"]["circuit_breaker_pct"])
        self._window = float(cfg["strategy"]["circuit_breaker_window"])
        self._prices: deque[tuple[float, float]] = deque()  # (monotonic, price)
        self._active = False
        self._active_until: float = 0.0
        self._restore_from_db()

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def update(self, price: float) -> bool:
        """새 가격을 반영하고 현재 서킷브레이커 활성 여부를 반환한다."""
        now = time.monotonic()
        self._expire(now)
        self._push(now, price)
        if not self._active:
            self._detect(now, price)
        return self._active

    def is_active(self) -> bool:
        """현재 서킷브레이커 상태를 반환한다."""
        self._expire(time.monotonic())
        return self._active

    def reset(self) -> None:
        """서킷브레이커를 강제 해제한다 (테스트/관리용)."""
        self._active = False
        self._active_until = 0.0
        self._prices.clear()
        set_circuit_breaker(self._symbol, False)

    # ── 내부 ─────────────────────────────────────────────────────────────────

    def _expire(self, now: float) -> None:
        if self._active and now >= self._active_until:
            self._active = False
            set_circuit_breaker(self._symbol, False)
            _log.info("서킷브레이커 자동 해제: %s", self._symbol)

    def _push(self, now: float, price: float) -> None:
        self._prices.append((now, price))
        while self._prices and now - self._prices[0][0] > self._window:
            self._prices.popleft()

    def _detect(self, now: float, price: float) -> None:
        if len(self._prices) < 2:
            return
        oldest_price = self._prices[0][1]
        if oldest_price <= 0:
            return
        change_pct = abs(price - oldest_price) / oldest_price * 100.0
        if change_pct >= self._threshold:
            self._active = True
            self._active_until = now + _CB_COOLDOWN
            set_circuit_breaker(self._symbol, True)
            _log.warning(
                "서킷브레이커 발동: %s (변동 %.2f%%, %.0f초 유지)",
                self._symbol, change_pct, _CB_COOLDOWN,
            )

    def _restore_from_db(self) -> None:
        """재시작 시 서킷브레이커를 초기화 상태로 복구한다."""
        try:
            state = get_state(self._symbol)
            if state and state.get("circuit_breaker_on"):
                set_circuit_breaker(self._symbol, False)
        except Exception as exc:
            _log.debug("서킷브레이커 DB 복구 실패: %s", exc)
