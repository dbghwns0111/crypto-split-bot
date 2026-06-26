"""API 호출 지수 백오프 재시도 데코레이터."""

import functools
import time
from typing import Callable, Type

from utils.logger import get_logger

_log = get_logger("exchange.retry")


_AUTH_ERROR_HINTS = ("401", "invalid_access_key", "invalid_secret_key", "Unauthorized")
_JWT_EXPIRED_HINT = "expired_jwt"

# JWT 만료 시 시간 동기화 후 텔레그램 알림을 보내기 위한 선택적 콜백.
# main.py 에서 set_notifier_fn() 으로 등록한다.
_notifier_fn: Callable[[bool], None] | None = None


def set_notifier_fn(fn: Callable[[bool], None]) -> None:
    """JWT 동기화 결과를 텔레그램으로 전송할 콜백을 등록한다."""
    global _notifier_fn
    _notifier_fn = fn


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
    단, expired_jwt 오류는 Windows 시계 동기화 후 1회 재시도한다.
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
                                "%s JWT 만료 감지 — Windows 시간 동기화 시도",
                                func.__name__,
                            )
                            from utils.time_sync import sync_windows_time
                            ok = sync_windows_time()
                            if _notifier_fn is not None:
                                try:
                                    _notifier_fn(ok)
                                except Exception:
                                    pass
                            if ok:
                                _log.info("%s 시간 동기화 성공 — 재시도", func.__name__)
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
