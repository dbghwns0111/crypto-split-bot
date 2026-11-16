"""텔레그램 폴링 일시 끊김은 WARNING, 오래 지속될 때만 ERROR (cs-err 잡음 방지)."""

from unittest.mock import MagicMock

import notification.telegram_handler as th
from notification.telegram_handler import TelegramCommandHandler


def _simulate(monkeypatch, n: int, step: float):
    clock = [1000.0]
    log = MagicMock()
    monkeypatch.setattr(th.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(th, "_log", log)
    state = (0, 0.0, False)
    for _ in range(n):
        state = TelegramCommandHandler._on_poll_error(Exception("reset"), *state)
        clock[0] += step
    return state, log


def test_short_outage_is_warning(monkeypatch):
    state, log = _simulate(monkeypatch, 5, 10)      # 50초 동안 5회 실패
    assert state[0] == 5 and state[2] is False
    assert log.warning.call_count == 5
    log.error.assert_not_called()


def test_long_outage_escalates_once(monkeypatch):
    state, log = _simulate(monkeypatch, 20, 60)     # 20분 동안 계속 실패
    assert state[2] is True
    assert log.error.call_count == 1
    assert log.warning.call_count == 19


def test_run_loop_backs_off_and_recovers(monkeypatch):
    """실패 시 대기가 5→10초로 늘고, 성공하면 상태가 초기화되며 업데이트를 처리한다."""
    log = MagicMock()
    monkeypatch.setattr(th, "_log", log)
    h = TelegramCommandHandler(MagicMock(), MagicMock(), {}, "tok", "1")
    h.register_commands = lambda: True
    waits = []
    h._stop.wait = lambda t: waits.append(t)
    calls = iter([Exception("a"), Exception("b"), [{"update_id": 7}]])

    def fake_get(offset):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        h._stop.set()
        return v

    h._get_updates = fake_get
    handled = []
    h._handle_update = handled.append
    h._run()
    assert waits == [5, 10]
    assert handled == [{"update_id": 7}]
    log.error.assert_not_called()
