"""TelegramCommandHandler — getUpdates long-polling으로 채팅 명령어를 수신하고 봇을 제어한다."""

import html
import threading
import time

import requests

import notification.message_builder as mb
from notification.telegram import _strip_html
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
        scheduler=None,
    ):
        self._bot_manager = bot_manager
        self._exchange    = exchange
        self._cfg         = cfg
        self._token       = token
        self._chat_id     = str(chat_id).strip()
        self._scheduler   = scheduler
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
        if cmd == "pnl":
            return self._cmd_pnl()
        if cmd == "set":
            return self._cmd_set(args)
        if cmd == "on":
            return self._cmd_bulk(args, True)
        if cmd == "off":
            return self._cmd_bulk(args, False)
        if cmd == "start":
            return self._cmd_start(args, fresh=True)
        if cmd == "resume":
            return self._cmd_start(args, fresh=False)
        if cmd == "stop":
            return self._cmd_stop(args)
        if cmd == "report":
            return self._cmd_report()
        if cmd in ("stopall", "pauseall"):
            return self._cmd_stop_all()
        if cmd in ("resumeall", "startall"):
            return self._cmd_resume_all()
        return f"알 수 없는 명령어: /{cmd}\n/help 로 목록을 확인하세요."

    # ── 명령어 구현 ───────────────────────────────────────────────────────────

    @staticmethod
    def _cmd_help() -> str:
        return (
            "📋 CryptoSplit 명령어\n"
            "─────────────────\n"
            "📊 현황 조회\n"
            "/status   — 차수·트리거·손익 현황\n"
            "/balance  — 잔고 + 코인 평가금액\n"
            "/pnl      — 오늘 실현손익 요약\n"
            "/report   — 오늘 결산 즉시 전송\n"
            "\n"
            "▶ 봇 제어  (예: BTC)\n"
            "/start BTC   — 새로 시작 (초기화)\n"
            "/resume BTC  — 재시작 (이전 상태 복원)\n"
            "/stop BTC    — 중지 + 미체결 취소\n"
            "\n"
            "⚙ 설정 변경\n"
            "/set BTC trigger_krw 300000\n"
            "  파라미터: trigger_mode / trigger_pct\n"
            "           trigger_krw / max_rounds\n"
            "           order_amount\n"
            "\n"
            "⏹ 전체 제어  (OFF 종목 제외)\n"
            "/stopall    — 전체 봇 중지\n"
            "/resumeall  — 중지된 봇 전체 재시작\n"
            "\n"
            "🔘 전체제어 ON/OFF  (예: BTC)\n"
            "/on BTC    — 전체 명령 대상 포함\n"
            "/off BTC   — 전체 명령에서 제외"
        )

    def _cmd_status(self) -> str:
        """정각 알림(build_hourly_status)과 같은 표 렌더러를 공유한다.

        같은 봇이 두 가지 양식으로 말하지 않도록 status_table()/trigger_lines()를
        그대로 쓰고, 명령으로 직접 물어본 경우에만 시작가·기준가 표를 덧붙인다.
        """
        from database.pnl_repo import get_daily_pnl

        statuses = []
        for symbol, status in self._bot_manager.get_all_status().items():
            ticker = status.get("ticker", f"KRW-{symbol}")
            try:
                price = self._exchange.get_current_price(ticker)
            except Exception:
                price = 0
            pair_cfg = self._cfg.get("pairs", {}).get(symbol, {})
            pnl_data = get_daily_pnl(symbol) or {}
            statuses.append({
                "symbol":             symbol,
                "running":            status.get("running", False),
                "current_price":      price,
                "current_round":      status.get("current_round", 0),
                "max_rounds":         int(pair_cfg.get("max_rounds", 50)),
                "today_pnl":          float(pnl_data.get("realized_pnl", 0) or 0),
                "start_price":        status.get("start_price", 0),
                "reference_price":    status.get("reference_price", 0),
                "pending_buy_price":  status.get("pending_buy_price", 0),
                "pending_sell_price": status.get("pending_sell_price", 0),
            })

        title = f"봇 상태 · {time.strftime('%H:%M')}"
        if not statuses:
            return mb.card(f"{mb.ICON_STOPPED} {title}", foot="등록된 종목이 없습니다.")

        running = [s for s in statuses if s["running"]]
        icon    = mb.ICON_RUNNING if running else mb.ICON_STOPPED
        total   = sum(s["today_pnl"] for s in statuses)

        body = mb.status_table(statuses)

        # 시작가·기준가는 /status 에서만 보여준다 (정각 알림은 표만)
        price_rows = [
            [s["symbol"],
             mb._num(s["start_price"])     if s["start_price"]     else "—",
             mb._num(s["reference_price"]) if s["reference_price"] else "—"]
            for s in statuses
            if s["start_price"] or s["reference_price"]
        ]
        if price_rows:
            body.append("")
            body += mb.table(["종목", "시작가", "기준가"], price_rows)

        trig = mb.trigger_lines(statuses)
        return mb.card(
            f"{icon} {title}",
            body,
            sub=f"{len(running)}/{len(statuses)} 종목 실행중 · 오늘 {mb.signed(total)}",
            foot=("대기 트리거\n" + "\n".join(trig)) if trig else None,
        )

    def _cmd_balance(self) -> str:
        try:
            balances = get_all_balances(self._exchange)
        except Exception as exc:
            return f"잔고 조회 실패: {exc}"

        krw_bal   = balances.get("KRW", 0.0)
        total_est = krw_bal
        rows      = [["KRW", "—", mb.krw(krw_bal)]]

        for symbol in self._bot_manager.symbols:
            ticker   = self._bot_manager.bots[symbol].ticker
            currency = ticker.replace("KRW-", "")
            qty      = balances.get(currency, 0.0)
            try:
                price   = self._exchange.get_current_price(ticker)
                est_krw = qty * price
                total_est += est_krw
                rows.append([symbol, mb.qty(qty), mb.krw(est_krw)])
            except Exception:
                rows.append([symbol, mb.qty(qty), "—"])

        rows.append(["총계", "—", mb.krw(total_est)])
        return mb.card("💰 잔고 현황",
                       mb.table(["자산", "수량", "평가금액"], rows),
                       sub=f"총 평가액 {mb.krw(total_est)}")

    def _cmd_pnl(self) -> str:
        from database.pnl_repo import get_daily_pnl

        today  = time.strftime("%Y-%m-%d")
        total  = 0.0
        trades_total = 0
        rows   = []
        for symbol in self._bot_manager.symbols:
            pnl_data = get_daily_pnl(symbol) or {}
            pnl      = float(pnl_data.get("realized_pnl", 0) or 0)
            trades   = int(pnl_data.get("trade_count", 0) or 0)
            total        += pnl
            trades_total += trades
            rows.append([symbol, mb.signed(pnl, unit=""), f"{trades}회"])

        body = mb.table(["종목", "손익", "거래"], rows)
        if len(rows) > 1:
            body.append(mb.rule(body))
            body += mb.table(["", "", ""], [[
                "합계", mb.signed(total, unit=""), f"{trades_total}회",
            ]])[1:]

        return mb.card(f"📊 오늘 실현손익 · {today}", body,
                       sub=f"합계 {mb.signed(total)}")

    def _cmd_set(self, args: list[str]) -> str:
        import os
        import yaml

        if len(args) < 3:
            return (
                "사용법: /set SYMBOL 파라미터 값\n"
                "예: /set BTC trigger_krw 300000\n"
                "파라미터: trigger_mode / trigger_pct / trigger_krw / max_rounds / order_amount"
            )

        symbol = args[0].upper()
        param  = args[1].lower()
        raw    = args[2]

        ALLOWED = {
            "trigger_mode": str,
            "trigger_pct":  float,
            "trigger_krw":  float,
            "max_rounds":   int,
            "order_amount": int,
        }
        if param not in ALLOWED:
            return f"❌ 지원하지 않는 파라미터: {param}"

        pairs = self._cfg.get("pairs", {})
        if symbol not in pairs:
            return f"❌ 알 수 없는 심볼: {symbol}"

        try:
            value = ALLOWED[param](raw)
        except (ValueError, TypeError):
            return f"❌ 값 변환 실패: {raw}"

        if param == "trigger_mode" and value not in ("pct", "krw"):
            return "❌ trigger_mode는 'pct' 또는 'krw'만 가능합니다."

        pairs[symbol][param] = value

        try:
            yaml_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
            )
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw_cfg = yaml.safe_load(f) or {}
            raw_cfg.setdefault("pairs", {}).setdefault(symbol, {})[param] = value
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(raw_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            _log.error("/set 파일 저장 실패: %s", exc)
            return f"✅ {symbol} {param} = {value}  (메모리 반영, 파일 저장 실패: {exc})"

        _log.info("[/set] %s.%s = %s", symbol, param, value)
        return f"✅ {symbol} {param} = {value}  (즉시 반영됨)"

    def _cmd_bulk(self, args: list[str], enabled: bool) -> str:
        """종목별 전체제어 ON/OFF (전체 가동/중지 명령 대상 포함 여부)."""
        import os
        import yaml

        if not args:
            cmd = "on" if enabled else "off"
            return f"심볼을 지정하세요. 예: /{cmd} USDT"
        symbol = args[0].upper()

        if not self._bot_manager.set_bulk_enabled(symbol, enabled):
            return f"❌ 알 수 없는 심볼: {symbol}"

        try:
            yaml_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
            )
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw_cfg = yaml.safe_load(f) or {}
            raw_cfg.setdefault("pairs", {}).setdefault(symbol, {})["bulk_enabled"] = enabled
            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(raw_cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as exc:
            _log.error("/on·off 저장 실패: %s", exc)
            return f"{'🟢' if enabled else '⏸'} {symbol} 전체제어 {'ON' if enabled else 'OFF'} (메모리만, 저장실패)"

        _log.info("[전체제어] %s = %s", symbol, "ON" if enabled else "OFF")
        if enabled:
            return f"🟢 {symbol} 전체제어 ON\n전체 가동/중지 명령에 포함됩니다."
        return f"⏸ {symbol} 전체제어 OFF\n전체 가동/중지 명령에서 제외됩니다."

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

    def _cmd_stop_all(self) -> str:
        stopped = self._bot_manager.stop_all()
        off = [s for s in self._bot_manager.symbols if not self._bot_manager.is_bulk_enabled(s)]
        if not stopped:
            msg = "전체 제어 대상 중 실행 중인 봇이 없습니다."
            if off:
                msg += f"\n⏸ 제외(OFF): {', '.join(off)}"
            return msg
        lines = ["⏹️ 전체 봇 중지 완료", "─────────────────", ", ".join(stopped)]
        if off:
            lines.append(f"⏸ 제외(OFF): {', '.join(off)}")
        return "\n".join(lines)

    def _cmd_resume_all(self) -> str:
        resumed, failed, skipped = self._bot_manager.start_all(fresh=False)
        if not resumed and not failed:
            msg = "재시작할 봇이 없습니다 (모두 실행 중이거나 OFF)."
            if skipped:
                msg += f"\n⏸ 제외(OFF): {', '.join(skipped)}"
            return msg
        lines = ["▶ 전체 봇 재시작", "─────────────────"]
        if resumed:
            lines.append(f"재시작: {', '.join(resumed)}")
        if failed:
            lines.append(f"실패: {', '.join(failed)}")
        if skipped:
            lines.append(f"⏸ 제외(OFF): {', '.join(skipped)}")
        return "\n".join(lines)

    def _cmd_report(self) -> str:
        if not self._scheduler:
            return "❌ 결산 기능을 사용할 수 없습니다 (스케줄러 미연결)."
        ok = self._scheduler.report_now()
        return "📊 오늘 결산을 전송했습니다." if ok else "❌ 결산 전송에 실패했습니다."

    # ── 응답 전송 ─────────────────────────────────────────────────────────────

    def _reply(self, chat_id: int, text: str) -> None:
        """명령어 응답 전송.

        /status·/balance·/report 는 card() 가 만든 HTML 이고, 나머지 단문은
        평문이다. 평문에는 사용자가 친 명령어나 예외 문자열이 섞일 수 있어
        (예: "<class 'ValueError'>") 그대로 HTML 로 보내면 400이 난다 —
        card 가 아니면 이스케이프하고, 그래도 400이면 평문으로 한 번 더 보낸다.
        """
        url  = f"{_BASE_URL.format(token=self._token)}/sendMessage"
        body = text if mb.is_card(text) else html.escape(text, quote=False)
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": body, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.status_code == 400:
                _log.warning("명령어 응답 HTML 파싱 실패 — 평문으로 재전송")
                requests.post(
                    url,
                    json={"chat_id": chat_id, "text": _strip_html(text)},
                    timeout=10,
                )
        except Exception as exc:
            _log.error("명령어 응답 전송 실패: %s", exc)
