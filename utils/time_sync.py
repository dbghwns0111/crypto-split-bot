"""Windows 시스템 시계 동기화 유틸리티."""

import socket
import struct
import subprocess
from datetime import datetime, timezone

from utils.logger import get_logger

_log = get_logger("utils.time_sync")

_NTP_SERVERS = ["pool.ntp.org", "time.windows.com", "time.google.com"]


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

    1단계: w32tm /resync /force 시도 (이미 정상 동작 중인 W32Time 서비스에게
    즉시 재동기화만 요청 — 서비스 설정 자체는 건드리지 않는다)
    2단계: 실패하면 W32Time 서비스와 무관하게 PowerShell로 NTP 서버에서 직접
    시각을 받아와 Set-Date로 시스템 시계를 맞춘다

    과거에는 1단계 실패 시 W32Time 서비스를 net stop/unregister/register 하고
    sc sidtype/sc config로 서비스 SID 유형까지 바꾸는 재설정을 시도했으나, 이
    재설정이 반복 실행되면 서비스 SID 유형이 실행 중인 프로세스와 호환되지
    않는 상태가 되어(Windows 시스템 오류 1290) OS 재부팅 전까지 서비스 자체가
    시작 불가능해지는 사고가 발생했다. 서비스 설정을 바꾸는 조치는 완전히
    제거하고, 서비스가 불안정할 때는 서비스에 의존하지 않는 PowerShell 경로로
    바로 넘어가도록 단순화했다.

    빗썸 API JWT는 요청 시각 타임스탬프를 포함하므로 시계가 수십 초 이상
    어긋나면 expired_jwt 401 오류가 발생한다.

    Returns:
        True — 동기화 성공, False — 실패(권한 부족 등)
    """
    try:
        r = subprocess.run(
            ["w32tm", "/resync", "/force"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            _log.info("Windows 시간 동기화 완료 (w32tm)")
            return True
        _log.warning("w32tm /resync 실패 (exit=%d) — PowerShell 직접 동기화로 대체", r.returncode)
    except FileNotFoundError:
        _log.warning("w32tm 없음 — PowerShell 직접 동기화 시도")
    except subprocess.TimeoutExpired:
        _log.warning("w32tm 타임아웃 — PowerShell 직접 동기화 시도")
    except Exception as exc:
        _log.error("w32tm 실행 오류: %s — PowerShell 직접 동기화 시도", exc)

    if _sync_via_powershell():
        return True

    _log.error("모든 시간 동기화 방법 실패")
    return False
