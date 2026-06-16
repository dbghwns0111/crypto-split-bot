"""BotManager — ETH/USDT MagicSplit 봇 인스턴스 관리.

main.py 실행 시 초기화(봇은 중지 상태).
웹 대시보드의 start/stop 요청을 받아 봇을 제어한다.
"""

import threading

from order.canceller import cancel_all
from strategy.magic_split import MagicSplit
from utils.logger import get_logger

_log = get_logger("bot_manager")


class BotManager:
    """ETH/USDT MagicSplit 봇 인스턴스를 Thread-safe 하게 관리한다."""

    def __init__(self, exchange, cfg: dict, notifier=None):
        self._exchange = exchange
        self._cfg = cfg
        self._notifier = notifier
        self._lock = threading.Lock()
        self._bots: dict[str, MagicSplit] = {}
        self._init_bots()

    # ── 초기화 ────────────────────────────────────────────────────────────────

    def _init_bots(self) -> None:
        """활성화된 페어에 대해 봇 인스턴스를 생성한다 (중지 상태)."""
        for symbol, pair_cfg in self._cfg["pairs"].items():
            if pair_cfg.get("enabled", True):
                self._bots[symbol] = MagicSplit(
                    symbol, self._exchange, self._cfg, self._notifier
                )
                _log.info("봇 인스턴스 생성: %s (중지 상태)", symbol)

    # ── 시작 / 중지 ───────────────────────────────────────────────────────────

    def start(self, symbol: str, fresh: bool = False) -> tuple[bool, str]:
        """symbol 봇을 시작한다.

        Args:
            fresh: True이면 기존 상태를 초기화하고 처음부터 시작한다.

        Returns:
            (success: bool, message: str)
        """
        with self._lock:
            if symbol not in self._bots:
                return False, f"알 수 없는 심볼: {symbol}"
            bot = self._bots[symbol]
            if bot.is_running:
                return False, f"{symbol} 봇이 이미 실행 중입니다."
            try:
                if fresh:
                    cancel_all(symbol, self._exchange)
                    bot.reset_state()
                bot.start()
                _log.info("봇 시작: %s (fresh=%s)", symbol, fresh)
                if self._notifier:
                    pair         = self._cfg["pairs"][symbol]
                    trigger_mode = pair.get("trigger_mode", "pct")
                    trigger_pct  = float(pair.get("trigger_pct", 0.5))
                    trigger_krw  = float(pair.get("trigger_krw", 0.0))
                    order_amount = float(pair.get("order_amount", 50000))
                    max_rounds   = int(pair.get("max_rounds", 50))
                    if not fresh and bot.is_resume:
                        self._notifier.send_bot_restarted(
                            symbol, bot.current_round, bot.reference_price,
                            trigger_mode=trigger_mode, trigger_pct=trigger_pct,
                            trigger_krw=trigger_krw, order_amount=order_amount,
                        )
                    else:
                        start_price = bot.start_price
                        self._notifier.send_bot_started(
                            symbol,
                            trigger_mode=trigger_mode, trigger_pct=trigger_pct,
                            trigger_krw=trigger_krw, order_amount=order_amount,
                            max_rounds=max_rounds, start_price=start_price,
                        )
                return True, f"{symbol} 봇을 시작했습니다."
            except Exception as exc:
                _log.error("봇 시작 실패 (%s): %s", symbol, exc)
                return False, f"시작 실패: {exc}"

    def stop(self, symbol: str) -> tuple[bool, str]:
        """symbol 봇을 중지하고 미체결 주문을 전량 취소한다.

        Returns:
            (success: bool, message: str)
        """
        with self._lock:
            if symbol not in self._bots:
                return False, f"알 수 없는 심볼: {symbol}"
            bot = self._bots[symbol]
            if not bot.is_running:
                return False, f"{symbol} 봇이 실행 중이 아닙니다."
            try:
                current_round   = bot.current_round
                reference_price = bot.reference_price
                bot.stop()
                cancelled = cancel_all(symbol, self._exchange)
                msg = f"{symbol} 봇 중지 완료"
                if cancelled:
                    msg += f", 미체결 주문 {len(cancelled)}건 취소"
                _log.info(msg)
                if self._notifier:
                    self._notifier.send_bot_stopped(
                        symbol,
                        current_round=current_round,
                        reference_price=reference_price,
                        cancelled_orders=len(cancelled),
                    )
                return True, msg
            except Exception as exc:
                _log.error("봇 중지 실패 (%s): %s", symbol, exc)
                return False, f"중지 실패: {exc}"

    def stop_all(self) -> list[str]:
        """실행 중인 모든 봇을 중지한다. 중지된 symbol 목록을 반환한다."""
        stopped = []
        for symbol in list(self._bots.keys()):
            if self._bots[symbol].is_running:
                ok, _ = self.stop(symbol)
                if ok:
                    stopped.append(symbol)
        if stopped:
            _log.info("전체 봇 중지 완료: %s", stopped)
        return stopped

    # ── 상태 조회 ─────────────────────────────────────────────────────────────

    def get_status(self, symbol: str) -> dict:
        """symbol 봇의 상태를 반환한다."""
        if symbol not in self._bots:
            return {"symbol": symbol, "running": False, "error": "알 수 없는 심볼"}
        bot = self._bots[symbol]
        return {
            "symbol":            symbol,
            "running":           bot.is_running,
            "current_round":     bot.current_round,
            "reference_price":   bot.reference_price,
            "start_price":       bot.start_price,
            "pending_buy_price": bot.pending_buy_price,
            "pending_sell_price": bot.pending_sell_price,
            "ticker":            bot.ticker,
        }

    def get_all_status(self) -> dict[str, dict]:
        """모든 봇의 상태를 {symbol: status_dict} 형태로 반환한다."""
        return {sym: self.get_status(sym) for sym in self._bots}

    def is_running(self, symbol: str) -> bool:
        """symbol 봇이 실행 중이면 True."""
        return symbol in self._bots and self._bots[symbol].is_running

    def any_running(self) -> bool:
        """하나 이상의 봇이 실행 중이면 True."""
        return any(b.is_running for b in self._bots.values())

    # ── 프로퍼티 ──────────────────────────────────────────────────────────────

    @property
    def symbols(self) -> list[str]:
        """관리 중인 symbol 목록."""
        return list(self._bots.keys())

    @property
    def bots(self) -> dict[str, MagicSplit]:
        """봇 인스턴스 dict (읽기 전용 복사본)."""
        return dict(self._bots)
