"""TelegramCommandHandler — getUpdates long-polling으로 채팅 명령어를 수신하고 봇을 제어한다."""

import threading
import time

import requests

from portfolio.balance import get_all_balances
from utils.logger import get_logger

_log = get_logger("notification.telegram_handler")

_BASE_URL     = "https://api.telegram.org/bot{token}"
_POLL_TIMEOUT = 30     # long-polling 대기 (초)
_HTTP_TIMEOUT = 35     # requests 타임아웃 (_POLL_TIMEOUT 보다 여유 있게)
_ERR_SLEEP    = 5      # 오류 발생 후 재시도 대기 (초)


class TelegramCommandHandler:
    """텔레그램 채팅 명령어를 수신하여 BotManager를 제어한다.

    chat_id 화이트리스트로 허가된 사용자만 명령을 처리한다.
    """

    def __init__(
        self,
        bot_manager,
        exchange,
        cfg: dict,
        token: str,
        chat_id: str,
    ):
        self._bot_manager = bot_manager
        self._exchange    = exchange
        self._cfg         = cfg
        self._token       = token
        self._chat_id     = str(chat_id).strip()
        self._stop        = threading.Event()
        self._thread: threading.Thread | None = None

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tg-cmd", daemon=True)
        self._thread.start()
        _log.info("텔레그램 명령어 핸들러 시작")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=_POLL_TIMEOUT + 5)
        _log.info("텔레그램 명령어 핸들러 종료")

    # ── 폴링 루프 ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        offset = 0
        while not self._stop.is_set():
            try:
                updates = self._get_updates(offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception as exc:
                _log.error("폴링 오류: %s", exc)
                time.sleep(_ERR_SLEEP)

    def _get_updates(self, offset: int) -> list[dict]:
        url  = f"{_BASE_URL.format(token=self._token)}/getUpdates"
        resp = requests.get(
            url,
            params={"offset": offset, "timeout": _POLL_TIMEOUT},
            timeout=_HTTP_TIMEOUT,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"getUpdates 실패: {data}")
        return data.get("result", [])

    # ── 메시지 처리 ───────────────────────────────────────────────────────────

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message")
        if not msg:
            return

        sender_id = str(msg.get("chat", {}).get("id", ""))
        if sender_id != self._chat_id:
            _log.warning("허가되지 않은 chat_id에서 메시지 수신: %s", sender_id)
            return

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return

        parts = text.split()
        # '/start@BotName arg' 형태도 처리
        cmd  = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1:]

        reply = self._dispatch(cmd, args)
        if reply:
            self._reply(int(sender_id), reply)

    def _dispatch(self, cmd: str, args: list[str]) -> str:
        if cmd == "help":
            return self._cmd_help()
        if cmd == "status":
            return self._cmd_status()
        if cmd == "balance":
            return self._cmd_balance()
        if cmd == "start":
            return self._cmd_start(args, fresh=True)
        if cmd == "resume":
            return self._cmd_start(args, fresh=False)
        if cmd == "stop":
            return self._cmd_stop(args)
        return f"알 수 없는 명령어: /{cmd}\n/help 로 목록을 확인하세요."

    # ── 명령어 구현 ───────────────────────────────────────────────────────────

    @staticmethod
    def _cmd_help() -> str:
        return (
            "📋 CryptoSplit 명령어\n"
            "─────────────────\n"
            "/status        — 차수·트리거·수익 상세 현황\n"
            "/balance       — 잔고 + 코인 평가금액\n"
            "/start ETH     — ETH 새로 시작 (초기화)\n"
            "/start USDT    — USDT 새로 시작 (초기화)\n"
            "/resume ETH    — ETH 재시작 (이전 상태 복원)\n"
            "/resume USDT   — USDT 재시작 (이전 상태 복원)\n"
            "/stop ETH      — ETH 중지 + 미체결 취소\n"
            "/stop USDT     — USDT 중지 + 미체결 취소"
        )

    def _cmd_status(self) -> str:
        from database.pnl_repo import get_daily_pnl

        now   = time.strftime("%H:%M")
        lines = [f"📊 봇 상태 [{now}]"]

        for symbol, status in self._bot_manager.get_all_status().items():
            lines.append("─────────────────")
            ticker     = status.get("ticker", f"KRW-{symbol}")
            running    = status.get("running", False)
            round_     = status.get("current_round", 0)
            ref_p      = status.get("reference_price", 0)
            start_p    = status.get("start_price", 0)
            buy_p      = status.get("pending_buy_price", 0)
            sell_p     = status.get("pending_sell_price", 0)
            pair_cfg   = self._cfg.get("pairs", {}).get(symbol, {})
            max_rounds = int(pair_cfg.get("max_rounds", 50))

            try:
                price = self._exchange.get_current_price(ticker)
            except Exception:
                price = 0

            if running:
                lines.append(f"🟢 {symbol} 실행중 · {round_}/{max_rounds}차")
                if start_p > 0:
                    lines.append(f"📍 시작가    {int(start_p):,}원")
                if ref_p > 0:
                    lines.append(f"📌 기준가    {int(ref_p):,}원")
                if price > 0:
                    lines.append(f"💲 현재가    {price:,.0f}원")

                if round_ >= max_rounds:
                    lines.append(f"🔵 매수 중단 (최대 {max_rounds}차 도달)")
                elif buy_p > 0:
                    lines.append(f"🔵 {round_ + 1}차 매수 대기  {int(buy_p):,}원 이하")
                else:
                    lines.append(f"🔵 매수 주문 처리중")

                if sell_p > 0:
                    lines.append(f"🟠 {round_}차 매도 대기  {int(sell_p):,}원 이상")
                elif round_ > 0:
                    lines.append(f"🟠 매도 주문 처리중")
                else:
                    lines.append(f"🟠 매도 없음 (보유 없음)")

                try:
                    pnl_data = get_daily_pnl(symbol) or {}
                    net_pnl  = float(pnl_data.get("realized_pnl", 0)) - float(pnl_data.get("fee_total", 0))
                    trades   = int(pnl_data.get("trade_count", 0))
                    sign     = "+" if net_pnl >= 0 else ""
                    icon     = "📈" if net_pnl >= 0 else "📉"
                    lines.append(f"{icon} 오늘수익    {sign}{int(net_pnl):,}원 ({trades}회)")
                except Exception:
                    pass
            else:
                lines.append(f"🔴 {symbol} 중지됨")
                if round_ > 0:
                    lines.append(f"📌 종료차수  {round_}차")
                if ref_p > 0:
                    lines.append(f"📍 기준가    {int(ref_p):,}원")
                if price > 0:
                    lines.append(f"💲 현재가    {price:,.0f}원")

        return "\n".join(lines)

    def _cmd_balance(self) -> str:
        try:
            balances = get_all_balances(self._exchange)
        except Exception as exc:
            return f"잔고 조회 실패: {exc}"

        lines = ["💰 잔고 현황", "─────────────────"]
        krw       = balances.get("KRW", 0.0)
        total_est = krw
        lines.append(f"KRW    {krw:,.0f}원")

        for symbol in self._bot_manager.symbols:
            bot      = self._bot_manager.bots[symbol]
            ticker   = bot.ticker
            currency = ticker.replace("KRW-", "")
            qty      = balances.get(currency, 0.0)
            try:
                price   = self._exchange.get_current_price(ticker)
                est_krw = qty * price
                total_est += est_krw
                lines.append(f"{symbol}    {qty:g} {currency}  ≈ {int(est_krw):,}원")
            except Exception:
                lines.append(f"{symbol}    {qty:g} {currency}")

        lines.append("─────────────────")
        lines.append(f"총 평가액   {int(total_est):,}원")
        return "\n".join(lines)

    def _cmd_start(self, args: list[str], fresh: bool) -> str:
        if not args:
            cmd = "start" if fresh else "resume"
            return f"심볼을 지정하세요. 예: /{cmd} ETH"
        symbol = args[0].upper()
        ok, msg = self._bot_manager.start(symbol, fresh=fresh)
        if ok:
            action = "새로 시작 (초기화)" if fresh else "재시작 (이전 상태 복원)"
            lines  = [f"✅ {symbol} {action}"]
            if not fresh:
                status = self._bot_manager.get_status(symbol)
                round_ = status.get("current_round", 0)
                ref_p  = status.get("reference_price", 0)
                if round_ > 0:
                    lines.append(f"📌 복원차수  {round_}차")
                if ref_p > 0:
                    lines.append(f"📍 기준가    {int(ref_p):,}원")
            return "\n".join(lines)
        return f"❌ {msg}"

    def _cmd_stop(self, args: list[str]) -> str:
        if not args:
            return "심볼을 지정하세요. 예: /stop ETH"
        symbol = args[0].upper()
        status_before = self._bot_manager.get_status(symbol)
        ok, msg = self._bot_manager.stop(symbol)
        if ok:
            lines  = [f"✅ {symbol} 봇 중지 완료"]
            round_ = status_before.get("current_round", 0)
            ref_p  = status_before.get("reference_price", 0)
            if round_ > 0:
                lines.append(f"📌 종료차수  {round_}차")
            if ref_p > 0:
                lines.append(f"📍 기준가    {int(ref_p):,}원")
            return "\n".join(lines)
        return f"❌ {msg}"

    # ── 응답 전송 ─────────────────────────────────────────────────────────────

    def _reply(self, chat_id: int, text: str) -> None:
        url = f"{_BASE_URL.format(token=self._token)}/sendMessage"
        try:
            requests.post(
                url,
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        except Exception as exc:
            _log.error("명령어 응답 전송 실패: %s", exc)
