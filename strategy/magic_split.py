"""MagicSplit 메인 루프 — Thread 실행, 1초 폴링, 체결 처리."""

import threading
import time
from typing import Optional

from database.connection import configure as db_configure, get_connection
from database.models import create_tables
from database.order_repo import (
    get_filled_buy_for_round,
    get_latest_filled_buy,
    get_open_orders,
    save_catchup_order,
    save_order,
    update_order,
    update_order_fill,
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
from portfolio.balance import check_buy_feasible, check_sell_feasible
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

        # 최대 차수 알림 중복 방지
        self._max_round_notified = False
        # 잔고 부족 알림 중복 방지 (잔고 회복 시 자동 리셋)
        self._balance_warned = False

        self._volatility      = VolatilityMeter(exchange, symbol, self._ticker, cfg)
        self._circuit_breaker = CircuitBreaker(symbol, cfg)

    # ── 생명주기 ──────────────────────────────────────────────────────────────

    def start(self, virtual_catchup: bool = False) -> None:
        """DB에서 이전 상태를 복구하고 루프 스레드를 시작한다."""
        if self._running:
            return
        self._circuit_breaker.reset()
        self._restore_state(virtual_catchup=virtual_catchup)
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
        self._reference_price    = 0.0
        self._current_round      = 0
        self._is_resume          = False
        self._pending_buy        = None
        self._pending_sell       = None
        self._max_round_notified = False
        self._balance_warned     = False
        upsert_state(self._symbol, reference_price=0.0, current_round=0, is_running=False)
        _log.info("상태 초기화: %s", self._symbol)

    def stop(self) -> None:
        """루프를 중지한다. 미체결 주문 취소는 호출자(BotManager)가 담당한다."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                _log.warning("봇 중지 타임아웃 — 스레드가 5초 후에도 실행 중: %s", self._symbol)
                if self._notifier:
                    self._notifier.send(f"⚠️ {self._symbol} 봇 중지 타임아웃\n스레드가 종료되지 않았습니다. 수동 확인이 필요합니다.")
        upsert_state(self._symbol, reference_price=self._reference_price,
                     current_round=self._current_round, is_running=False)
        _log.info("봇 중지: %s", self._symbol)

    def force_restart(self) -> None:
        """죽은 스레드를 정리하고 DB 상태로부터 재시작한다 (워치독 복구용)."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        _log.warning("워치독 복구: %s 스레드 재시작", self._symbol)
        self.start()  # _restore_state로 DB 최신 차수/기준가 복원 후 새 스레드 시작

    # ── 프로퍼티 ──────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_thread_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

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

    @property
    def start_price(self) -> float:
        return self._start_price

    @property
    def pending_buy_price(self) -> float:
        if self._pending_buy:
            return float(self._pending_buy.get("order_price", 0))
        if self._reference_price <= 0:
            return 0
        # 0차: 다음 매수는 reference_price - trigger가 아닌 start_price에서 발생
        if self._current_round == 0 and self._start_price > 0:
            return round_price(self._start_price)
        pair = self._cfg["pairs"][self._symbol]
        trigger_mode = pair.get("trigger_mode", "pct")
        if trigger_mode == "krw":
            return round_price(buy_order_price_krw(self._reference_price,
                                                   float(pair.get("trigger_krw", 10000))))
        return round_price(buy_order_price(self._reference_price,
                                           self._volatility.get_trigger_pct()))

    @property
    def pending_sell_price(self) -> float:
        if self._pending_sell:
            return float(self._pending_sell.get("order_price", 0))
        if self._reference_price <= 0 or self._current_round <= 0:
            return 0
        pair = self._cfg["pairs"][self._symbol]
        trigger_mode = pair.get("trigger_mode", "pct")
        if trigger_mode == "krw":
            return round_price(sell_order_price_krw(self._reference_price,
                                                    float(pair.get("trigger_krw", 10000))))
        return round_price(sell_order_price(self._reference_price,
                                            self._volatility.get_trigger_pct(),
                                            self._sell_extra_pct))

    @property
    def _current_order_amount(self) -> float:
        """cfg에서 현재 1회 주문금액을 동적으로 읽는다 (런타임 변경 즉시 반영)."""
        return float(self._cfg["pairs"][self._symbol].get("order_amount", self._order_amount))

    # ── 루프 ─────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        consecutive_errors = 0
        while self._running:
            try:
                self._tick()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                _log.error("루프 오류 (%d연속) (%s): %s", consecutive_errors, self._symbol, exc)
                if consecutive_errors >= 10:
                    if self._notifier:
                        self._notifier.send_api_error(
                            self._symbol,
                            f"10회 연속 오류로 주문이 중단됐습니다: {exc}",
                        )
                    consecutive_errors = 0
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
                # KRW 모드: 매도가 = 기준가 + 트리거 (그리드 정렬 유지)
                do_sell = should_sell_krw(current_price, self._reference_price, trigger_krw)
            else:
                do_sell = should_sell(current_price, self._reference_price,
                                      trigger_pct, self._sell_extra_pct)
            if do_sell:
                self._place_sell(trigger_pct, trigger_mode, trigger_krw)

        # ── 매수 (서킷브레이커 발동 시 중단) ─────────────────────────────────
        if not cb_active and self._pending_buy is None:
            if self._current_round >= self._max_rounds:
                self._notify_max_round()
            elif self._current_round == 0 and self._start_price > 0:
                # 0차: 시작가 이하 도달 시 시작가에서 즉시 매수
                if current_price <= self._start_price:
                    self._place_buy_at_start()
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
        quantity = calc_quantity(self._current_order_amount, price)
        if quantity <= 0:
            return
        feasible, reason = check_buy_feasible(
            self._exchange, self._current_order_amount,
            self._current_round, self._max_rounds,
        )
        if not feasible:
            _log.warning("매수 불가 (%s): %s", self._symbol, reason)
            if "KRW 잔고 부족" in reason and not self._balance_warned:
                self._balance_warned = True
                try:
                    krw = self._exchange.get_balance("KRW")
                except Exception:
                    krw = 0.0
                self._notify("low_balance", krw=krw, order_amount=self._current_order_amount)
            return
        try:
            result = self._exchange.buy_limit(self._ticker, price, quantity)
            oid = result["order_id"]
            if not oid:
                _log.error("매수 주문 실패: order_id 없음 (%s)", self._symbol)
                return
            self._pending_buy = {"order_id": oid, "placed_at": time.monotonic(),
                                 "order_price": price, "quantity": quantity}
            save_order(self._symbol, oid, "buy", price, quantity,
                       round(price * quantity, 2), self._current_round + 1)
            _log.info("매수 주문 전송: %s 가격=%d 수량=%s 금액=%d원",
                      self._symbol, price, quantity, int(price * quantity))
        except Exception as exc:
            _log.error("매수 주문 실패 (%s): %s", self._symbol, exc)

    def _place_buy_at_start(self) -> None:
        """0차 시작: 시작가에서 즉시 1차 매수 주문."""
        price = round_price(self._start_price)
        quantity = calc_quantity(self._current_order_amount, price)
        if quantity <= 0:
            return
        try:
            result = self._exchange.buy_limit(self._ticker, price, quantity)
            oid = result["order_id"]
            if not oid:
                _log.error("시작가 매수 실패: order_id 없음 (%s)", self._symbol)
                return
            self._pending_buy = {"order_id": oid, "placed_at": time.monotonic(),
                                 "order_price": price, "quantity": quantity}
            save_order(self._symbol, oid, "buy", price, quantity,
                       round(price * quantity, 2), 1)
            _log.info("시작가 매수 주문: %s 가격=%d 수량=%s", self._symbol, price, quantity)
        except Exception as exc:
            _log.error("시작가 매수 실패 (%s): %s", self._symbol, exc)

    def _place_sell(self, trigger_pct: float, trigger_mode: str = "pct", trigger_krw: float = 0.0) -> None:
        current_buy = get_filled_buy_for_round(self._symbol, self._current_round)
        if not current_buy:
            return
        quantity = current_buy["quantity"]
        if trigger_mode == "krw":
            # KRW 모드: 매도가 = 기준가 + 트리거 (그리드 정렬 유지)
            price = round_price(sell_order_price_krw(self._reference_price, trigger_krw))
        else:
            price = round_price(sell_order_price(self._reference_price,
                                                 trigger_pct, self._sell_extra_pct))
        ref = round_price(self._reference_price)
        if price <= ref:
            _log.warning("매도 생략: 반올림 후 가격(%d) ≤ 기준가(%d) — trigger가 tick 단위보다 작음",
                         price, ref)
            return
        feasible, reason = check_sell_feasible(self._exchange, self._symbol, quantity)
        if not feasible:
            _log.warning("매도 불가 (%s): %s", self._symbol, reason)
            return
        try:
            result = self._exchange.sell_limit(self._ticker, price, quantity)
            oid = result["order_id"]
            if not oid:
                _log.error("매도 주문 실패: order_id 없음 (%s)", self._symbol)
                return
            self._pending_sell = {"order_id": oid, "placed_at": time.monotonic(),
                                  "order_price": price, "quantity": quantity}
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
                order_price = (self._pending_buy or {}).get("order_price") or info.get("price", filled_price)
                self._on_buy_filled(filled_price, order_price, info.get("filled_quantity", 0))
            else:
                self._on_sell_filled(filled_price, info.get("filled_quantity", 0))

        elif info["status"] == "cancelled":
            update_order(order_id, "cancelled")
            if side == "buy":
                self._pending_buy = None
            else:
                self._pending_sell = None

    def _on_buy_filled(self, filled_price: float, order_price: float, qty: float) -> None:
        self._current_round += 1
        self._reference_price = float(order_price)  # 주문가 기준 (fill_price 드리프트 방지)
        self._pending_buy = None
        self._balance_warned = False  # 잔고 회복 후 다음 부족 상황에서 재알림하도록 리셋
        increment_round(self._symbol)
        set_reference_price(self._symbol, self._reference_price)
        _log.info("매수 체결: %s 체결가=%s 주문가=%s 차수=%d",
                  self._symbol, filled_price, order_price, self._current_round)
        self._notify("buy_filled", price=filled_price, qty=qty, round_no=self._current_round)
        if self._current_round >= self._max_rounds:
            self._notify_max_round()

    def _on_sell_filled(self, filled_price: float, qty: float) -> None:
        sold_round = self._current_round  # 알림용: 감소 전 저장
        buy_order = get_filled_buy_for_round(self._symbol, sold_round)
        buy_price = 0.0
        net_pnl   = 0.0
        if buy_order and buy_order.get("filled_price"):
            buy_price = float(buy_order["filled_price"])
            pnl = calc_pnl(buy_price, filled_price, qty, self._fee_rate, self._point_rate)
            net_pnl = pnl["net"]
            add_trade_pnl(self._symbol, pnl["net"], pnl["fee"], pnl["points"])

        self._current_round = max(0, self._current_round - 1)
        self._pending_sell = None
        if self._current_round < self._max_rounds:
            self._max_round_notified = False
        decrement_round(self._symbol)

        # 이전 차수 매수 주문가로 기준가 복원 (그리드 정렬 유지)
        if self._current_round > 0:
            prev_buy = get_filled_buy_for_round(self._symbol, self._current_round)
            self._reference_price = float(prev_buy["price"]) if prev_buy else float(round_price(filled_price))
        else:
            self._reference_price = 0.0  # 0차 복귀 → 다음 tick에서 start_price로 재초기화
        set_reference_price(self._symbol, self._reference_price)

        _log.info("매도 체결: %s 가격=%s 차수=%d 복원기준가=%s",
                  self._symbol, filled_price, self._current_round, self._reference_price)
        self._notify("sell_filled", price=filled_price, qty=qty,
                     round_no=sold_round, net_pnl=net_pnl, buy_price=buy_price)

    # ── 타임아웃 ──────────────────────────────────────────────────────────────

    def _handle_timeouts(self) -> None:
        now = time.monotonic()
        for attr, side in (("_pending_buy", "buy"), ("_pending_sell", "sell")):
            pending = getattr(self, attr)
            if not (pending and (now - pending["placed_at"]) > self._order_timeout):
                continue

            oid = pending["order_id"]
            # 취소 전 최종 체결 상태 확인 (부분 체결 정산용)
            try:
                info = self._exchange.get_order_status(oid)
            except Exception as exc:
                _log.error("타임아웃 상태조회 실패 (%s): %s", oid, exc)
                info = None

            ordered = float(pending.get("quantity", 0) or 0)
            filled  = float(info.get("filled_quantity", 0) or 0) if info else 0.0

            # (a) 폴링 사이 완전체결된 경우 — 취소하지 않고 체결 처리
            if info and (info["status"] == "filled" or (ordered > 0 and filled >= ordered * 0.999)):
                self._check_one_fill(oid, side)
                continue

            # 잔량 취소
            _log.info("주문 타임아웃 취소: %s (%s, 체결 %s/%s)", oid, side, filled, ordered)
            try:
                self._exchange.cancel_order(oid)
                update_order(oid, "cancelled")
            except Exception as exc:
                _log.error("타임아웃 취소 실패: %s", exc)

            # (b) 부분 체결: 체결분을 실제 체결로 정산
            if info and filled > 0:
                try:
                    self._settle_partial(side, info, filled, pending)
                except Exception as exc:
                    _log.error("부분 체결 정산 실패 (%s): %s", self._symbol, exc)

            setattr(self, attr, None)

    def _settle_partial(self, side: str, info: dict, filled_qty: float, pending: dict) -> None:
        """타임아웃 취소 시 이미 체결된 부분을 실제 체결로 정산한다.

        매수: 보유가 생겼으므로 차수를 진행 (DB 행을 체결 수량으로 보정).
        매도: 체결분의 PnL을 실현하고 차수 감소. 미매도 잔량은 경고로 가시화.
        """
        oid          = pending["order_id"]
        order_price  = pending.get("order_price") or 0.0
        filled_price = info.get("filled_price") or order_price or 0.0
        update_order_fill(oid, filled_price=filled_price, filled_qty=filled_qty)

        if side == "buy":
            self._on_buy_filled(filled_price, order_price or filled_price, filled_qty)
            self._notify("partial_fill", side="buy", qty=filled_qty, price=filled_price)
        else:
            ordered  = float(pending.get("quantity", filled_qty) or filled_qty)
            leftover = max(0.0, ordered - filled_qty)
            self._on_sell_filled(filled_price, filled_qty)
            self._notify("partial_fill", side="sell", qty=filled_qty,
                         price=filled_price, leftover=leftover)

    # ── 알림 헬퍼 ─────────────────────────────────────────────────────────────

    def _notify(self, event: str, **kwargs) -> None:
        if self._notifier is None:
            return
        try:
            fn = getattr(self._notifier, f"send_{event}", None)
            if fn:
                fn(symbol=self._symbol, **kwargs)
        except Exception as exc:
            _log.warning("텔레그램 알림 실패 (%s): %s", event, exc)

    def _notify_max_round(self) -> None:
        if self._max_round_notified:
            return
        _log.warning("최대 차수 도달: %s (%d차)", self._symbol, self._current_round)
        self._notify("max_round", round_no=self._current_round, max_rounds=self._max_rounds)
        self._max_round_notified = True

    # ── 상태 복구 ─────────────────────────────────────────────────────────────

    def _restore_state(self, virtual_catchup: bool = False) -> None:
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
                # ② 신규 시작(fresh start) 인데 기준가가 0인 경우: 시작가를 기준가로 사용
                is_fresh_start = (self._current_round == 0 and self._start_price > 0)
                if is_fresh_start and self._reference_price <= 0:
                    self._reference_price = self._start_price
                    _log.info("신규 시작 기준가 초기화: %s → %s", self._symbol, self._start_price)
                # ③ 캐치업: 가상(virtual_catchup=True) 또는 실제 시장 매수
                if self._reference_price > 0:
                    import math as _math
                    current_price = self._exchange.get_current_price(self._ticker)

                    # 시작가 기준 예상 총 차수에서 현재 차수를 뺀 목표 개수 계산
                    target_count = None
                    if self._start_price > 0 and current_price < self._start_price:
                        pair_cfg     = self._cfg["pairs"][self._symbol]
                        t_mode       = pair_cfg.get("trigger_mode", "pct")
                        t_krw        = float(pair_cfg.get("trigger_krw", 10000))
                        t_pct        = float(pair_cfg.get("trigger_pct", 0.5))
                        if t_mode == "krw":
                            exp_total = 1 + int((self._start_price - current_price) // t_krw)
                        else:
                            exp_total = 1 + int(_math.log(current_price / self._start_price)
                                                / _math.log(1 - t_pct / 100))
                        target_count = max(0, exp_total - self._current_round)

                    if virtual_catchup:
                        self._do_virtual_catchup(current_price,
                                                 fresh_start=is_fresh_start,
                                                 target_count=target_count)
                    else:
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
                        self._reference_price = float(order["price"])  # 주문가 기준
                        _log.info("매수 체결 복구: %s 주문가=%s 차수=%d",
                                  self._symbol, order["price"], self._current_round)
                    else:  # sell
                        latest_buy = get_latest_filled_buy(self._symbol)
                        if latest_buy and latest_buy.get("filled_price"):
                            pnl = calc_pnl(
                                float(latest_buy["filled_price"]),
                                filled_price, filled_qty,
                                self._fee_rate, self._point_rate,
                            )
                            add_trade_pnl(self._symbol, pnl["net"], pnl["fee"], pnl["points"])
                        self._current_round = max(0, self._current_round - 1)
                        if self._current_round > 0:
                            prev_buy = get_filled_buy_for_round(self._symbol, self._current_round)
                            self._reference_price = float(prev_buy["price"]) if prev_buy else float(round_price(filled_price))
                        else:
                            self._reference_price = 0.0
                        _log.info("매도 체결 복구: %s 가격=%s 차수=%d 복원기준가=%s",
                                  self._symbol, filled_price, self._current_round, self._reference_price)
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
            affordable  = int(float(balance_krw) // self._current_order_amount)
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
            quantity = calc_quantity(self._current_order_amount, current_price)
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

    def _do_virtual_catchup(
        self, current_price: float, fresh_start: bool = False, target_count: int = None
    ) -> None:
        """그리드 레벨을 가상 체결로 채운다 (실제 주문 없음).

        target_count가 지정되면 그 개수만큼 채우고, start_price 기반 그리드 가격을 사용한다.
        미지정 시 reference_price 기준으로 갭을 계산한다 (start_price 미설정 시 폴백).
        KRW 잔고 / catchup_max 제한을 받지 않는다.
        """
        if current_price <= 0 or self._reference_price <= 0:
            return

        import math as _math
        pair         = self._cfg["pairs"][self._symbol]
        trigger_mode = pair.get("trigger_mode", "pct")
        trigger_krw  = float(pair.get("trigger_krw", 10000))
        trigger_pct  = float(pair.get("trigger_pct", 0.5))

        if target_count is not None:
            gap = target_count
        else:
            # start_price 미설정 시 reference_price 기반 폴백
            if trigger_mode == "krw":
                gap = int((self._reference_price - current_price) // trigger_krw)
            else:
                step = self._reference_price * trigger_pct / 100
                gap  = int((self._reference_price - current_price) // step) if step > 0 else 0
            if fresh_start:
                gap += 1

        if gap <= 0:
            return

        room = self._max_rounds - self._current_round
        n    = min(gap, room)
        if n <= 0:
            _log.info("가상 캐치업 생략: %s (갭=%d, room=%d)", self._symbol, gap, room)
            return

        original_ref = self._reference_price
        _log.info("가상 캐치업 시작: %s %d/%d 차수 (기준가=%s → 현재가=%s)",
                  self._symbol, n, gap, original_ref, current_price)

        # 시작가 기반 그리드 가격 생성 (정합성 보장)
        grid_prices = []
        if self._start_price > 0:
            for i in range(n):
                k = self._current_round + i + 1  # 1차 = 시작가 레벨
                if trigger_mode == "krw":
                    gp = self._start_price - (k - 1) * trigger_krw
                else:
                    gp = self._start_price * ((1 - trigger_pct / 100) ** (k - 1))
                grid_prices.append(round_price(gp))
        else:
            # start_price 미설정 시 reference_price 기반 폴백
            g = self._reference_price
            if fresh_start and n >= 1:
                grid_prices.append(round_price(g))
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
        for grid_price in grid_prices:
            quantity = calc_quantity(self._current_order_amount, grid_price)
            if quantity <= 0:
                break
            self._current_round += 1
            virt_oid = f"virt-{self._symbol}-r{self._current_round}-{int(time.time())}"
            save_catchup_order(
                self._symbol, virt_oid, "buy",
                grid_price=float(grid_price),
                filled_price=float(grid_price),
                quantity=quantity,
                amount=round(float(grid_price) * quantity, 2),
                round_no=self._current_round,
            )
            done += 1

        if done > 0:
            last_grid = grid_prices[done - 1]
            self._reference_price = float(last_grid)
            upsert_state(self._symbol,
                         reference_price=self._reference_price,
                         current_round=self._current_round)
            _log.info("가상 캐치업 완료: %s %d회, 새 기준가=%s, 차수=%d",
                      self._symbol, done, self._reference_price, self._current_round)
            self._notify("virtual_catchup", count=done, gap=gap,
                         from_price=original_ref, current_price=current_price)
