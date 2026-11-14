"""서버(프로세스) 재기동 후 자동매매 자동 재가동.

정전·인터넷 장애·크래시·자동 업데이트·cs-restart 로 프로세스가 다시 뜨면, **사용자가 켜둔
종목**(bot_state.desired_running)만 이전 상태 그대로(fresh=False) 다시 가동한다.
사용자가 웹/텔레그램으로 직접 끈 종목은 desired_running=0 이라 다시 켜지 않는다.

재가동하지 않고 텔레그램으로 알리기만 하는 경우:
  - 이번 기동에 건너뛰기 요청이 있음   (cs-restart -n → data/.no_auto_resume 표식)
  - 너무 오래 꺼져 있었음              (system.auto_resume_max_down_hours, 기본 6시간)
  - 중단 시간을 알 수 없음             (생존 표식 data/.last_alive 없음 — 도입 직후 1회 등)
  - 짧은 시간에 재기동이 반복됨         (30분 내 3회 이상 — 크래시 반복 의심)
  - 인터넷이 돌아오지 않음             (빗썸 시세 조회가 10분간 실패)

재가동은 /resumeall 과 같은 동작이다 — 꺼져 있는 동안 가격이 내려갔으면 지나친 매수 칸만큼
실제 캐치업 매수가 나간다(catchup_max, 잔고 한도 내).
"""

import time
from pathlib import Path
from typing import Callable, Optional

from database.state_repo import get_desired_running_symbols
from utils.logger import get_logger

_log = get_logger("auto_resume")

_DATA_DIR    = Path(__file__).resolve().parent / "data"
HEARTBEAT    = _DATA_DIR / ".last_alive"          # 1분마다 갱신 — 마지막 생존 시각
SKIP_MARKER  = _DATA_DIR / ".no_auto_resume"      # cs-restart -n 이 만든다, 기동 시 1회 소비
HISTORY      = _DATA_DIR / ".auto_resume_history"  # 자동 재가동 시각 기록 (반복 감지)

_LOOP_WINDOW_SEC = 30 * 60
_LOOP_MAX        = 3


# ── 생존 표식 / 건너뛰기 표식 ──────────────────────────────────────────────────

def touch_heartbeat() -> None:
    """마지막 생존 시각을 기록한다 (스케줄러 1분 잡 + 정상 종료 직전)."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(str(int(time.time())))
    except Exception as exc:
        _log.warning("생존 표식 기록 실패: %s", exc)


def downtime_seconds() -> Optional[float]:
    """마지막 생존 시각부터 지금까지 꺼져 있던 시간(초). 표식이 없으면 None."""
    try:
        return max(0.0, time.time() - float(HEARTBEAT.read_text().strip()))
    except Exception:
        return None


def consume_skip_marker() -> bool:
    """건너뛰기 표식이 있으면 지우고 True (이번 기동 1회만 적용)."""
    try:
        if SKIP_MARKER.exists():
            SKIP_MARKER.unlink()
            return True
    except Exception as exc:
        _log.warning("건너뛰기 표식 처리 실패: %s", exc)
        return True   # 지우지 못했어도 사용자의 "하지 마" 요청은 존중한다
    return False


def _recent_resume_count(now: float) -> int:
    try:
        stamps = [float(x) for x in HISTORY.read_text().split()]
    except Exception:
        return 0
    return sum(1 for t in stamps if now - t <= _LOOP_WINDOW_SEC)


def _record_resume(now: float) -> None:
    try:
        try:
            stamps = [float(x) for x in HISTORY.read_text().split()]
        except Exception:
            stamps = []
        stamps = [t for t in stamps if now - t <= _LOOP_WINDOW_SEC] + [now]
        HISTORY.write_text("\n".join(str(int(t)) for t in stamps))
    except Exception as exc:
        _log.warning("자동 재가동 기록 실패: %s", exc)


# ── 본체 ──────────────────────────────────────────────────────────────────────

def run_auto_resume(
    bot_manager,
    exchange,
    notifier,
    cfg: dict,
    downtime: Optional[float],
    skip_requested: bool,
    wait_timeout: float = 600,
    poll_interval: float = 15,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """조건을 확인하고 사용자가 켜둔 종목을 재가동한다. 결과 dict 반환(테스트·로그용).

    main.py 가 별도 스레드에서 호출한다(인터넷 대기 중에도 웹·텔레그램은 바로 동작).
    """
    sys_cfg   = cfg.get("system", {})
    enabled   = bool(sys_cfg.get("auto_resume_enabled", True))
    max_hours = float(sys_cfg.get("auto_resume_max_down_hours", 6))

    try:
        desired = get_desired_running_symbols()
    except Exception as exc:
        _log.error("자동 재가동 대상 조회 실패: %s", exc)
        return {"action": "error", "reason": str(exc)}
    targets = [s for s in desired
               if s in bot_manager.symbols and not bot_manager.is_running(s)]

    def held(reason: str) -> dict:
        _log.warning("자동 재가동 보류: %s (대상: %s)", reason, targets)
        if notifier:
            notifier.send_auto_resume_held(targets, reason)
        return {"action": "held", "reason": reason, "targets": targets}

    if not enabled:
        _log.info("자동 재가동 꺼짐(설정) — 대상: %s", targets)
        return {"action": "disabled", "targets": targets}
    if not targets:
        _log.info("자동 재가동 대상 없음 (사용자가 켜둔 종목 없음)")
        return {"action": "none", "targets": []}
    if skip_requested:
        return held("재시작 명령에 '재가동 안 함'(-n) 옵션이 지정됨")
    if downtime is None:
        return held("마지막 가동 시각을 알 수 없음")
    if downtime > max_hours * 3600:
        return held(f"{downtime / 3600:.1f}시간 동안 중단됨 (기준 {max_hours:g}시간 초과)")
    now = time.time()
    if _recent_resume_count(now) >= _LOOP_MAX:
        return held(f"{_LOOP_WINDOW_SEC // 60}분 안에 재기동이 {_LOOP_MAX}회 이상 반복됨 (오류 반복 의심)")

    # ── 인터넷(빗썸) 복구 대기 — 정전 후 공유기가 늦게 뜨는 경우 등 ──
    ticker = bot_manager.bots[targets[0]].ticker
    waited = 0.0
    while True:
        try:
            if float(exchange.get_current_price(ticker) or 0) > 0:
                break
        except Exception as exc:
            _log.info("자동 재가동 대기 — 빗썸 연결 안 됨 (%.0f초 경과): %s", waited, exc)
        if waited >= wait_timeout:
            return held(f"인터넷(빗썸) 연결이 {wait_timeout / 60:.0f}분 동안 복구되지 않음")
        sleep(poll_interval)
        waited += poll_interval

    resumed, failed = [], []
    for sym in targets:
        ok, msg = bot_manager.start(sym, fresh=False)
        (resumed if ok else failed).append(sym)
        if not ok:
            _log.error("자동 재가동 실패 (%s): %s", sym, msg)
    _record_resume(time.time())
    _log.info("자동 재가동 완료: %s (실패: %s, 중단 %.0f초)", resumed, failed, downtime)
    if notifier:
        notifier.send_auto_resume(resumed, failed, downtime)
    return {"action": "resumed", "resumed": resumed, "failed": failed}


def start_in_background(bot_manager, exchange, notifier, cfg: dict,
                        downtime: Optional[float], skip_requested: bool):
    """run_auto_resume 을 데몬 스레드로 실행한다."""
    import threading

    def _run():
        try:
            run_auto_resume(bot_manager, exchange, notifier, cfg, downtime, skip_requested)
        except Exception as exc:
            _log.error("자동 재가동 처리 오류: %s", exc)

    t = threading.Thread(target=_run, name="AutoResume", daemon=True)
    t.start()
    return t

