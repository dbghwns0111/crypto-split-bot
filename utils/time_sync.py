"""Windows 시스템 시계 동기화 유틸리티."""

import subprocess

from utils.logger import get_logger

_log = get_logger("utils.time_sync")


def sync_windows_time() -> bool:
    """w32tm /resync /force 로 Windows 시계를 NTP 서버와 동기화한다.

    빗썸 API JWT는 요청 시각 타임스탬프를 포함하므로 시계가 수십 초 이상
    어긋나면 expired_jwt 401 오류가 발생한다.

    Returns:
        True — 동기화 성공, False — 실패(권한 부족 또는 w32tm 없음 등)
    """
    try:
        result = subprocess.run(
            ["w32tm", "/resync", "/force"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            _log.info("Windows 시간 동기화 완료")
            return True
        stderr = result.stderr.strip()
        _log.warning("Windows 시간 동기화 실패 (exit=%d): %s", result.returncode, stderr)
        return False
    except FileNotFoundError:
        _log.warning("w32tm 명령어를 찾을 수 없음 — Windows 전용 기능")
        return False
    except subprocess.TimeoutExpired:
        _log.warning("Windows 시간 동기화 타임아웃 (30초 초과)")
        return False
    except Exception as exc:
        _log.error("Windows 시간 동기화 오류: %s", exc)
        return False
