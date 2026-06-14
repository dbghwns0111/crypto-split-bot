"""TelegramNotifier — 메시지 전송, rate limit, 3회 재시도."""

import time
from threading import Lock
from typing import Any

import requests

from notification.message_builder import (
    build_api_error,
    build_bot_restarted,
    build_bot_started,
    build_bot_stopped,
    build_buy_filled,
    build_catchup,
    build_daily_report,
    build_hourly_status,
    build_max_round,
    build_order_cancelled,
    build_sell_filled,
)
from utils.logger import get_logger

_log = get_logger("notification.telegram")

_API_URL  = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT  = 10      # HTTP 요청 타임아웃 (초)
_RATE_GAP = 1.0     # 전송 최소 간격 (초)
_MAX_RETRY = 3


class TelegramNotifier:
    """Telegram Bot API 래퍼."""

    def __init__(self, bot_token: str, chat_id: str):
        self._token   = bot_token
        self._chat_id = str(chat_id)
        self._enabled = bool(bot_token and chat_id)
        self._lock     = Lock()
        self._last_sent: float = 0.0

        if not self._enabled:
            _log.warning("텔레그램 비활성 (토큰 또는 CHAT_ID 미설정)")

    # ── 핵심 전송 ─────────────────────────────────────────────────────────────

    def send(self, text: str) -> bool:
        """텍스트 메시지를 전송한다. 비활성 상태면 False를 반환한다."""
        if not self._enabled:
            return False

        with self._lock:
            self._rate_wait()
            success = self._send_with_retry(text)
            if success:
                self._last_sent = time.monotonic()
            return success

    # ── 편의 메서드 ───────────────────────────────────────────────────────────

    def send_bot_started(
        self, symbol: str,
        trigger_mode: str = "pct", trigger_pct: float = 0.5,
        trigger_krw: float = 0.0, order_amount: float = 50000,
        max_rounds: int = 50, **_: Any,
    ) -> bool:
        return self.send(build_bot_started(
            symbol, trigger_mode, trigger_pct, trigger_krw, order_amount, max_rounds,
        ))

    def send_bot_restarted(
        self, symbol: str, current_round: int, reference_price: float,
        trigger_mode: str = "pct", trigger_pct: float = 0.5,
        trigger_krw: float = 0.0, order_amount: float = 50000, **_: Any,
    ) -> bool:
        return self.send(build_bot_restarted(
            symbol, current_round, reference_price,
            trigger_mode, trigger_pct, trigger_krw, order_amount,
        ))

    def send_bot_stopped(
        self, symbol: str,
        current_round: int = 0, reference_price: float = 0.0,
        cancelled_orders: int = 0,
    ) -> bool:
        return self.send(build_bot_stopped(symbol, current_round, reference_price, cancelled_orders))

    def send_buy_filled(
        self, symbol: str, price: float = 0,
        qty: float = 0, round_no: int = 0, **_: Any
    ) -> bool:
        return self.send(build_buy_filled(symbol, price, qty, round_no))

    def send_sell_filled(
        self, symbol: str, price: float = 0, qty: float = 0,
        round_no: int = 0, net_pnl: float = 0, buy_price: float = 0, **_: Any
    ) -> bool:
        return self.send(build_sell_filled(symbol, price, qty, round_no, net_pnl, buy_price))

    def send_max_round(
        self, symbol: str, round_no: int = 0, max_rounds: int = 50, **_: Any
    ) -> bool:
        return self.send(build_max_round(symbol, round_no, max_rounds))

    def send_order_cancelled(
        self, symbol: str, order_id: str = "", side: str = "buy",
        price: float = 0, elapsed: float = 0, **_: Any
    ) -> bool:
        return self.send(build_order_cancelled(symbol, order_id, side, price, elapsed))

    def send_catchup(
        self, symbol: str, count: int = 0, gap: int = 0, price: float = 0, **_: Any
    ) -> bool:
        return self.send(build_catchup(symbol, count, gap, price))

    def send_api_error(self, symbol: str, error: str = "", **_: Any) -> bool:
        return self.send(build_api_error(symbol, error))

    def send_hourly_status(self, hour: int, statuses: list[dict]) -> bool:
        return self.send(build_hourly_status(hour, statuses))

    def send_daily_report(
        self, date: str, symbol_data: list[dict],
        total_krw: float = 0, balances: dict | None = None
    ) -> bool:
        return self.send(build_daily_report(date, symbol_data, total_krw, balances or {}))

    # ── 내부 ─────────────────────────────────────────────────────────────────

    def _rate_wait(self) -> None:
        elapsed = time.monotonic() - self._last_sent
        if elapsed < _RATE_GAP:
            time.sleep(_RATE_GAP - elapsed)

    def _send_with_retry(self, text: str) -> bool:
        url = _API_URL.format(token=self._token)
        payload = {"chat_id": self._chat_id, "text": text}

        for attempt in range(_MAX_RETRY):
            try:
                resp = requests.post(url, json=payload, timeout=_TIMEOUT)
                if resp.status_code == 200:
                    return True
                _log.warning(
                    "텔레그램 전송 실패 (HTTP %d, 시도 %d/%d)",
                    resp.status_code, attempt + 1, _MAX_RETRY,
                )
            except requests.RequestException as exc:
                _log.error(
                    "텔레그램 요청 오류 (시도 %d/%d): %s",
                    attempt + 1, _MAX_RETRY, exc,
                )

            if attempt < _MAX_RETRY - 1:
                time.sleep(2 ** attempt)   # 1 → 2 → (끝)

        _log.error("텔레그램 전송 최종 실패 (3회 소진)")
        return False
