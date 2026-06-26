"""Windows 시스템 시계 동기화 유틸리티."""

import subprocess

from utils.logger import get_logger

_log = get_logger("utils.time_sync")

_NTP_PEERS = "time.windows.com pool.ntp.org"


def _run(args: list[str], timeout: int = 30) -> bool:
    """명령어를 실행하고 종료 코드가 0이면 True를 반환한다."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def _configure_w32tm() -> bool:
    """Windows Time 서비스를 재등록하고 NTP 서버를 설정한다."""
    _log.info("Windows Time 서비스 재설정 시도")
    steps = [
        ["net", "stop", "w32tm"],
        ["w32tm", "/unregister"],
        ["w32tm", "/register"],
        ["net", "start", "w32tm"],
        ["w32tm", "/config",
         "/manualpeerlist:" + _NTP_PEERS,
         "/syncfromflags:manual",
         "/reliable:YES",
         "/update"],
    ]
    for cmd in steps:
        _run(cmd)  # 일부 단계 실패해도 계속 진행
    return True


def sync_windows_time() -> bool:
    """Windows 시계를 NTP 서버와 동기화한다.

    1단계: w32tm /resync /force 시도
    2단계: 실패하면 서비스 재설정 후 재시도

    빗썸 API JWT는 요청 시각 타임스탬프를 포함하므로 시계가 수십 초 이상
    어긋나면 expired_jwt 401 오류가 발생한다.

    Returns:
        True — 동기화 성공, False — 실패(권한 부족 또는 w32tm 없음 등)
    """
    try:
        r = subprocess.run(
            ["w32tm", "/resync", "/force"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0:
            _log.info("Windows 시간 동기화 완료")
            return True

        # "사용 가능한 시간 데이터가 없어" — 서비스 미설정 시 재설정 후 재시도
        _log.warning("w32tm /resync 실패 (exit=%d) — 서비스 재설정 후 재시도", r.returncode)
        _configure_w32tm()

        r2 = subprocess.run(
            ["w32tm", "/resync", "/force"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r2.returncode == 0:
            _log.info("Windows 시간 동기화 완료 (서비스 재설정 후)")
            return True

        _log.warning("Windows 시간 동기화 최종 실패: %s", r2.stderr.strip())
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
