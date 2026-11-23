"""빗썸 장애(응답 형식 이상) 대응 — 2026-11-23 01:00~07:30 사고 재현.

- 빗썸이 HTTP 200 에 평소와 다른 JSON 을 주면 python_bithumb 이 TypeError 로 터진다.
  래퍼는 이를 BithumbResponseError(응답 요약 포함)로 바꾸고 원본을 로그에 남겨야 한다.
- 봇 루프는 장애 내내 알림을 쏟아내지 말고 첫 알림 / 1시간 재알림 / 복구 알림만 보내야 한다.
"""

import types
from unittest.mock import MagicMock

import pytest
import requests

import exchange.bithumb_client as bc
import exchange.retry as retry_mod
import strategy.magic_split as ms
from exchange.errors import BithumbResponseError


def _resp(body: str, status: int = 200, url="https://api.bithumb.com/v1/ticker?markets=KRW-BTC"):
    r = requests.Response()
    r.status_code = status
    r._content = body.encode("utf-8")
    r.encoding = "utf-8"
    r.url = url
    r.headers["Content-Type"] = "application/json"
    r.request = requests.Request("GET", url).prepare()
    return r


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)
    bc._dump_state.update(key=None, at=0.0, suppressed=0)
    return bc.BithumbClient("test_access", "test_secret")


def _serve(monkeypatch, resp):
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url))
        return resp
    monkeypatch.setattr(bc.requests, "request", fake_request)
    return calls


class TestBadResponse:
    MAINT = '{"status":"5600","data":"시스템 점검 중입니다"}'

    def test_format_error_is_explained(self, client, monkeypatch):
        _serve(monkeypatch, _resp(self.MAINT))
        with pytest.raises(BithumbResponseError) as ei:
            client.get_current_price("KRW-BTC")
        msg = str(ei.value)
        assert "응답 형식 이상" in msg
        assert "시스템 점검 중입니다" in msg
        assert "HTTP 200" in msg

    def test_format_error_is_retried_not_auth(self, client, monkeypatch):
        # 본문에 "401" 이 섞여도 인증 오류로 오판해 재시도를 건너뛰면 안 된다
        calls = _serve(monkeypatch, _resp('{"data":"price 84012345"}'))
        with pytest.raises(BithumbResponseError):
            client.get_current_price("KRW-BTC")
        assert len(calls) == 4   # 1회 + 재시도 3회

    def test_raw_response_logged_once_while_repeating(self, client, monkeypatch):
        _serve(monkeypatch, _resp(self.MAINT))
        logged = MagicMock()
        monkeypatch.setattr(bc._bad_log, "error", logged)
        for _ in range(3):
            with pytest.raises(BithumbResponseError):
                client.get_current_price("KRW-BTC")
        assert logged.call_count == 1            # 같은 응답 12번 → 전체 기록 1번
        args = logged.call_args.args
        assert "시스템 점검 중입니다" in str(args)
        assert bc._dump_state["suppressed"] == 11

    def test_private_order_list_bad_response(self, client, monkeypatch):
        _serve(monkeypatch, _resp('"점검중"', url="https://api.bithumb.com/v1/orders"))
        with pytest.raises(BithumbResponseError) as ei:
            client.get_open_orders("KRW-BTC")
        assert "점검중" in str(ei.value)

    def test_connection_error_unchanged(self, client, monkeypatch):
        def boom(method, url, **kw):
            raise requests.exceptions.ConnectionError("dns fail")
        monkeypatch.setattr(bc.requests, "request", boom)
        with pytest.raises(requests.exceptions.ConnectionError):
            client.get_current_price("KRW-BTC")

    def test_normal_response_ok(self, client, monkeypatch):
        _serve(monkeypatch, _resp('[{"market":"KRW-BTC","trade_price":83560000}]'))
        assert client.get_current_price("KRW-BTC") == 83560000.0

    def test_cancel_order_bad_response_returns_false(self, client, monkeypatch):
        _serve(monkeypatch, _resp('"점검중"', url="https://api.bithumb.com/v1/order"))
        assert client.cancel_order("abc") is False


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


def _run_loop(monkeypatch, outcomes, step_sec=8.0):
    """outcomes: True=성공, False=실패 순서대로 _tick 을 실행하고 notifier 를 돌려준다."""
    clock = _Clock()
    it = iter(outcomes)
    bot = types.SimpleNamespace(_symbol="BTC", _poll_interval=0, _running=True,
                                _notifier=MagicMock())

    def tick():
        clock.t += step_sec
        try:
            ok = next(it)
        except StopIteration:
            bot._running = False
            return
        if not ok:
            raise TypeError("string indices must be integers, not 'str'")

    bot._tick = tick
    bot._notify_loop_error = types.MethodType(ms.MagicSplit._notify_loop_error, bot)
    monkeypatch.setattr(ms.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)
    ms.MagicSplit._loop(bot)
    return bot._notifier


class TestLoopAlerts:
    def test_long_outage_sends_few_alerts(self, monkeypatch):
        # 8초 간격 실패 2925회 ≈ 6.5시간 (실제 사고 규모)
        n = _run_loop(monkeypatch, [False] * 2925 + [True])
        # 첫 알림 1 + 1시간마다 재알림 6 = 7 (예전 코드는 292건)
        assert n.send_api_error.call_count == 7
        first = n.send_api_error.call_args_list[0].args[1]
        assert "자동 재시도" in first and "string indices" in first
        n.send_api_recovered.assert_called_once()
        sym, mins, errs = n.send_api_recovered.call_args.args
        assert sym == "BTC" and errs == 2925 and mins >= 389

    def test_short_blip_is_silent(self, monkeypatch):
        n = _run_loop(monkeypatch, [False] * 9 + [True] + [False] * 9 + [True])
        n.send_api_error.assert_not_called()
        n.send_api_recovered.assert_not_called()

    def test_new_outage_after_recovery_alerts_again(self, monkeypatch):
        n = _run_loop(monkeypatch, [False] * 10 + [True] + [False] * 10 + [True])
        assert n.send_api_error.call_count == 2
        assert n.send_api_recovered.call_count == 2
