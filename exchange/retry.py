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

# BTC/ETH/XRP/USDT 봇이 각자 독립된 스레드에서 1초마다 폴링하므로, 시계 드리프트로
# JWT가 만료되면 거의 동시에 여러 스레드가 동일한 오류를 감지한다. 스레드마다 개별
# 동기화를 수행하면 w32tm 서비스 재시작 등 서로 겹치는 시스템 명령이 동시에 실행돼
# 충돌할 수 있고, 같은 드리프트 사건에 대해 텔레그램 알림이 중복 발송된다. 아래
# 락 + 쿨다운으로 짧은 시간 내 중복 동기화·알림을 한 번으로 묶는다.
_sync_lock = threading.Lock()
_last_sync_at = 0.0
_SYNC_DEDUP_WINDOW = 60.0  # 초 — 이 시간 안의 재감지는 새 동기화 없이 성공으로 간주


def set_notifier_fn(fn: Callable[..., None]) -> None:
    """JWT 동기화 결과를 텔레그램으로 전송할 콜백을 등록한다."""
    global _notifier_fn
    _notifier_fn = fn


def _sync_time_deduped() -> bool:
    """여러 스레드가 동시에 JWT 만료를 감지해도 실제 동기화·알림은 한 번만 수행한다."""
    global _last_sync_at
    with _sync_lock:
        now = time.monotonic()
        if now - _last_sync_at < _SYNC_DEDUP_WINDOW:
            _log.info("최근 %.0f초 내 동기화 이미 수행됨 — 중복 동기화 생략", _SYNC_DEDUP_WINDOW)
            return True
        from utils.time_sync import sync_windows_time
        ok = sync_windows_time()
        _last_sync_at = time.monotonic()
        if _notifier_fn is not None:
            try:
                reason = (
                    "JWT 만료 오류 감지 후 시계를 동기화했습니다.\n자동매매가 정상 재개됩니다."
                    if ok else
                    "JWT 만료 오류 감지 후 동기화를 시도했으나 실패했습니다."
                )
                _notifier_fn(ok, reason)
            except Exception:
                pass
        return ok


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
                                "%s JWT 만료 감지 — 시간 동기화 처리",
                                func.__name__,
                            )
                            ok = _sync_time_deduped()
                            if ok:
                                _log.info("%s 시간 동기화 완료 — 재시도", func.__name__)
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
