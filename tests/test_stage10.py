"""10단계: 웹 대시보드 테스트."""

import json
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest

from database.connection import close_connection, configure, get_connection
from database.models import create_tables
from database.order_repo import save_order, update_order_fill
from database.pnl_repo import add_trade_pnl, get_daily_pnl
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
    bm.start.assert_called_once_with("ETH", fresh=False, virtual_catchup=False)


def test_start_bot_symbol_uppercased(client):
    c, bm, ex = client
    c.post("/api/bot/start",
           data=json.dumps({"symbol": "eth"}),
           content_type="application/json")
    bm.start.assert_called_once_with("ETH", fresh=False, virtual_catchup=False)


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


def test_pnl_today_net_pnl_is_realized_minus_fee(client):
    c, bm, ex = client
    add_trade_pnl("ETH", 5_000, 500)
    data = c.get("/api/pnl/today").get_json()
    assert data["net_pnl"] == 4_500


def test_pnl_today_totals_sum_todays_filled_orders(client):
    c, bm, ex = client
    from utils.time_utils import today_kst_str
    today = today_kst_str()
    _insert_filled_order("ETH", "ord-t1", "buy",  3_000_000, 0.01, 1, today)
    _insert_filled_order("ETH", "ord-t2", "sell", 3_100_000, 0.01, 1, today)
    data = c.get("/api/pnl/today").get_json()
    assert data["total_buy"]  == 30_000
    assert data["total_sell"] == 31_000


def test_pnl_today_totals_exclude_virtual_catchup(client):
    c, bm, ex = client
    from utils.time_utils import today_kst_str
    today = today_kst_str()
    _insert_filled_order("ETH", "virt-1", "buy", 3_000_000, 0.01, 1, today)
    data = c.get("/api/pnl/today").get_json()
    assert data["total_buy"] == 0


def test_pnl_today_totals_exclude_other_days(client):
    c, bm, ex = client
    _insert_filled_order("ETH", "ord-old", "buy", 3_000_000, 0.01, 1, "2026-01-01")
    data = c.get("/api/pnl/today").get_json()
    assert data["total_buy"] == 0


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


def test_dashboard_has_symbol_data_attr(client):
    # 코인 목록은 JS(dashboard.js)가 런타임에 그리므로, 서버는
    # #cs-symbols의 data-symbols 속성으로 심볼 목록만 전달한다.
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="cs-symbols"' in html
    assert 'data-symbols="ETH,USDT"' in html


def test_dashboard_has_market_price_list(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="market-price-list"' in html


def test_dashboard_has_bulk_start_stop_buttons(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="bulk-start-btn"' in html
    assert 'id="bulk-stop-btn"'  in html


def test_dashboard_has_assets_section(client):
    c, bm, ex = client
    html = c.get("/").data.decode()
    assert 'id="assets-krw-avail"' in html


def test_dashboard_single_symbol(fresh_db):
    from web.app import create_app
    bm = _make_bot_manager(symbols=["ETH"])
    ex = _make_exchange()
    app = create_app(bm, ex, {})
    app.config["TESTING"] = True
    with app.test_client() as c:
        html = c.get("/").data.decode()
        assert 'data-symbols="ETH"' in html


# ═══════════════════════════════════════════════════════════════════════════════
# DELETE /api/trades/orders
# ═══════════════════════════════════════════════════════════════════════════════

def _insert_filled_order(symbol, order_id, side, price, qty, round_no, filled_date):
    row_id = save_order(symbol, order_id, side, price, qty, round(price * qty, 2), round_no)
    update_order_fill(order_id, price, qty, filled_at=f"{filled_date} 10:00:00")
    return row_id


def test_delete_orders_by_ids_removes_rows(client):
    c, bm, ex = client
    row_id = _insert_filled_order("ETH", "ord-1", "buy", 3_000_000, 0.01, 1, "2026-07-01")
    resp = c.delete("/api/trades/orders",
                     data=json.dumps({"ids": [row_id]}),
                     content_type="application/json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] == 1


def test_delete_orders_by_ids_resets_daily_pnl(client):
    c, bm, ex = client
    row_id = _insert_filled_order("ETH", "ord-2", "sell", 3_100_000, 0.01, 1, "2026-07-02")
    add_trade_pnl("ETH", 5_000, 500, date="2026-07-02")
    assert get_daily_pnl("ETH", "2026-07-02") is not None

    c.delete("/api/trades/orders",
             data=json.dumps({"ids": [row_id]}),
             content_type="application/json")

    assert get_daily_pnl("ETH", "2026-07-02") is None


def test_delete_orders_by_range_requires_dates(client):
    c, bm, ex = client
    resp = c.delete("/api/trades/orders",
                     data=json.dumps({"symbol": "ETH"}),
                     content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_delete_orders_by_range_filters_by_symbol(client):
    c, bm, ex = client
    _insert_filled_order("ETH",  "ord-3", "buy", 3_000_000, 0.01, 1, "2026-07-03")
    _insert_filled_order("USDT", "ord-4", "buy", 1_380, 10, 1, "2026-07-03")

    resp = c.delete("/api/trades/orders",
                     data=json.dumps({"start": "2026-07-01", "end": "2026-07-10", "symbol": "ETH"}),
                     content_type="application/json")
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] == 1  # USDT 주문은 삭제되지 않음


def test_delete_orders_no_ids_or_range_deletes_nothing(client):
    c, bm, ex = client
    resp = c.delete("/api/trades/orders",
                     data=json.dumps({"ids": []}),
                     content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/settings — 시크릿 마스킹
# ═══════════════════════════════════════════════════════════════════════════════

def _secrets_cfg():
    return {
        "exchange": {"access_key": "AKIA1234567890", "secret_key": "SECRETVALUE123", "membership": "화이트"},
        "telegram": {"bot_token": "123456:ABCDEFghijk", "chat_id": "999999"},
    }


@pytest.fixture
def client_with_secrets(fresh_db):
    from web.app import create_app
    bm = _make_bot_manager()
    ex = _make_exchange()
    flask_app = create_app(bm, ex, _secrets_cfg())
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_get_settings_masks_access_key(client_with_secrets):
    data = client_with_secrets.get("/api/settings").get_json()
    assert data["exchange"]["access_key"] == "AKIA****"
    assert "1234567890" not in data["exchange"]["access_key"]


def test_get_settings_masks_secret_key_fully(client_with_secrets):
    data = client_with_secrets.get("/api/settings").get_json()
    assert data["exchange"]["secret_key"] == "****"


def test_get_settings_masks_bot_token(client_with_secrets):
    data = client_with_secrets.get("/api/settings").get_json()
    assert data["telegram"]["bot_token"] == "123456****"


def test_get_settings_empty_key_masks_to_empty_string(client):
    c, bm, ex = client
    data = c.get("/api/settings").get_json()
    assert data["exchange"]["access_key"] == ""
    assert data["exchange"]["secret_key"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/settings — .env / settings.yaml 쓰기 (실제 파일은 건드리지 않음)
# ═══════════════════════════════════════════════════════════════════════════════

def test_save_settings_env_field_calls_dotenv_set_key(client):
    c, bm, ex = client
    try:
        with patch("dotenv.set_key") as mock_set_key:
            resp = c.post("/api/settings",
                          data=json.dumps({"access_key": "AKIA1234567890"}),
                          content_type="application/json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["needs_restart"] is True

        mock_set_key.assert_called_once()
        call_env_key, call_value = mock_set_key.call_args[0][1], mock_set_key.call_args[0][2]
        assert call_env_key == "BITHUMB_ACCESS_KEY"
        assert call_value   == "AKIA1234567890"
        # 프로세스 환경변수에도 즉시 반영되는지 확인
        assert os.environ.get("BITHUMB_ACCESS_KEY") == "AKIA1234567890"
    finally:
        os.environ.pop("BITHUMB_ACCESS_KEY", None)


def test_save_settings_blank_env_field_skipped(client):
    c, bm, ex = client
    with patch("dotenv.set_key") as mock_set_key:
        resp = c.post("/api/settings",
                      data=json.dumps({"access_key": ""}),
                      content_type="application/json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["needs_restart"] is False
    mock_set_key.assert_not_called()


def test_save_settings_yaml_field_writes_new_value(client):
    c, bm, ex = client
    existing = {"strategy": {"order_timeout": 300, "price_poll_interval": 1}}
    m = mock_open()
    with patch("builtins.open", m), \
         patch("yaml.safe_load", return_value=existing), \
         patch("yaml.dump") as mock_dump:
        resp = c.post("/api/settings",
                      data=json.dumps({"order_timeout": 600}),
                      content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    dumped = mock_dump.call_args[0][0]
    assert dumped["strategy"]["order_timeout"] == 600


def test_save_settings_yaml_write_failure_returns_500(client):
    c, bm, ex = client
    with patch("builtins.open", side_effect=OSError("disk full")):
        resp = c.post("/api/settings",
                      data=json.dumps({"order_timeout": 600}),
                      content_type="application/json")
    assert resp.status_code == 500
    assert resp.get_json()["ok"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/strategy/settings — 전략 설정 yaml 반영 (실제 파일은 건드리지 않음)
# ═══════════════════════════════════════════════════════════════════════════════

def _strategy_cfg():
    return {
        "pairs": {
            "ETH": {
                "ticker": "KRW-ETH", "trigger_mode": "pct", "trigger_pct": 0.5,
                "trigger_krw": 15000.0, "max_rounds": 50, "order_amount": 10000,
            },
        },
    }


@pytest.fixture
def client_with_strategy_cfg(fresh_db):
    from web.app import create_app
    bm = _make_bot_manager()
    ex = _make_exchange()
    flask_app = create_app(bm, ex, _strategy_cfg())
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c, bm


def test_strategy_settings_missing_symbol_returns_400(client_with_strategy_cfg):
    c, bm = client_with_strategy_cfg
    resp = c.post("/api/strategy/settings",
                  data=json.dumps({}),
                  content_type="application/json")
    assert resp.status_code == 400


def test_strategy_settings_unknown_symbol_returns_400(client_with_strategy_cfg):
    c, bm = client_with_strategy_cfg
    resp = c.post("/api/strategy/settings",
                  data=json.dumps({"symbol": "DOGE", "max_rounds": 10}),
                  content_type="application/json")
    assert resp.status_code == 400


def test_strategy_settings_updates_yaml_and_memory_cfg(client_with_strategy_cfg):
    c, bm = client_with_strategy_cfg
    existing = {"pairs": {"ETH": {"trigger_mode": "pct", "trigger_pct": 0.5,
                                   "trigger_krw": 15000.0, "max_rounds": 50,
                                   "order_amount": 10000}}}
    m = mock_open()
    with patch("builtins.open", m), \
         patch("yaml.safe_load", return_value=existing), \
         patch("yaml.dump") as mock_dump:
        resp = c.post("/api/strategy/settings",
                      data=json.dumps({"symbol": "ETH", "max_rounds": 80}),
                      content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    dumped = mock_dump.call_args[0][0]
    assert dumped["pairs"]["ETH"]["max_rounds"] == 80


def test_strategy_settings_yaml_failure_returns_500(client_with_strategy_cfg):
    c, bm = client_with_strategy_cfg
    with patch("builtins.open", side_effect=OSError("disk full")):
        resp = c.post("/api/strategy/settings",
                      data=json.dumps({"symbol": "ETH", "max_rounds": 80}),
                      content_type="application/json")
    assert resp.status_code == 500
    assert resp.get_json()["ok"] is False
