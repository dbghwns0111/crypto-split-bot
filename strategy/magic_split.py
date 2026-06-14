"""MagicSplit 메인 루프 — Thread 실행, 1초 폴링, 체결 처리."""

import threading
import time
from typing import Optional

from database.connection import configure as db_configure, get_connection
from database.models import create_tables
from database.order_repo import (
    get_latest_filled_buy,
    get_open_orders,
    save_catchup_order,
    save_order,
    update_order,
)
from database.pnl_repo import add_trade_pnl
from database.state_repo import (
    decrement_round,
    get_state,
    increment_round,
    set_reference_price,
    set_start_price,
    upsert_state,
)
from strategy.circuit_breaker import CircuitBreaker
from strategy.trigger import (
    buy_order_price,
    buy_order_price_krw,
    sell_order_price,
    sell_order_price_krw,
    should_buy,
    should_buy_krw,
    should_sell,
    should_sell_krw,
)
from strategy.volatility import VolatilityMeter
from utils.logger import get_logger
from utils.math_utils import calc_pnl, calc_quantity, get_point_rate, round_price
from utils.time_utils import now_kst_str

_log = get_logger("strategy.magic_split")


class MagicSplit:
    """매직스플릿 전략 실행기."""

    def __init__(
        self,
        symbol: str,
        exchange,
        cfg: dict,
        notifier=None,
    ):
        self._symbol = symbol
        self._exchange = exchange
        self._cfg = cfg
        self._notifier = notifier

        pair = cfg["pairs"][symbol]
        self._ticker       = pair["ticker"]
        self._max_rounds   = pair["max_rounds"]
        self._order_amount = pair["order_amount"]
        self._poll_interval  = cfg["strategy"]["price_poll_interval"]
        self._order_timeout  = cfg["strategy"]["order_timeout"]
        self._sell_extra_pct = cfg["strategy"]["sell_extra_pct"]
        membership = cfg.get("exchange", {}).get("membership", "화이트")
        self._fee_rate   = 0.04
        self._point_rate = get_point_rate(membership)

        # 런타임 상태
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._reference_price = 0.0
        self._start_price     = 0.0
        self._current_round   = 0
        self._is_resume       = False

        # 미체결 주문 추적
        self._pending_buy:  Optional[dict] = None   # {order_id, placed_at}
        self._pending_sell: Optional[dict] = None

        self._volatility      = VolatilityMeter(exchange, symbol, self._ticker, cfg)
        self._circuit_breaker = CircuitBreaker(symbol, cfg)

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """DB에서 이전 상태를 복구하고 루프 스레드를 시작한다."""
        if self._running:
            return
        self._restore_state()
        self._running = True
        upsert_state(self._symbol, reference_price=self._reference_price,
                     current_round=self._current_round, is_running=True)
        self._thread = threading.Thread(
            target=self._loop,
            name=f"MagicSplit-{self._symbol}",
            daemon=True,
        )
        self._thread.start()
        _log.info("봇 시작: %s (기준가=%s 차수=%d)",
                  self._symbol, self._reference_price, self._current_round)

    def reset_state(self) -> None:
        """런타임 상태와 DB를 초기화한다 (전략 첫 시작 전 호출)."""
        self._reference_price = 0.0
        self._current_round   = 0
        self._is_resume       = False
        self._pending_buy     = None
        self._pending_sell    = None
        upsert_state(self._symbol, reference_price=0.0, current_round=0, is_running=False)
        _log.info("상태 초기화: %s", self._symbol)

    def stop(self) -> None:
        """루프를 중지한다. 미체결 주문 취소는 호출자(BotManager)가 담당한다."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        upsert_state(self._symbol, reference_price=self._reference_price,
                     current_round=self._current_round, is_running=False)
        _log.info("봇 중지: %s", self._symbol)

    # ── 프로퍼티 ──────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_resume(self) -> bool:
        return self._is_resume

    @property
    def current_round(self) -> int:
        return self._current_round

    @property
    def reference_price(self) -> float:
        return self._reference_price

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def ticker(self) -> str:
        return self._ticker

    # ── 루프 ─────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                _log.error("루프 오류 (%s): %s", self._symbol, exc)
            time.sleep(self._poll_interval)

    def _tick(self) -> None:
        """단일 폴링 사이클: 현재가 조회 → 트리거 판단 → 주문."""
        current_price = self._exchange.get_current_price(self._ticker)

        cb_active = self._circuit_breaker.update(current_price)

        # 미체결 타임아웃 처리
        self._handle_timeouts()

        # 체결 확인
        self._check_fills()

        # 기준가 미설정 시: start_price가 있으면 그 값, 없으면 현재가로 초기화
        if self._reference_price <= 0:
            init_price = self._start_price if self._start_price > 0 else current_price
            self._reference_price = init_price
            set_reference_price(self._symbol, init_price)
            if self._start_price <= 0:
                self._start_price = current_price
                set_start_price(self._symbol, current_price)
            return

        pair         = self._cfg["pairs"][self._symbol]
        trigger_mode = pair.get("trigger_mode", "pct")
        trigger_pct  = self._volatility.get_trigger_pct()
        trigger_krw  = float(pair.get("trigger_krw", 10000))

        # ── 매도 (서킷브레이커 발동 시에도 매도는 허용) ─────────────────────
        if self._pending_sell is None and self._current_round > 0:
            if trigger_mode == "krw":
                fee_krw = self._reference_price * self._sell_extra_pct / 100
                do_sell = should_sell_krw(current_price, self._reference_price, trigger_krw + fee_krw)
            else:
                do_sell = should_sell(current_price, self._reference_price,
                                      trigger_pct, self._sell_extra_pct)
            if do_sell:
                self._place_sell(trigger_pct, trigger_mode, trigger_krw)

        # ── 매수 (서킷브레이커 발동 시 중단) ─────────────────────────────────
        if not cb_active and self._pending_buy is None:
            if self._current_round >= self._max_rounds:
                self._notify_max_round()
            else:
                if trigger_mode == "krw":
                    do_buy = should_buy_krw(current_price, self._reference_price, trigger_krw)
                else:
                    do_buy = should_buy(current_price, self._reference_price, trigger_pct)
                if do_buy:
                    self._place_buy(trigger_pct, trigger_mode, trigger_krw)

    # ── 주문 배치 ─────────────────────────────────────────────────────────────

    def _place_buy(self, trigger_pct: float, trigger_mode: str = "pct", trigger_krw: float = 0.0) -> None:
        if trigger_mode == "krw":
            price = round_price(buy_order_price_krw(self._reference_price, trigger_krw))
        else:
            price = round_price(buy_order_price(self._reference_price, trigger_pct))
        ref = round_price(self._reference_price)
        if price >= ref:
            _log.warning("매수 생략: 반올림 후 가격(%d) ≥ 기준가(%d) — trigger가 tick 단위보다 작음",
                         price, ref)
            return
        quantity = calc_quantity(self._order_amount, price)
        if quantity <= 0:
            return
        try:
            result = self._exchange.buy_limit(self._ticker, price, quantity)
            oid = result["order_id"]
            self._pending_buy = {"order_id": oid, "placed_at": time.monotonic()}
            save_order(self._symbol, oid, "buy", price, quantity,
                       round(price * quantity, 2), self._current_round + 1)
            _log.info("매수 주문 전송: %s 가격=%d 수량=%s", self._symbol, price, quantity)
        except Exception as exc:
            _log.error("매수 주문 실패 (%s): %s", self._symbol, exc)

    def _place_sell(self, trigger_pct: float, trigger_mode: str = "pct", trigger_krw: float = 0.0) -> None:
        latest_buy = get_latest_filled_buy(self._symbol)
        if not latest_buy:
            return
        quantity = latest_buy["quantity"]
        if trigger_mode == "krw":
            fee_krw = self._reference_price * self._sell_extra_pct / 100
            price = round_price(sell_order_price_krw(self._reference_price, trigger_krw + fee_krw))
        else:
            price = round_price(sell_order_price(self._reference_price,
                                                 trigger_pct, self._sell_extra_pct))
        ref = round_price(self._reference_price)
        if price <= ref:
            _log.warning("매도 생략: 반올림 후 가격(%d) ≤ 기준가(%d) — trigger가 tick 단위보다 작음",
                         price, ref)
            return
        try:
            result = self._exchange.sell_limit(self._ticker, price, quantity)
            oid = result["order_id"]
            self._pending_sell = {"order_id": oid, "placed_at": time.monotonic()}
            save_order(self._symbol, oid, "sell", price, quantity,
                       round(price * quantity, 2), self._current_round)
            _log.info("매도 주문 전송: %s 가격=%d 수량=%s", self._symbol, price, quantity)
        except Exception as exc:
            _log.error("매도 주문 실패 (%s): %s", self._symbol, exc)

    # ── 체결 확인 ─────────────────────────────────────────────────────────────

    def _check_fills(self) -> None:
        if self._pending_buy:
            self._check_one_fill(self._pending_buy["order_id"], "buy")
        if self._pending_sell:
            self._check_one_fill(self._pending_sell["order_id"], "sell")

    def _check_one_fill(self, order_id: str, side: str) -> None:
        try:
            info = self._exchange.get_order_status(order_id)
        except Exception as exc:
            _log.error("체결 조회 실패 (%s): %s", order_id, exc)
            return

        if info["status"] == "filled":
            filled_price = info.get("filled_price") or info["price"]
            update_order(order_id, "filled",
                         filled_price=filled_price, filled_at=now_kst_str())
            if side == "buy":
                self._on_buy_filled(filled_price, info.get("filled_quantity", 0))
            else:
                self._on_sell_filled(filled_price, info.get("filled_quantity", 0))

        elif info["status"] == "cancelled":
            update_order(order_id, "cancelled")
            if side == "buy":
                self._pending_buy = None
            else:
                self._pending_sell = None

    def _on_buy_filled(self, filled_price: float, qty: float) -> None:
        self._current_round += 1
        self._reference_price = filled_price
        self._pending_buy = None
        increment_round(self._symbol)
        set_reference_price(self._symbol, filled_price)
        _log.info("매수 체결: %s 가격=%s 차수=%d",
                  self._symbol, filled_price, self._current_round)
        self._notify("buy_filled", price=filled_price, qty=qty, round_no=self._current_round)
        if self._current_round >= self._max_rounds:
            self._notify_max_round()

    def _on_sell_filled(self, filled_price: float, qty: float) -> None:
        latest_buy = get_latest_filled_buy(self._symbol)
        buy_price = 0.0
        net_pnl   = 0.0
        if latest_buy and latest_buy.get("filled_price"):
            buy_price = float(latest_buy["filled_price"])
            pnl = calc_pnl(buy_price, filled_price, qty, self._fee_rate, self._point_rate)
            net_pnl = pnl["net"]
            add_trade_pnl(self._symbol, pnl["net"], pnl["fee"], pnl["points"])

        self._current_round = max(0, self._current_round - 1)
        self._reference_price = filled_price
        self._pending_sell = None
        decrement_round(self._symbol)
        set_reference_price(self._symbol, filled_price)
        _log.info("매도 체결: %s 가격=%s 차수=%d",
                  self._symbol, filled_price, self._current_round)
        self._notify("sell_filled", price=filled_price, qty=qty,
                     round_no=self._current_round, net_pnl=net_pnl, buy_price=buy_price)

    # ── 타임아웃 ──────────────────────────────────────────────────────────────

    def _handle_timeouts(self) -> None:
        now = time.monotonic()
        for attr, side in (("_pending_buy", "buy"), ("_pending_sell", "sell")):
            pending = getattr(self, attr)
            if pending and (now - pending["placed_at"]) > self._order_timeout:
                oid = pending["order_id"]
                _log.info("주문 타임아웃 취소: %s (%s)", oid, side)
                try:
                    self._exchange.cancel_order(oid)
                    update_order(oid, "cancelled")
                except Exception as exc:
                    _log.error("타임아웃 취소 실패: %s", exc)
                setattr(self, attr, None)

    # ── 알림 헬퍼 ─────────────────────────────────────────────────────────────

    def _notify(self, event: str, **kwargs) -> None:
        if self._notifier is None:
            return
        try:
            fn = getattr(self._notifier, f"send_{event}", None)
            if fn:
                fn(symbol=self._symbol, **kwargs)
        except Exception as exc:
            _log.debug("텔레그램 알림 실패 (%s): %s", event, exc)

    def _notify_max_round(self) -> None:
        _log.warning("최대 차수 도달: %s (%d차)", self._symbol, self._current_round)
        self._notify("max_round", round_no=self._current_round)

    # ── 상태 복구 ─────────────────────────────────────────────────────────────

    def _restore_state(self) -> None:
        """DB에서 이전 실행 상태를 복구한다."""
        try:
            state = get_state(self._symbol)
            if state:
                self._reference_price = float(state["reference_price"] or 0)
                self._start_price     = float(state.get("start_price") or 0)
                self._current_round   = int(state["current_round"] or 0)
                self._is_resume       = (self._current_round > 0)
                _log.info("상태 복구: %s 기준가=%s 차수=%d",
                          self._symbol, self._reference_price, self._current_round)
                # ① open 주문 동기화 (강제 중단 대비)
                self._reconcile_open_orders()
                # ② 캐치업 매수 (reconcile 후 최신 기준가 기준으로 갭 계산)
                if self._reference_price > 0:
                    current_price = self._exchange.get_current_price(self._ticker)
                    is_fresh_start = (self._current_round == 0 and self._start_price > 0)
                    self._do_catchup(current_price, fresh_start=is_fresh_start)
            else:
                upsert_state(self._symbol, reference_price=0.0,
                             current_round=0, is_running=False)
        except Exception as exc:
            _log.error("상태 복구 실패: %s", exc)

    def _reconcile_open_orders(self) -> None:
        """강제 중단 시 DB에 남은 open 주문을 거래소 실제 상태와 동기화한다."""
        open_orders = get_open_orders(self._symbol)
        if not open_orders:
            return

        _log.info("미체결 주문 복구 시작: %s %d건", self._symbol, len(open_orders))
        changed = False

        for order in open_orders:
            oid  = order["order_id"]
            side = order["side"]
            try:
                info   = self._exchange.get_order_status(oid)
                status = info["status"]

                if status == "filled":
                    filled_price = float(info.get("filled_price") or order["price"])
                    filled_qty   = float(info.get("filled_quantity") or order["quantity"])
                    update_order(oid, "filled",
                                 filled_price=filled_price, filled_at=now_kst_str())

                    if side == "buy":
                        self._current_round  += 1
                        self._reference_price = filled_price
                        _log.info("매수 체결 복구: %s 가격=%s 차수=%d",
                                  self._symbol, filled_price, self._current_round)
                    else:  # sell
                        latest_buy = get_latest_filled_buy(self._symbol)
                        if latest_buy and latest_buy.get("filled_price"):
                            pnl = calc_pnl(
                                float(latest_buy["filled_price"]),
                                filled_price, filled_qty,
                                self._fee_rate, self._point_rate,
                            )
                            add_trade_pnl(self._symbol, pnl["net"], pnl["fee"], pnl["points"])
                        self._current_round   = max(0, self._current_round - 1)
                        self._reference_price = filled_price
                        _log.info("매도 체결 복구: %s 가격=%s 차수=%d",
                                  self._symbol, filled_price, self._current_round)
                    changed = True

                elif status == "open":
                    # 아직 미체결 → 취소 (루프 재시작 후 조건 만족 시 새로 주문)
                    self._exchange.cancel_order(oid)
                    update_order(oid, "cancelled")
                    _log.info("미체결 %s 주문 취소: %s", side, oid)

                else:  # cancelled
                    update_order(oid, "cancelled")
                    _log.info("이미 취소된 주문 DB 반영: %s", oid)

            except Exception as exc:
                _log.error("주문 복구 실패 (%s): %s", oid, exc)

        if changed:
            upsert_state(self._symbol,
                         reference_price=self._reference_price,
                         current_round=self._current_round)
            _log.info("복구 완료: %s 기준가=%s 차수=%d",
                      self._symbol, self._reference_price, self._current_round)

    def _do_catchup(self, current_price: float, fresh_start: bool = False) -> None:
        """중단 중 지나친 트리거 레벨을 매수로 채운다."""
        if current_price <= 0 or self._reference_price <= 0:
            return

        pair         = self._cfg["pairs"][self._symbol]
        trigger_mode = pair.get("trigger_mode", "pct")
        trigger_krw  = float(pair.get("trigger_krw", 10000))
        trigger_pct  = float(pair.get("trigger_pct", 0.5))
        max_catchup  = int(self._cfg["strategy"].get("catchup_max", 10))

        if trigger_mode == "krw":
            gap = int((self._reference_price - current_price) // trigger_krw)
        else:
            step = self._reference_price * trigger_pct / 100
            gap  = int((self._reference_price - current_price) // step) if step > 0 else 0

        if fresh_start:
            gap += 1  # 시작가 레벨 자체를 첫 번째 매수로 포함

        if gap <= 0:
            return

        room = self._max_rounds - self._current_round
        try:
            balance_krw = self._exchange.get_balance("KRW")
            affordable  = int(float(balance_krw) // self._order_amount)
        except Exception:
            affordable  = gap

        n = min(gap, room, max_catchup, affordable)
        if n <= 0:
            _log.info("캐치업 생략: %s (갭=%d, room=%d, affordable=%d)",
                      self._symbol, gap, room, affordable)
            return

        _log.info("캐치업 매수 시작: %s %d/%d회 (기준가=%s → 현재가=%s)",
                  self._symbol, n, gap, self._reference_price, current_price)

        grid_prices = []
        g = self._reference_price
        if fresh_start and n >= 1:
            grid_prices.append(round_price(g))  # 시작가 자체가 1차 그리드
            n_loop = n - 1
        else:
            n_loop = n
        for _ in range(n_loop):
            if trigger_mode == "krw":
                g = g - trigger_krw
            else:
                g = g * (1 - trigger_pct / 100)
            grid_prices.append(round_price(g))

        done = 0
        for intended_price in grid_prices:
            quantity = calc_quantity(self._order_amount, current_price)
            if quantity <= 0:
                break
            try:
                result = self._exchange.buy_limit(self._ticker, current_price, quantity)
                oid    = result["order_id"]

                filled_price = current_price
                for _ in range(60):
                    time.sleep(1)
                    info = self._exchange.get_order_status(oid)
                    if info["status"] == "filled":
                        filled_price = info.get("filled_price") or current_price
                        break
                    if info["status"] == "cancelled":
                        _log.warning("캐치업 주문 취소됨: %s", oid)
                        oid = None
                        break

                if oid is None:
                    continue

                self._current_round += 1
                save_catchup_order(
                    self._symbol, oid, "buy",
                    grid_price=intended_price,
                    filled_price=filled_price,
                    quantity=quantity,
                    amount=round(filled_price * quantity, 2),
                    round_no=self._current_round,
                )
                done += 1

            except Exception as exc:
                _log.error("캐치업 매수 실패 (%d/%d): %s", done + 1, n, exc)
                break

        if done > 0:
            self._reference_price = current_price
            upsert_state(self._symbol,
                         reference_price=current_price,
                         current_round=self._current_round)
            _log.info("캐치업 완료: %s %d회, 새 기준가=%s, 차수=%d",
                      self._symbol, done, current_price, self._current_round)
            self._notify("catchup", count=done, gap=gap, price=current_price)
