"""TelegramNotifier — 큐 기반 비동기 메시지 전송.

send() 는 즉시 리턴 (큐에 적재) — 트레이딩 루프를 절대 블로킹하지 않는다.
별도 sender 스레드가 rate-limit 및 3회 재시도를 처리한다.
"""

import queue
import threading
import time
from typing import Any, Optional

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
    build_virtual_catchup,
)
from utils.logger import get_logger

_log = get_logger("notification.telegram")

_API_URL   = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT   = 10      # HTTP 요청 타임아웃 (초)
_RATE_GAP  = 1.0     # 전송 최소 간격 (초)
_MAX_RETRY = 3
_QUEUE_MAX = 100     # 큐 최대 크기 (초과 시 오래된 메시지 폐기)


class TelegramNotifier:
    """Telegram Bot API 래퍼 — 비동기 큐 기반."""

    def __init__(self, bot_token: str, chat_id: str):
        self._token   = bot_token
        self._chat_id = str(chat_id)
        self._enabled = bool(bot_token and chat_id)
        self._queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=_QUEUE_MAX)
        self._last_sent: float = 0.0

        if self._enabled:
            self._sender_thread = threading.Thread(
                target=self._sender_loop,
                name="TelegramSender",
                daemon=True,
            )
            self._sender_thread.start()
            _log.info("텔레그램 비동기 sender 시작 (큐 용량=%d)", _QUEUE_MAX)
        else:
            _log.warning("텔레그램 비활성 (토큰 또는 CHAT_ID 미설정)")

    # ── 핵심 전송 ─────────────────────────────────────────────────────────────

    def send(self, text: str) -> bool:
        """메시지를 큐에 적재한다. 즉시 리턴 (블로킹 없음).

        큐가 가득 찬 경우에만 False 반환.
        """
        if not self._enabled:
            return False
        try:
            self._queue.put_nowait(text)
            return True
        except queue.Full:
            _log.warning("텔레그램 큐 가득 참 — 메시지 폐기 (%.40s...)", text.replace("\n", " "))
            return False

    # ── 편의 메서드 ───────────────────────────────────────────────────────────

    def send_bot_started(
        self, symbol: str,
        trigger_mode: str = "pct", trigger_pct: float = 0.5,
        trigger_krw: float = 0.0, order_amount: float = 50000,
        max_rounds: int = 50, start_price: float = 0.0, **_: Any,
    ) -> bool:
        return self.send(build_bot_started(
            symbol, trigger_mode, trigger_pct, trigger_krw,
            order_amount, max_rounds, start_price,
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

    def send_virtual_catchup(
        self, symbol: str, count: int = 0, gap: int = 0,
        from_price: float = 0, current_price: float = 0, **_: Any
    ) -> bool:
        return self.send(build_virtual_catchup(symbol, count, gap, from_price, current_price))

    def send_api_error(self, symbol: str, error: str = "", **_: Any) -> bool:
        return self.send(build_api_error(symbol, error))

    def send_hourly_status(self, hour: int, statuses: list[dict]) -> bool:
        return self.send(build_hourly_status(hour, statuses))

    def send_daily_report(
        self, date: str, symbol_data: list[dict],
        total_krw: float = 0, balances: dict | None = None,
        prices: dict | None = None,
    ) -> bool:
        return self.send(build_daily_report(date, symbol_data, total_krw, balances or {}, prices or {}))

    # ── 내부 ─────────────────────────────────────────────────────────────────

    def _sender_loop(self) -> None:
        """백그라운드 스레드: 큐에서 메시지를 꺼내 rate-limit + 재시도 전송."""
        while True:
            try:
                text = self._queue.get(timeout=60)
            except queue.Empty:
                continue

            if text is None:  # stop sentinel
                break

            elapsed = time.monotonic() - self._last_sent
            if elapsed < _RATE_GAP:
                time.sleep(_RATE_GAP - elapsed)

            if self._send_with_retry(text):
                self._last_sent = time.monotonic()
            else:
                _log.warning(
                    "텔레그램 최종 전송 실패 — 메시지 폐기: %.60s",
                    text.replace("\n", " "),
                )

    def _send_with_retry(self, text: str) -> bool:
        url     = _API_URL.format(token=self._token)
        payload = {"chat_id": self._chat_id, "text": text}

        for attempt in range(_MAX_RETRY):
            try:
                resp = requests.post(url, json=payload, timeout=_TIMEOUT)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                    _log.warning("텔레그램 rate limit — %d초 대기 (시도 %d/%d)", retry_after, attempt + 1, _MAX_RETRY)
                    time.sleep(retry_after)
                    continue
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
                time.sleep(2 ** attempt)  # 1 → 2 → (끝)

        _log.error("텔레그램 전송 최종 실패 (3회 소진)")
        return False
