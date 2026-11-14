"""서버 재기동 후 자동 재가동 테스트."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import auto_resume as ar
from database.connection import close_connection, configure, get_connection
from database.models import create_tables
from database.state_repo import get_desired_running_symbols, upsert_state


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    configure(":memory:")
    create_tables(get_connection())
    monkeypatch.setattr(ar, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(ar, "HEARTBEAT", tmp_path / ".last_alive")
    monkeypatch.setattr(ar, "SKIP_MARKER", tmp_path / ".no_auto_resume")
    monkeypatch.setattr(ar, "HISTORY", tmp_path / ".auto_resume_history")
    yield tmp_path
    close_connection()
    configure(None)


class FakeBM:
    def __init__(self, symbols=("BTC", "ETH")):
        self.bots = {s: SimpleNamespace(ticker=f"KRW-{s}") for s in symbols}
        self.running = set()
        self.started = []

    @property
    def symbols(self):
        return list(self.bots)

    def is_running(self, s):
        return s in self.running

    def start(self, s, fresh=False):
        assert fresh is False
        self.started.append(s)
        self.running.add(s)
        return True, "ok"


def _desire(*symbols):
    for s in symbols:
        upsert_state(s)
        get_connection().execute("UPDATE bot_state SET desired_running=1 WHERE symbol=?", (s,))
    get_connection().commit()


def _run(bm, downtime=30.0, skip=False, cfg=None, exchange=None, **kw):
    ex = exchange or MagicMock(get_current_price=MagicMock(return_value=100.0))
    notifier = MagicMock()
    r = ar.run_auto_resume(bm, ex, notifier, cfg or {"system": {}}, downtime, skip,
                           sleep=lambda s: None, **kw)
    return r, notifier


def test_resumes_only_user_enabled_symbols():
    _desire("BTC")
    bm = FakeBM()
    r, n = _run(bm)
    assert r["action"] == "resumed" and bm.started == ["BTC"]
    n.send_auto_resume.assert_called_once()


def test_skip_marker_holds():
    _desire("BTC")
    bm = FakeBM()
    r, n = _run(bm, skip=True)
    assert r["action"] == "held" and bm.started == []
    assert "-n" in n.send_auto_resume_held.call_args[0][1]


def test_long_downtime_holds():
    _desire("BTC")
    r, _ = _run(FakeBM(), downtime=7 * 3600)
    assert r["action"] == "held" and "시간" in r["reason"]


def test_unknown_downtime_holds():
    _desire("BTC")
    r, _ = _run(FakeBM(), downtime=None)
    assert r["action"] == "held"


def test_disabled_in_config_does_nothing():
    _desire("BTC")
    bm = FakeBM()
    r, n = _run(bm, cfg={"system": {"auto_resume_enabled": False}})
    assert r["action"] == "disabled" and bm.started == []
    n.send_auto_resume_held.assert_not_called()


def test_no_targets_is_silent():
    r, n = _run(FakeBM())
    assert r["action"] == "none"
    n.send_auto_resume_held.assert_not_called()


def test_waits_for_network_then_resumes():
    _desire("ETH")
    ex = MagicMock()
    ex.get_current_price.side_effect = [Exception("down"), Exception("down"), 100.0]
    bm = FakeBM()
    r, _ = _run(bm, exchange=ex)
    assert r["action"] == "resumed" and bm.started == ["ETH"]


def test_network_never_returns_holds():
    _desire("ETH")
    ex = MagicMock(get_current_price=MagicMock(side_effect=Exception("down")))
    r, _ = _run(FakeBM(), exchange=ex, wait_timeout=60, poll_interval=15)
    assert r["action"] == "held" and "인터넷" in r["reason"]


def test_restart_loop_holds_after_three():
    _desire("BTC")
    for _ in range(3):
        bm = FakeBM()
        assert _run(bm)[0]["action"] == "resumed"
    r, _ = _run(FakeBM())
    assert r["action"] == "held" and "반복" in r["reason"]


def test_heartbeat_and_skip_marker_files(env):
    assert ar.downtime_seconds() is None
    ar.touch_heartbeat()
    assert ar.downtime_seconds() < 5
    assert ar.consume_skip_marker() is False
    (env / ".no_auto_resume").touch()
    assert ar.consume_skip_marker() is True
    assert not (env / ".no_auto_resume").exists()


# ── BotManager: 사용자 시작/중지 vs 프로세스 종료 ───────────────────────────────

def _bm():
    from bot_manager import BotManager
    cfg = {"pairs": {"BTC": {"ticker": "KRW-BTC", "max_rounds": 10, "order_amount": 10000,
                             "trigger_mode": "krw", "trigger_krw": 1000, "trigger_pct": 0.5}},
           "strategy": {"price_poll_interval": 1, "order_timeout": 300, "sell_extra_pct": 0.08,
                        "circuit_breaker_pct": 5, "circuit_breaker_window": 300,
                        "trigger_min_pct": 0.3, "trigger_max_pct": 1.0}}
    bm = BotManager(MagicMock(), cfg, None)
    bot = MagicMock(is_running=False)
    bm._bots["BTC"] = bot
    upsert_state("BTC")
    return bm, bot


def test_user_start_sets_desired_user_stop_clears():
    bm, bot = _bm()
    assert bm.start("BTC")[0]
    assert get_desired_running_symbols() == ["BTC"]
    bot.is_running = True
    assert bm.stop("BTC")[0]
    assert get_desired_running_symbols() == []


def test_shutdown_keeps_desired():
    bm, bot = _bm()
    bm.start("BTC")
    bot.is_running = True
    assert bm.shutdown_all() == ["BTC"]
    assert get_desired_running_symbols() == ["BTC"]


def test_reset_clears_desired():
    bm, bot = _bm()
    bm.start("BTC")
    bot.is_running = False
    bm.reset("BTC")
    assert get_desired_running_symbols() == []


def test_migration_seeds_desired_from_is_running(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE bot_state (symbol TEXT PRIMARY KEY, reference_price REAL NOT NULL DEFAULT 0,
                 current_round INTEGER NOT NULL DEFAULT 0, is_running INTEGER NOT NULL DEFAULT 0,
                 circuit_breaker_on INTEGER NOT NULL DEFAULT 0, cb_until TEXT, updated_at TEXT NOT NULL)""")
    c.execute("INSERT INTO bot_state(symbol,is_running,updated_at) VALUES ('ETH',1,'x'),('XRP',0,'x')")
    c.commit()
    create_tables(c)
    assert c.execute("SELECT symbol FROM bot_state WHERE desired_running=1").fetchall() == [("ETH",)]
