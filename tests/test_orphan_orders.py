"""남은 봇 주문 정리 + 매도 코인 부족 알림 테스트."""

from unittest.mock import MagicMock

import pytest

from database.connection import close_connection, configure, get_connection
from database.models import create_tables
from database.order_repo import get_order, save_order, update_order
from order.canceller import clean_orphan_orders


@pytest.fixture(autouse=True)
def db():
    configure(":memory:")
    create_tables(get_connection())
    yield
    close_connection()
    configure(None)


class Ex:
    """빗썸 흉내: live = {oid: dict}, cancel 시 cancelled(또는 fill_on_cancel 이면 filled)."""

    def __init__(self, live, fill_on_cancel=()):
        self.live = {oid: dict(status="open", side=s, price=p, filled_quantity=0.0)
                     for oid, (s, p) in live.items()}
        self.fill_on_cancel = set(fill_on_cancel)
        self.cancelled = []

    def get_open_orders(self, ticker):
        return [{"order_id": k, **v} for k, v in self.live.items() if v["status"] == "open"]

    def get_order_status(self, oid):
        return dict(self.live[oid])

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        if oid in self.fill_on_cancel:
            self.live[oid].update(status="filled", filled_quantity=1.0)
        else:
            self.live[oid]["status"] = "cancelled"
        return True


def _run(ex, tracked=(), running=True):
    return clean_orphan_orders("USDT", "KRW-USDT", ex, lambda oid: oid in tracked, running)


def test_manual_order_never_touched():
    ex = Ex({"manual-1": ("buy", 1300)})
    assert _run(ex) == []
    assert ex.cancelled == []


def test_db_cancelled_but_live_is_cancelled():
    save_order("USDT", "bot-1", "buy", 1360, 18, 24480, 30)
    update_order("bot-1", "cancelled")
    ex = Ex({"bot-1": ("buy", 1360)})
    r = _run(ex)
    assert [x["outcome"] for x in r] == ["cancelled"]
    assert ex.cancelled == ["bot-1"]


def test_tracked_order_skipped():
    save_order("USDT", "bot-2", "sell", 1365, 18, 24570, 30)
    ex = Ex({"bot-2": ("sell", 1365)})
    assert _run(ex, tracked={"bot-2"}) == []
    assert ex.cancelled == []


def test_untracked_open_order_of_stopped_bot_left_for_reconcile():
    save_order("USDT", "bot-3", "sell", 1365, 18, 24570, 30)
    ex = Ex({"bot-3": ("sell", 1365)})
    assert _run(ex, running=False) == []
    assert get_order("bot-3")["status"] == "open"


def test_untracked_open_order_of_running_bot_cancelled_and_db_updated():
    save_order("USDT", "bot-4", "buy", 1359, 18, 24462, 31)
    ex = Ex({"bot-4": ("buy", 1359)})
    r = _run(ex)
    assert r[0]["outcome"] == "cancelled"
    assert get_order("bot-4")["status"] == "cancelled"


def test_becomes_tracked_during_recheck_is_skipped():
    """경합: 처음엔 추적 안 됨 → 재확인 시점엔 봇이 추적 중이면 취소하지 않는다."""
    save_order("USDT", "bot-5", "buy", 1359, 18, 24462, 31)
    ex = Ex({"bot-5": ("buy", 1359)})
    calls = {"n": 0}

    def is_tracked(oid):
        calls["n"] += 1
        return calls["n"] > 1

    assert clean_orphan_orders("USDT", "KRW-USDT", ex, is_tracked, True) == []
    assert ex.cancelled == []


def test_filled_during_cancel_reported_db_untouched():
    save_order("USDT", "bot-6", "sell", 1366, 18, 24588, 30)
    update_order("bot-6", "cancelled")
    ex = Ex({"bot-6": ("sell", 1366)}, fill_on_cancel={"bot-6"})
    r = _run(ex)
    assert r[0]["outcome"] == "filled"
    assert get_order("bot-6")["status"] == "cancelled"


# ── 매도 코인 부족 알림 ─────────────────────────────────────────────────────────

def test_sell_blocked_alert_once_then_reset(monkeypatch):
    from strategy.magic_split import MagicSplit
    import strategy.magic_split as ms

    ex = MagicMock()
    ex.get_balance.return_value = 5.0
    ex.sell_limit.return_value = {"order_id": "s-1"}
    cfg = {"pairs": {"USDT": {"ticker": "KRW-USDT", "max_rounds": 100, "order_amount": 25000,
                              "trigger_mode": "krw", "trigger_krw": 1.0, "trigger_pct": 0.5}},
           "strategy": {"price_poll_interval": 1, "order_timeout": 300, "sell_extra_pct": 0.08,
                        "circuit_breaker_pct": 5, "circuit_breaker_window": 300,
                        "trigger_min_pct": 0.3, "trigger_max_pct": 1.0}}
    notifier = MagicMock()
    bot = MagicSplit("USDT", ex, cfg, notifier)
    bot._reference_price, bot._current_round = 1360, 30
    monkeypatch.setattr(ms, "get_filled_buy_for_round", lambda s, r: {"quantity": 18.0})

    bot._place_sell(0.5, "krw", 1.0)   # 잔고 5 < 18 → 알림
    bot._place_sell(0.5, "krw", 1.0)   # 같은 상황 → 중복 알림 없음
    assert notifier.send_sell_blocked.call_count == 1
    kw = notifier.send_sell_blocked.call_args.kwargs
    assert kw["needed"] == 18.0 and kw["available"] == 5.0

    ex.get_balance.return_value = 100.0
    bot._place_sell(0.5, "krw", 1.0)   # 매도 성공 → 플래그 리셋
    assert bot._pending_sell["order_id"] == "s-1"
    assert bot._sell_balance_warned is False


def test_alert_builders_render():
    from notification.message_builder import build_orphan_order_alert, build_sell_blocked_alert
    assert "매도 보류" in build_sell_blocked_alert("USDT", "USDT", 18, 5)
    assert "남은 봇 주문" in build_orphan_order_alert("USDT", "buy", 1360, "x", "취소")
