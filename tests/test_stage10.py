"""10단계: 웹 대시보드 테스트."""

import json
from unittest.mock import MagicMock, patch

import pytest

from database.connection import close_connection, configure, get_connection
from database.models import create_tables
from database.pnl_repo import add_trade_pnl
from web.routes.api import clear_price_cache


# ── 픽스처 ────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    configure(":memory:")
    conn = get_connection()
    create_tables(conn)
    yield conn
    close_connection()
    configure(None)
    clear_price_cache()


def _make_bot_manager(symbols=None, running=False):
    bm = MagicMock()
    bm.symbols = symbols or ["ETH", "USDT"]

    def _status(sym):
        return {
            "symbol": sym,
            "ticker": f"KRW-{sym}",
            "running": running,
            "current_round": 3 if running else 0,
            "reference_price": 3_000_000.0 if running else 0.0,
            "circuit_breaker_on": False,
        }

    bm.get_status.side_effect    = _status
    bm.get_all_status.side_effect = lambda: {s: _status(s) for s in bm.symbols}
    bm.start.return_value    = (True,  "ETH 봇이 시작되었습니다.")
    bm.stop.return_value     = (True,  "ETH 봇이 중지되었습니다.")
    bm.stop_all.return_value = ["ETH", "USDT"]
    return bm


def _make_exchange():
    ex = MagicMock()
    ex.get_current_price.return_value = 3_245_000.0
    return ex


@pytest.fixture
def app():
    from web.app import create_app
    bm  = _make_bot_manager()
    ex  = _make_exchange()
    flask_app = create_app(bm, ex, {})
    flask_app.config["TESTING"] = True
    return flask_app, bm, ex


@pytest.fixture
def client(app):
    flask_app, bm, ex = app
    with flask_app.test_client() as c:
        yield c, bm, ex


@pytest.fixture
def app_no_exchange():
    from web.app import create_app
    bm  = _make_bot_manager()
    flask_app = create_app(bm, None, {})
    flask_app.config["TESTING"] = True
    return flask_app, bm


@pytest.fixture
def client_no_exchange(app_no_exchange):
    flask_app, bm = app_no_exchange
    with flask_app.test_client() as c:
        yield c, bm


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/status
# ═══════════════════════════════════════════════════════════════════════════════

def test_status_returns_200(client):
    c, bm, ex = client
    resp = c.get("/api/status")
    assert resp.status_code == 200


def test_status_contains_all_symbols(client):
    c, bm, ex = client
    data = c.get("/api/status").get_json()
    assert "ETH"  in data
    assert "USDT" in data


def test_status_has_required_fields(client):
    c, bm, ex = client
    data = c.get("/api/status").get_json()
    eth = data["ETH"]
    for field in ("symbol", "ticker", "running", "current_price", "today_pnl", "today_trades"):
        assert field in eth, f"missing field: {field}"


def test_status_current_price_from_exchange(client):
    c, bm, ex = client
    data = c.get("/api/status").get_json()
    assert data["ETH"]["current_price"] == 3_245_000.0


def test_status_current_price_zero_without_exchange(client_no_exchange):
    c, bm = client_no_exchange
    data = c.get("/api/status").get_json()
    assert data["ETH"]["current_price"] == 0


def test_status_includes_today_pnl(client):
    c, bm, ex = client
    add_trade_pnl("ETH", 10_000, 1_000)
    data = c.get("/api/status").get_json()
    assert data["ETH"]["today_pnl"] == 10_000.0


def test_status_price_error_fallback_to_zero(client):
    c, bm, ex = client
    ex.get_current_price.side_effect = RuntimeError("API down")
    data = c.get("/api/status").get_json()
    assert data["ETH"]["current_price"] == 0


def test_status_price_cached_on_second_call(client):
    c, bm, ex = client
    c.get("/api/status")
    c.get("/api/status")
    # exchange는 캐시 TTL 내에서 한 번만 호출
    assert ex.get_current_price.call_count == len(bm.symbols)


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/bot/start
# ═══════════════════════════════════════════════════════════════════════════════

def test_start_bot_success(client):
    c, bm, ex = client
    resp = c.post("/api/bot/start",
                  data=json.dumps({"symbol": "ETH"}),
                  content_type="application/json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


def test_start_bot_calls_manager(client):
    c, bm, ex = client
    c.post("/api/bot/start",
           data=json.dumps({"symbol": "ETH"}),
           content_type="application/json")
    bm.start.assert_called_once_with("ETH")


def test_start_bot_symbol_uppercased(client):
    c, bm, ex = client
    c.post("/api/bot/start",
           data=json.dumps({"symbol": "eth"}),
           content_type="application/json")
    bm.start.assert_called_once_with("ETH")


def test_start_bot_missing_symbol_returns_400(client):
    c, bm, ex = client
    resp = c.post("/api/bot/start",
                  data=json.dumps({}),
                  content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_start_bot_empty_body_returns_400(client):
    c, bm, ex = client
    resp = c.post("/api/bot/start",
                  data="",
                  content_type="application/json")
    assert resp.status_code == 400


def test_start_bot_manager_returns_false(client):
    c, bm, ex = client
    bm.start.return_value = (False, "이미 실행 중")
    resp = c.post("/api/bot/start",
                  data=json.dumps({"symbol": "ETH"}),
                  content_type="application/json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "이미 실행 중" in data["message"]


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/bot/stop
# ═══════════════════════════════════════════════════════════════════════════════

def test_stop_bot_success(client):
    c, bm, ex = client
    resp = c.post("/api/bot/stop",
                  data=json.dumps({"symbol": "ETH"}),
                  content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_stop_bot_calls_manager(client):
    c, bm, ex = client
    c.post("/api/bot/stop",
           data=json.dumps({"symbol": "USDT"}),
           content_type="application/json")
    bm.stop.assert_called_once_with("USDT")


def test_stop_bot_missing_symbol_returns_400(client):
    c, bm, ex = client
    resp = c.post("/api/bot/stop",
                  data=json.dumps({}),
                  content_type="application/json")
    assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/bot/stop_all
# ═══════════════════════════════════════════════════════════════════════════════

def test_stop_all_returns_200(client):
    c, bm, ex = client
    resp = c.post("/api/bot/stop_all")
    assert resp.status_code == 200


def test_stop_all_returns_stopped_list(client):
    c, bm, ex = client
    data = c.post("/api/bot/stop_all").get_json()
    assert data["ok"] is True
    assert set(data["stopped"]) == {"ETH", "USDT"}
    assert data["count"] == 2


def test_stop_all_calls_manager(client):
    c, bm, ex = client
    c.post("/api/bot/stop_all")
    bm.stop_all.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/pnl/today
# ═══════════════════════════════════════════════════════════════════════════════

def test_pnl_today_returns_200(client):
    c, bm, ex = client
    assert c.get("/api/pnl/today").status_code == 200


def test_pnl_today_has_date_field(client):
    c, bm, ex = client
    data = c.get("/api/pnl/today").get_json()
    assert "date" in data


def test_pnl_today_has_symbols_field(client):
    c, bm, ex = client
    data = c.get("/api/pnl/today").get_json()
    assert "symbols" in data
    assert isinstance(data["symbols"], list)


def test_pnl_today_includes_pnl_data(client):
    c, bm, ex = client
    add_trade_pnl("ETH", 5_000, 500)
    data = c.get("/api/pnl/today").get_json()
    eth_pnl = next((s for s in data["symbols"] if s["symbol"] == "ETH"), None)
    assert eth_pnl is not None
    assert eth_pnl["realized_pnl"] == 5_000.0


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/portfolio
# ═══════════════════════════════════════════════════════════════════════════════

def test_portfolio_returns_200(client):
    c, bm, ex = client
    with patch("web.routes.api.take_snapshot", return_value={"KRW": 500_000.0, "ETH": 0.142}):
        resp = c.get("/api/portfolio")
    assert resp.status_code == 200


def test_portfolio_returns_snapshot(client):
    c, bm, ex = client
    snap = {"KRW": 512_000.0, "ETH": 0.142, "USDT": 1850.0}
    with patch("web.routes.api.take_snapshot", return_value=snap):
        data = c.get("/api/portfolio").get_json()
    assert data["KRW"] == 512_000.0


def test_portfolio_without_exchange_returns_503(client_no_exchange):
    c, bm = client_no_exchange
    resp = c.get("/api/portfolio")
    assert resp.status_code == 503


def test_portfolio_snapshot_error_returns_500(client):
    c, bm, ex = client
    with patch("web.routes.api.take_snapshot", side_effect=RuntimeError("fail")):
        resp = c.get("/api/portfolio")
    assert resp.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════════
# GET / (대시보드 HTML)
# ═══════════════════════════════════════════════════════════════════════════════

def test_dashboard_returns_200(client):
    c, bm, ex = client
    assert c.get("/").status_code == 200


def test_dashboard_contains_eth(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert "ETH" in html


def test_dashboard_contains_usdt(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert "USDT" in html


def test_dashboard_contains_confirm_modal(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert "confirmModal" in html
    assert "confirmBtn"   in html


def test_dashboard_loads_dashboard_js(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert "dashboard.js" in html


def test_dashboard_contains_bootstrap(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert "bootstrap" in html.lower()


def test_dashboard_has_status_badge_placeholders(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="badge-ETH"'  in html
    assert 'id="badge-USDT"' in html


def test_dashboard_has_price_placeholders(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="price-ETH"'  in html
    assert 'id="price-USDT"' in html


def test_dashboard_has_start_stop_buttons(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="btn-ETH"'  in html
    assert 'id="btn-USDT"' in html


def test_dashboard_has_portfolio_section(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="bal-KRW"' in html


def test_dashboard_single_symbol(fresh_db):
    from web.app import create_app
    bm = _make_bot_manager(symbols=["ETH"])
    ex = _make_exchange()
    app = create_app(bm, ex, {})
    app.config["TESTING"] = True
    with app.test_client() as c:
        html = c.get("/").data.decode()
        assert "ETH" in html
        assert "USDT" not in html
