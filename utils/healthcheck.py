"""외부 생존 감시(dead-man's switch) ping — healthchecks.io 호환.

미니PC(홈서버)가 정전·인터넷 장애·프로세스 사망으로 멈추면 봇은 스스로 알릴 수
없다. 그래서 반대로 "살아 있다"는 신호를 외부 서비스에 주기적으로 보내고, 신호가
끊기면 외부 서비스(healthchecks.io)가 텔레그램/메일로 알리게 한다.

URL 형식: https://hc-ping.com/<uuid>
- ""       : 정상 (살아 있음)
- "/start" : 프로세스 시작
- "/fail"  : 프로세스는 살아 있지만 이상 상태(가동 중이어야 할 봇 스레드 사망 등) → 즉시 알림
본문(body)은 healthchecks.io 이벤트 로그에 표시된다.
"""

import requests

from utils.logger import get_logger

_log = get_logger("utils.healthcheck")

_TIMEOUT = (5, 10)  # (connect, read) 초 — 스케줄러 스레드를 오래 붙잡지 않게


def ping(url: str, signal: str = "", body: str = "") -> bool:
    """url(+signal)로 ping을 보낸다. 성공 시 True. 예외는 삼키고 False."""
    if not url:
        return False
    target = url.rstrip("/") + signal
    try:
        resp = requests.post(target, data=body.encode("utf-8"), timeout=_TIMEOUT)
        if resp.status_code == 200:
            return True
        _log.warning("헬스체크 ping 응답 이상: %s → HTTP %s", signal or "(ok)", resp.status_code)
    except Exception as exc:
        _log.warning("헬스체크 ping 실패 (%s): %s", signal or "(ok)", exc)
    return False
