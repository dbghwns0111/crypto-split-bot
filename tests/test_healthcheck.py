"""외부 생존 감시(healthchecks.io ping) 테스트."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from notification.scheduler import NotificationScheduler
from utils import healthcheck

URL = "https://hc-ping.com/test-uuid"


def _bot(running, alive):
    return SimpleNamespace(is_running=running, is_thread_alive=alive)


def _sched(bots, url=URL):
    bm = MagicMock()
    bm.bots = bots
    cfg = {"system": {"healthcheck_url": url}}
    return NotificationScheduler(MagicMock(), bm, MagicMock(), cfg)


def test_ping_ok():
    with patch("utils.healthcheck.requests.post") as post:
        post.return_value.status_code = 200
        assert healthcheck.ping(URL, "/start", "hi") is True
        assert post.call_args[0][0] == URL + "/start"


def test_ping_network_error_returns_false():
    with patch("utils.healthcheck.requests.post", side_effect=OSError("down")):
        assert healthcheck.ping(URL) is False


def test_ping_without_url_is_noop():
    with patch("utils.healthcheck.requests.post") as post:
        assert healthcheck.ping("") is False
        post.assert_not_called()


def test_job_registered_only_with_url():
    assert _sched({}, url="")._scheduler.get_job("healthcheck") is None
    assert _sched({})._scheduler.get_job("healthcheck") is not None


def test_job_pings_success_when_healthy():
    s = _sched({"BTC": _bot(True, True), "ETH": _bot(False, False)})
    with patch("utils.healthcheck.ping", return_value=True) as ping:
        s._job_healthcheck()
    url, signal, body = ping.call_args[0]
    assert signal == ""
    assert "가동: BTC" in body and "중지: ETH" in body


def test_job_pings_fail_when_running_bot_thread_dead():
    s = _sched({"XRP": _bot(True, False)})
    with patch("utils.healthcheck.ping", return_value=True) as ping:
        s._job_healthcheck()
    assert ping.call_args[0][1] == "/fail"
    assert "스레드 사망: XRP" in ping.call_args[0][2]
