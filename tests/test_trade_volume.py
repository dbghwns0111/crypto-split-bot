"""가상 캐치업 매수가 실거래대금 집계에서 제외되는지 검증.

빗썸 실제 거래대금과 웹 화면의 "누적 거래금액"/"총 매수금액"이 어긋났던 문제 —
가상 캐치업(order_id 'virt-' 접두)은 거래소에 실제 주문을 보내지 않는데도
매수금액이 거래대금 집계에 포함되고 있었다. 손익(PnL) 계산 자체는 가상 매수가의
차수 기록이 있어야 정확하므로 그대로 유지하고, "거래대금" 성격의 집계에서만
가상 매수분을 제외한다.
"""
from unittest.mock import MagicMock

import pytest

from database.connection import close_connection, configure, get_connection
from database.models import create_tables
from database.order_repo import (
    get_cumulative_totals,
    get_pnl_rounds,
    save_catchup_order,
    save_order,
    update_order_fill,
)


@pytest.fixture(autouse=True)
def fresh_db():
    configure(":memory:")
    conn = get_connection()
    create_tables(conn)
    yield conn
    close_connection()
    configure(None)


def _insert_real_order(symbol, order_id, side, price, qty, round_no, filled_date):
    save_order(symbol, order_id, side, price, qty, round(price * qty, 2), round_no)
    update_order_fill(order_id, price, qty, filled_at=f"{filled_date} 10:00:00")


def _insert_virtual_buy(symbol, round_no, price, qty, filled_date, seq=1):
    order_id = f"virt-{symbol}-r{round_no}-{seq}"
    save_catchup_order(
        symbol, order_id, "buy",
        grid_price=price, filled_price=price, quantity=qty,
        amount=round(price * qty, 2), round_no=round_no,
        filled_at=f"{filled_date} 09:00:00",
    )
    return order_id


# ═══════════════════════════════════════════════════════════════════════════════
# get_pnl_rounds — buy_is_virtual 플래그
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetPnlRoundsVirtualFlag:
    def test_real_round_not_flagged_virtual(self):
        _insert_real_order("ETH", "ord-buy-1", "buy", 3_000_000, 0.01, 1, "2026-07-01")
        _insert_real_order("ETH", "ord-sell-1", "sell", 3_100_000, 0.01, 1, "2026-07-02")

        rows = get_pnl_rounds(["ETH"], "2026-07-01", "2026-07-31")
        assert len(rows) == 1
        assert rows[0]["buy_is_virtual"] is False
        assert rows[0]["buy_amount"] == pytest.approx(30_000)

    def test_virtual_catchup_round_flagged_virtual(self):
        _insert_virtual_buy("ETH", 1, 3_000_000, 0.01, "2026-07-01")
        _insert_real_order("ETH", "ord-sell-1", "sell", 3_100_000, 0.01, 1, "2026-07-02")

        rows = get_pnl_rounds(["ETH"], "2026-07-01", "2026-07-31")
        assert len(rows) == 1
        assert rows[0]["buy_is_virtual"] is True
        # PnL 계산 자체는 가상 매수가를 그대로 사용 — 손익 정확도는 유지
        assert rows[0]["buy_amount"] == pytest.approx(30_000)
        assert rows[0]["pnl"] is not None


# ═══════════════════════════════════════════════════════════════════════════════
# get_cumulative_totals — 가상 매수 제외
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCumulativeTotalsExcludesVirtual:
    def test_real_buy_counted(self):
        _insert_real_order("ETH", "ord-buy-1", "buy", 3_000_000, 0.01, 1, "2026-07-01")
        _insert_real_order("ETH", "ord-sell-1", "sell", 3_100_000, 0.01, 1, "2026-07-02")

        rows = get_cumulative_totals("2026-07-01", "2026-07-31")
        eth = next(r for r in rows if r["symbol"] == "ETH")
        assert eth["total_buy"] == pytest.approx(30_000)
        assert eth["total_sell"] == pytest.approx(31_000)
        assert eth["buy_count"] == 1

    def test_virtual_buy_excluded_from_total_buy(self):
        _insert_virtual_buy("ETH", 1, 3_000_000, 0.01, "2026-07-01")
        _insert_real_order("ETH", "ord-sell-1", "sell", 3_100_000, 0.01, 1, "2026-07-02")

        rows = get_cumulative_totals("2026-07-01", "2026-07-31")
        eth = next(r for r in rows if r["symbol"] == "ETH")
        assert eth["total_buy"] == 0        # 가상 매수는 실제 거래대금이 아님
        assert eth["total_sell"] == pytest.approx(31_000)  # 매도는 실거래이므로 그대로
        assert eth["buy_count"] == 0

    def test_mixed_real_and_virtual_rounds(self):
        # 1차: 실제 매수+매도, 2차: 가상 매수+실제 매도
        _insert_real_order("ETH", "ord-buy-1", "buy", 3_000_000, 0.01, 1, "2026-07-01")
        _insert_real_order("ETH", "ord-sell-1", "sell", 3_100_000, 0.01, 1, "2026-07-02")
        _insert_virtual_buy("ETH", 2, 2_900_000, 0.01, "2026-07-03")
        _insert_real_order("ETH", "ord-sell-2", "sell", 3_000_000, 0.01, 2, "2026-07-04")

        rows = get_cumulative_totals("2026-07-01", "2026-07-31")
        eth = next(r for r in rows if r["symbol"] == "ETH")
        assert eth["total_buy"] == pytest.approx(30_000)          # 1차 실매수만
        assert eth["total_sell"] == pytest.approx(31_000 + 30_000)  # 매도 2건 모두 실거래
        assert eth["buy_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/trades/pnl — API 응답에 buy_is_virtual 포함
# ═══════════════════════════════════════════════════════════════════════════════

def _make_bot_manager():
    bm = MagicMock()
    bm.symbols = ["ETH", "USDT"]
    bm.get_all_status.side_effect = lambda: {s: {"running": False} for s in bm.symbols}
    return bm


@pytest.fixture
def client():
    from web.app import create_app
    bm = _make_bot_manager()
    flask_app = create_app(bm, MagicMock(), {})
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


class TestTradePnlApiVirtualFlag:
    def test_virtual_round_marked_in_response(self, client):
        _insert_virtual_buy("ETH", 1, 3_000_000, 0.01, "2026-07-01")
        _insert_real_order("ETH", "ord-sell-1", "sell", 3_100_000, 0.01, 1, "2026-07-02")

        resp = client.get("/api/trades/pnl?start=2026-07-01&end=2026-07-31&symbol=ETH")
        data = resp.get_json()
        assert len(data["rounds"]) == 1
        assert data["rounds"][0]["buy_is_virtual"] is True

    def test_real_round_not_marked_in_response(self, client):
        _insert_real_order("ETH", "ord-buy-1", "buy", 3_000_000, 0.01, 1, "2026-07-01")
        _insert_real_order("ETH", "ord-sell-1", "sell", 3_100_000, 0.01, 1, "2026-07-02")

        resp = client.get("/api/trades/pnl?start=2026-07-01&end=2026-07-31&symbol=ETH")
        data = resp.get_json()
        assert len(data["rounds"]) == 1
        assert data["rounds"][0]["buy_is_virtual"] is False
