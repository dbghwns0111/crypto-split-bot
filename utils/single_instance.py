"""단일 인스턴스 보장 — 중복 실행 시 두 번째 프로세스를 차단한다.

같은 PC에서 main.py 가 2개 이상 뜨면 텔레그램 봇 토큰을 여러 프로세스가
동시에 long-polling 하고, 정각 현황도 중복/모순되게 발송된다(가동·중지 동시 표시).
이를 막기 위해 단일 인스턴스를 보장한다.
- Windows: 명명된 뮤텍스(named mutex)
- POSIX:   파일 락(fcntl.flock)
"""

import os
import sys

from utils.logger import get_logger

_log = get_logger("single_instance")

# 락/뮤텍스 핸들을 전역으로 잡아두어 프로세스 수명 동안 해제되지 않게 한다(GC 방지).
_lock_handle = None


def acquire_single_instance_lock(name: str = "cryptosplit") -> bool:
    """프로세스 락을 획득한다.

    Returns:
        True  — 락 획득 성공(이 프로세스가 유일).
        False — 이미 다른 인스턴스가 실행 중.
    예기치 못한 오류 시에는 안전하게 True 를 반환(기존 동작 유지)한다.
    """
    global _lock_handle
    try:
        if sys.platform == "win32":
            return _acquire_windows(name)
        return _acquire_posix(name)
    except Exception as exc:
        _log.warning("단일 인스턴스 락 확인 실패(무시하고 진행): %s", exc)
        return True


def _acquire_windows(name: str) -> bool:
    global _lock_handle
    import ctypes

    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    # 세션 내 동일 사용자 중복 실행 차단(Local 네임스페이스)
    mutex_name = f"Local\\{name}_singleton"
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    last_error = kernel32.GetLastError()
    if handle == 0:
        _log.warning("뮤텍스 생성 실패(무시하고 진행): err=%s", last_error)
        return True
    if last_error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        _log.warning("이미 다른 인스턴스 실행 중(뮤텍스 점유): %s", mutex_name)
        return False
    _lock_handle = handle  # 핸들 유지(닫히면 뮤텍스 해제됨)
    _log.info("단일 인스턴스 락 획득: %s (pid=%s)", mutex_name, os.getpid())
    return True


def _acquire_posix(name: str) -> bool:
    global _lock_handle
    import fcntl

    base = os.environ.get("TMPDIR") or "/tmp"
    path = os.path.join(base, f"{name}.lock")
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        _log.warning("이미 다른 인스턴스 실행 중(파일 락 점유): %s", path)
        return False
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except Exception:
        pass
    _lock_handle = fh  # 핸들 유지(닫히면 락 해제됨)
    _log.info("단일 인스턴스 락 획득: %s (pid=%s)", path, os.getpid())
    return True
