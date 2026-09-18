"""API 호출 지수 백오프 재시도 데코레이터."""

import functools
import threading
import time
from typing import Callable, Type

from utils.logger import get_logger

_log = get_logger("exchange.retry")


_AUTH_ERROR_HINTS = ("401", "invalid_access_key", "invalid_secret_key", "Unauthorized")
_JWT_EXPIRED_HINT = "expired_jwt"

# JWT 만료 시 시간 동기화 후 텔레그램 알림을 보내기 위한 선택적 콜백.
# main.py 에서 set_notifier_fn() 으로 등록한다.
_notifier_fn: Callable[[bool], None] | None = None

# OS 시계 동기화(w32tm/PowerShell)는 관리자 권한과 정상 동작하는 W32Time 서비스가
# 필요하고 서브프로세스까지 띄우는 무거운 작업이라, JWT 만료가 잦은 환경에서 매번
# 실행하면 부담이 크다. 1차 복구는 NTP 기준 시각 재측정(utils/ntp_clock, UDP 1회)으로
# 처리하고 — 이것만으로 API 타임스탬프는 정확해진다 — OS 시계 자체 동기화는 DB 기록
# 시각/스케줄러 정확도를 위해 아래 쿨다운 간격으로만 보조 수행한다.
_sync_lock = threading.Lock()
_last_os_sync_at = 0.0
_OS_SYNC_COOLDOWN = 1800.0  # 초 — OS 시계 동기화는 최대 30분에 한 번


def set_notifier_fn(fn: Callable[..., None]) -> None:
    """시계 보정 실패를 텔레그램으로 전송할 콜백을 등록한다."""
    global _notifier_fn
    _notifier_fn = fn


def _recover_clock_skew() -> bool:
    """expired_jwt 복구 — NTP 기준 시각 재측정이 1차, OS 시계 동기화는 보조.

    여러 봇 스레드가 거의 동시에 같은 오류를 감지해도 NTP 재측정은 ntp_clock 쪽
    최소 간격 제한에, OS 동기화는 아래 쿨다운에 각각 묶여 중복 실행되지 않는다.
    """
    global _last_os_sync_at

    from utils import ntp_clock
    ntp_ok = ntp_clock.refresh(force=True)
    if ntp_ok:
        _log.info("NTP 기준 시각 재측정 완료 (OS 시계 오차 %+.3f초)", ntp_clock.offset())

    with _sync_lock:
        due = (time.monotonic() - _last_os_sync_at) >= _OS_SYNC_COOLDOWN
        if due:
            _last_os_sync_at = time.monotonic()

    if not due:
        return ntp_ok

    from utils.time_sync import sync_windows_time
    os_ok = sync_windows_time()
    _log.info("OS 시계 동기화 %s", "성공" if os_ok else "실패")

    # 성공 알림은 보내지 않는다 — 예전에 이 알림이 밤새 수십 건씩 쏟아져
    # 정작 매매가 멈춘 사실을 가려버렸다. 둘 다 실패한 경우에만 알린다.
    if not (ntp_ok or os_ok) and _notifier_fn is not None:
        try:
            _notifier_fn(
                False,
                "JWT 만료 감지 후 NTP 재측정과 시계 동기화가 모두 실패했습니다.\n"
                "네트워크(UDP 123) 차단 여부와 시스템 시계를 확인하세요.",
            )
        except Exception:
            pass
    return ntp_ok or os_ok


def _is_auth_error(exc: BaseException) -> bool:
    """인증 오류(재시도해도 해결 안 됨)인지 판별한다."""
    msg = str(exc)
    return any(hint in msg for hint in _AUTH_ERROR_HINTS)


def _is_jwt_expired(exc: BaseException) -> bool:
    """JWT 만료 오류인지 판별한다 — 시계 동기화로 복구 가능."""
    return _JWT_EXPIRED_HINT in str(exc)


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple[Type[BaseException], ...] = (Exception,),
):
    """지수 백오프 재시도 데코레이터.

    실패 시 base_delay × 2^(attempt-1) 초 대기 후 재시도한다.
    기본값: 1 → 2 → 4 초, 최대 3회 재시도(총 4회 시도).
    모든 재시도 소진 후에도 실패하면 마지막 예외를 그대로 re-raise한다.
    401/인증 오류는 재시도 없이 즉시 re-raise한다.
    단, expired_jwt 오류는 NTP 기준 시각을 다시 맞춘 뒤 1회 재시도한다.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            jwt_sync_attempted = False  # 시도당 1회만 시계 동기화
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if _is_auth_error(exc):
                        if _is_jwt_expired(exc) and not jwt_sync_attempted:
                            jwt_sync_attempted = True
                            _log.warning(
                                "%s JWT 만료 감지 — 기준 시각 재측정",
                                func.__name__,
                            )
                            ok = _recover_clock_skew()
                            if ok:
                                _log.info("%s 기준 시각 보정 완료 — 재시도", func.__name__)
                                continue  # 한 번만 재시도
                        _log.error(
                            "%s 인증 오류 (재시도 불가): %s",
                            func.__name__, exc,
                        )
                        raise
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        _log.warning(
                            "%s 실패 (시도 %d/%d), %.0f초 후 재시도: %s",
                            func.__name__, attempt + 1, max_retries + 1, delay, exc,
                        )
                        time.sleep(delay)
                    else:
                        _log.error(
                            "%s 최대 재시도(%d회) 초과: %s",
                            func.__name__, max_retries + 1, exc,
                        )
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator
