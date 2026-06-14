"""11단계: main.py 엔트리포인트 테스트."""

import sys
from unittest.mock import MagicMock

import pytest

from database.connection import close_connection, configure, get_connection
from database.models import create_tables


# ── 공유 픽스처 ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    configure(":memory:")
    conn = get_connection()
    create_tables(conn)
    yield conn
    close_connection()
    configure(None)


def _mock_cfg(port: int = 5000):
    return {
        "pairs": {
            "ETH":  {"ticker": "KRW-ETH",  "enabled": True, "capital": 100,
                     "order_amount": 10, "max_rounds": 5, "trigger_pct": 0.5},
            "USDT": {"ticker": "KRW-USDT", "enabled": True, "capital": 100,
                     "order_amount": 10, "max_rounds": 5, "trigger_pct": 0.5},
        },
        "strategy": {
            "trigger_min_pct": 0.3, "trigger_max_pct": 1.0,
            "sell_extra_pct": 0.08,
            "zone_normal_pct": 40, "zone_defensive_pct": 40, "zone_emergency_pct": 20,
            "circuit_breaker_pct": 5.0, "circuit_breaker_window": 300,
            "order_timeout": 300, "price_poll_interval": 1,
        },
        "web":      {"host": "0.0.0.0", "port": port},
        "database": {"path": ":memory:"},
        "report":   {"hour": 0, "minute": 0},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# build_components
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildComponents:
    def _patch_and_build(self, monkeypatch, cfg=None):
        mock_exchange = MagicMock()
        mock_bm       = MagicMock()
        mock_bm.symbols = ["ETH", "USDT"]
        mock_notifier = MagicMock()
        mock_sched    = MagicMock()
        mock_app      = MagicMock()

        import main as m
        monkeypatch.setattr(m, "BithumbClient",         lambda *a, **kw: mock_exchange)
        monkeypatch.setattr(m, "BotManager",            lambda *a, **kw: mock_bm)
        monkeypatch.setattr(m, "TelegramNotifier",      lambda *a, **kw: mock_notifier)
        monkeypatch.setattr(m, "NotificationScheduler", lambda *a, **kw: mock_sched)
        monkeypatch.setattr(m, "create_app",            lambda *a, **kw: mock_app)

        monkeypatch.setenv("BITHUMB_ACCESS_KEY", "key")
        monkeypatch.setenv("BITHUMB_SECRET_KEY", "secret")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID",   "chat")

        result = m.build_components(cfg or _mock_cfg())
        return result, mock_exchange, mock_bm, mock_notifier, mock_sched, mock_app

    def test_returns_five_components(self, monkeypatch):
        result, *_ = self._patch_and_build(monkeypatch)
        assert len(result) == 5

    def test_exchange_instance_returned(self, monkeypatch):
        (exchange, *_), mock_exchange, *_ = self._patch_and_build(monkeypatch)
        assert exchange is mock_exchange

    def test_bot_manager_instance_returned(self, monkeypatch):
        (_, bm, *_), _, mock_bm, *_ = self._patch_and_build(monkeypatch)
        assert bm is mock_bm

    def test_bots_not_started(self, monkeypatch):
        _, _, mock_bm, *_ = self._patch_and_build(monkeypatch)
        mock_bm.start.assert_not_called()

    def test_notifier_receives_env_token(self, monkeypatch):
        received = {}

        def capture_notifier(token, chat_id):
            received["token"]   = token
            received["chat_id"] = chat_id
            return MagicMock()

        import main as m
        monkeypatch.setattr(m, "BithumbClient",         lambda *a, **kw: MagicMock())
        monkeypatch.setattr(m, "BotManager",            lambda *a, **kw: MagicMock(symbols=[]))
        monkeypatch.setattr(m, "TelegramNotifier",      capture_notifier)
        monkeypatch.setattr(m, "NotificationScheduler", lambda *a, **kw: MagicMock())
        monkeypatch.setattr(m, "create_app",            lambda *a, **kw: MagicMock())

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "my_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID",   "my_chat")

        m.build_components(_mock_cfg())
        assert received["token"]   == "my_token"
        assert received["chat_id"] == "my_chat"

    def test_flask_app_returned(self, monkeypatch):
        (*_, app), *_, mock_app = self._patch_and_build(monkeypatch)
        assert app is mock_app


# ═══════════════════════════════════════════════════════════════════════════════
# graceful_shutdown
# ═══════════════════════════════════════════════════════════════════════════════

class TestGracefulShutdown:
    def test_calls_stop_all(self):
        import main as m
        bm, sched = MagicMock(), MagicMock()
        m.graceful_shutdown(bm, sched)
        bm.stop_all.assert_called_once()

    def test_calls_sched_stop(self):
        import main as m
        bm, sched = MagicMock(), MagicMock()
        m.graceful_shutdown(bm, sched)
        sched.stop.assert_called_once()

    def test_stop_all_before_sched_stop(self):
        import main as m
        order = []
        bm, sched = MagicMock(), MagicMock()
        bm.stop_all.side_effect   = lambda: order.append("stop_all")
        sched.stop.side_effect    = lambda: order.append("sched_stop")
        m.graceful_shutdown(bm, sched)
        assert order == ["stop_all", "sched_stop"]

    def test_does_not_raise_if_stop_all_fails(self):
        import main as m
        bm, sched = MagicMock(), MagicMock()
        bm.stop_all.side_effect = RuntimeError("crash")
        m.graceful_shutdown(bm, sched)   # 예외 없이 통과

    def test_does_not_raise_if_sched_stop_fails(self):
        import main as m
        bm, sched = MagicMock(), MagicMock()
        sched.stop.side_effect = RuntimeError("crash")
        m.graceful_shutdown(bm, sched)   # 예외 없이 통과


# ═══════════════════════════════════════════════════════════════════════════════
# main()
# ═══════════════════════════════════════════════════════════════════════════════

class TestMain:
    def _run(self, monkeypatch, extra_argv=None, cfg_port=5000):
        """main()을 모의 환경에서 실행한다."""
        import atexit
        monkeypatch.setattr(sys,    "argv",     ["main.py"] + (extra_argv or []))
        monkeypatch.setattr(atexit, "register", lambda *a, **kw: None)

        mock_bm    = MagicMock()
        mock_bm.symbols     = ["ETH", "USDT"]
        mock_bm.stop_all.return_value = []
        mock_sched = MagicMock()
        mock_app   = MagicMock()
        cfg        = _mock_cfg(port=cfg_port)

        import main as m
        monkeypatch.setattr(m, "get_config",       lambda *a, **kw: cfg)
        monkeypatch.setattr(m, "db_configure",     lambda *a, **kw: None)
        monkeypatch.setattr(m, "get_connection",   lambda *a, **kw: MagicMock())
        monkeypatch.setattr(m, "create_tables",    lambda *a, **kw: None)
        monkeypatch.setattr(m, "build_components",
                            lambda *a, **kw: (MagicMock(), mock_bm, MagicMock(), mock_sched, mock_app))

        m.main()
        return mock_bm, mock_sched, mock_app

    def test_bots_not_auto_started(self, monkeypatch):
        mock_bm, *_ = self._run(monkeypatch)
        mock_bm.start.assert_not_called()

    def test_scheduler_auto_started(self, monkeypatch):
        _, mock_sched, _ = self._run(monkeypatch)
        mock_sched.start.assert_called_once()

    def test_app_run_called(self, monkeypatch):
        *_, mock_app = self._run(monkeypatch)
        mock_app.run.assert_called_once()

    def test_default_port_from_config(self, monkeypatch):
        *_, mock_app = self._run(monkeypatch, cfg_port=5000)
        kwargs = mock_app.run.call_args.kwargs
        assert kwargs["port"] == 5000

    def test_cli_port_overrides_config(self, monkeypatch):
        *_, mock_app = self._run(monkeypatch, extra_argv=["--port", "8080"])
        kwargs = mock_app.run.call_args.kwargs
        assert kwargs["port"] == 8080

    def test_use_reloader_is_false(self, monkeypatch):
        *_, mock_app = self._run(monkeypatch)
        kwargs = mock_app.run.call_args.kwargs
        assert kwargs["use_reloader"] is False

    def test_debug_false_by_default(self, monkeypatch):
        *_, mock_app = self._run(monkeypatch)
        kwargs = mock_app.run.call_args.kwargs
        assert kwargs["debug"] is False

    def test_debug_flag_passed_through(self, monkeypatch):
        *_, mock_app = self._run(monkeypatch, extra_argv=["--debug"])
        kwargs = mock_app.run.call_args.kwargs
        assert kwargs["debug"] is True
