"""Windows 시스템 시계 동기화 유틸리티."""

import socket
import struct
import subprocess
from datetime import datetime, timezone

from utils.logger import get_logger

_log = get_logger("utils.time_sync")

_NTP_PEERS = "time.windows.com pool.ntp.org"
_NTP_SERVERS = ["pool.ntp.org", "time.windows.com", "time.google.com"]


def _run(args: list[str], timeout: int = 30) -> bool:
    """명령어를 실행하고 종료 코드가 0이면 True를 반환한다."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def _configure_w32tm() -> None:
    """Windows Time 서비스를 재등록하고 NTP 서버를 설정한다."""
    _log.info("Windows Time 서비스 재설정 시도")
    steps = [
        ["net", "stop", "W32Time"],
        ["w32tm", "/unregister"],
        ["w32tm", "/register"],
        ["sc", "sidtype", "W32Time", "unrestricted"],
        ["sc", "config", "W32Time", "type=", "own"],
        ["net", "start", "W32Time"],
        ["w32tm", "/config",
         "/manualpeerlist:" + _NTP_PEERS,
         "/syncfromflags:manual",
         "/reliable:YES",
         "/update"],
    ]
    for cmd in steps:
        _run(cmd)  # 일부 단계 실패해도 계속 진행


def _get_ntp_offset(server: str) -> float | None:
    """NTP 서버에서 현재 UTC 타임스탬프를 가져온다."""
    try:
        msg = b"\x1b" + b"\x00" * 47
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(5)
            s.sendto(msg, (server, 123))
            data, _ = s.recvfrom(1024)
        # NTP 타임스탬프: 바이트 40-43 (Transmit Timestamp, 초 부분)
        secs = struct.unpack("!I", data[40:44])[0]
        # NTP epoch(1900-01-01) → Unix epoch(1970-01-01) 변환
        unix_ts = secs - 2208988800
        return unix_ts
    except Exception as exc:
        _log.debug("NTP 서버 %s 응답 실패: %s", server, exc)
        return None


def _sync_via_powershell() -> bool:
    """w32tm 서비스 없이 PowerShell로 직접 NTP 시간을 설정한다."""
    for server in _NTP_SERVERS:
        unix_ts = _get_ntp_offset(server)
        if unix_ts is None:
            continue
        utc_dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
        # PowerShell Set-Date 로 시스템 시계를 직접 변경
        dt_str = utc_dt.strftime("%Y-%m-%d %H:%M:%S")
        ps_cmd = (
            f"$t = [System.DateTime]::Parse('{dt_str}', $null, "
            f"[System.Globalization.DateTimeStyles]::AssumeUniversal);"
            f"Set-Date $t.ToLocalTime() -Confirm:$false"
        )
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            _log.info("PowerShell NTP 동기화 완료 (서버: %s, UTC: %s)", server, dt_str)
            return True
        _log.debug("PowerShell Set-Date 실패: %s", result.stderr.strip())
    return False


def sync_windows_time() -> bool:
    """Windows 시계를 NTP 서버와 동기화한다.

    1단계: w32tm /resync /force 시도
    2단계: 실패하면 서비스 재설정 후 재시도
    3단계: w32tm 서비스 자체가 불가하면 PowerShell 직접 동기화

    빗썸 API JWT는 요청 시각 타임스탬프를 포함하므로 시계가 수십 초 이상
    어긋나면 expired_jwt 401 오류가 발생한다.

    Returns:
        True — 동기화 성공, False — 실패(권한 부족 등)
    """
    try:
        # 1단계: w32tm 직접 재동기화
        r = subprocess.run(
            ["w32tm", "/resync", "/force"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            _log.info("Windows 시간 동기화 완료 (w32tm)")
            return True

        # 2단계: 서비스 재설정 후 재시도
        _log.warning("w32tm /resync 실패 (exit=%d) — 서비스 재설정 후 재시도", r.returncode)
        _configure_w32tm()
        r2 = subprocess.run(
            ["w32tm", "/resync", "/force"],
            capture_output=True, text=True, timeout=30,
        )
        if r2.returncode == 0:
            _log.info("Windows 시간 동기화 완료 (서비스 재설정 후)")
            return True

        # 3단계: w32tm 서비스 사용 불가 → PowerShell 직접 동기화
        _log.warning("w32tm 서비스 복구 실패 — PowerShell 직접 동기화 시도")
        if _sync_via_powershell():
            return True

        _log.error("모든 시간 동기화 방법 실패")
        return False

    except FileNotFoundError:
        _log.warning("w32tm 없음 — PowerShell 직접 동기화 시도")
        return _sync_via_powershell()
    except subprocess.TimeoutExpired:
        _log.warning("w32tm 타임아웃 — PowerShell 직접 동기화 시도")
        return _sync_via_powershell()
    except Exception as exc:
        _log.error("시간 동기화 오류: %s", exc)
        return False
