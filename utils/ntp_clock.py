"""NTP 기준 시각 관리 — OS 시계가 틀어져도 거래소 API 타임스탬프는 정확히 유지한다.

빗썸 API는 JWT payload의 `timestamp`(ms)를 서버 시각과 대조해 허용 오차를 벗어나면
`expired_jwt` 401을 반환한다. python_bithumb 라이브러리는 이 값을 OS 시계
(`time.time()`)에서 그대로 가져오기 때문에, 클라우드PC 시계가 드리프트하면 매매 API
호출이 통째로 막힌다 — 실제로 새벽 3시간 동안 매매가 전혀 안 된 사고가 있었다.

이 모듈은 NTP 서버에서 받은 정확한 시각을 **monotonic 시계에 앵커로 고정**해두고
now()가 그 앵커 + 경과시간을 돌려준다. monotonic은 시스템 시계 조정의 영향을 받지
않으므로, OS 시계가 얼마나 틀어지든/튀든 API 타임스탬프는 정확하게 유지된다.
OS 시계 자체를 고치는 건 utils/time_sync.py의 몫이고, 이 모듈은 그것이 실패해도
매매는 계속되게 하는 독립적인 안전장치다.
"""

import socket
import struct
import threading
import time as _time
from typing import Callable, Optional

from utils.logger import get_logger

_log = get_logger("utils.ntp_clock")

_NTP_EPOCH_DELTA = 2208988800  # NTP epoch(1900-01-01) → Unix epoch(1970-01-01)
# 손상된 응답 패킷을 걸러내기 위한 절대 시각 범위 (2020-01-01 ~ 2100-01-01)
_SANE_MIN = 1577836800.0
_SANE_MAX = 4102444800.0

_DEFAULT_SERVERS = ["time.google.com", "pool.ntp.org", "time.windows.com"]
_MIN_REFRESH_GAP = 5.0     # 초 — 여러 스레드가 몰려도 이 간격 안에서는 재측정 생략
_NOTIFY_OFFSET_SEC = 5.0   # OS 시계 오차가 이 값을 넘으면 알림 후보
_NOTIFY_COOLDOWN = 3600.0  # 초 — 오차 알림은 최대 1시간에 한 번

_lock = threading.Lock()
_anchor_ntp: Optional[float] = None   # NTP에서 받은 정확한 unix 시각
_anchor_mono: Optional[float] = None  # 그 시각을 받은 시점의 monotonic 값
_last_attempt = 0.0                   # 마지막 측정 시도 시각 (monotonic)
_last_notified = 0.0                  # 마지막 오차 알림 시각 (monotonic)

_servers: list[str] = list(_DEFAULT_SERVERS)
_interval = 60.0
_thread: Optional[threading.Thread] = None
_running = False

# 큰 시계 오차를 텔레그램으로 알리기 위한 선택적 콜백 (main.py에서 등록)
_notifier_fn: Optional[Callable[..., None]] = None


def set_notifier_fn(fn: Callable[..., None]) -> None:
    """시계 오차 알림을 전송할 콜백을 등록한다."""
    global _notifier_fn
    _notifier_fn = fn


# ── 시각 조회 ─────────────────────────────────────────────────────────────────

def now() -> float:
    """NTP 기준 현재 unix 시각(초). 아직 한 번도 동기화 못 했으면 OS 시계로 폴백."""
    with _lock:
        anchor_ntp, anchor_mono = _anchor_ntp, _anchor_mono
    if anchor_ntp is None or anchor_mono is None:
        return _time.time()
    return anchor_ntp + (_time.monotonic() - anchor_mono)


def offset() -> float:
    """OS 시계가 NTP 기준보다 얼마나 어긋나 있는지(초). 양수면 OS 시계가 느림."""
    return now() - _time.time()


def is_synced() -> bool:
    with _lock:
        return _anchor_ntp is not None


# ── NTP 측정 ──────────────────────────────────────────────────────────────────

def _to_unix(secs: int, frac: int) -> float:
    return (secs - _NTP_EPOCH_DELTA) + frac / 2 ** 32


def _query(server: str, timeout: float = 3.0) -> Optional[tuple[float, float, float]]:
    """NTP 서버에 질의한다. 실패 시 None.

    Returns:
        (응답 수신 시점의 정확한 unix 시각, 왕복 지연, 그 시점의 monotonic 값)
        — 시각과 monotonic을 "같은 순간"의 쌍으로 돌려줘야 앵커가 정확해진다.
    """
    packet = b"\x1b" + 47 * b"\0"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            t1 = _time.monotonic()
            sock.sendto(packet, (server, 123))
            data, _ = sock.recvfrom(1024)
            t4 = _time.monotonic()
    except Exception as exc:
        _log.debug("NTP 조회 실패 (%s): %s", server, exc)
        return None

    if len(data) < 48:
        return None
    # 32-39: 서버 수신 시각, 40-47: 서버 송신 시각
    rx_s, rx_f, tx_s, tx_f = struct.unpack("!IIII", data[32:48])
    server_rx = _to_unix(rx_s, rx_f)
    server_tx = _to_unix(tx_s, tx_f)
    if not (_SANE_MIN <= server_tx <= _SANE_MAX):
        _log.debug("NTP 응답 시각이 비정상 범위 (%s): %s", server, server_tx)
        return None

    round_trip = max(0.0, (t4 - t1) - max(0.0, server_tx - server_rx))
    # 응답이 도착한 시점(t4)의 정확한 시각 = 서버 송신 시각 + 편도 지연
    return server_tx + round_trip / 2, round_trip, t4


def refresh(force: bool = False) -> bool:
    """NTP 서버에서 시각을 다시 받아 앵커를 갱신한다.

    여러 봇 스레드가 동시에 호출해도 _MIN_REFRESH_GAP 안에서는 한 번만 실제 질의한다.

    Returns:
        갱신 성공 여부 (실패해도 이전 앵커는 그대로 유지된다)
    """
    global _last_attempt, _anchor_ntp, _anchor_mono

    with _lock:
        if not force and (_time.monotonic() - _last_attempt) < _MIN_REFRESH_GAP:
            return _anchor_ntp is not None
        _last_attempt = _time.monotonic()
        servers = list(_servers)

    samples: list[tuple[float, float, float]] = []  # (정확한 시각, 왕복지연, 그 시점 monotonic)
    for server in servers:
        result = _query(server)
        if result is not None:
            samples.append(result)
        if len(samples) >= 2:
            break

    if not samples:
        _log.warning("NTP 시각 조회 실패 — 모든 서버 응답 없음 (%s)", ", ".join(servers))
        return False

    # 왕복 지연이 가장 짧은 샘플이 가장 정확하다 (NTP 표준 관행)
    exact, rtt, mono_at = min(samples, key=lambda s: s[1])
    with _lock:
        _anchor_ntp, _anchor_mono = exact, mono_at

    skew = exact - _time.time()
    _log.info("NTP 시각 동기화 완료 (OS 시계 오차 %+.3f초, 왕복 %.0fms)", skew, rtt * 1000)
    _maybe_notify(skew)
    return True


def _maybe_notify(skew: float) -> None:
    """OS 시계 오차가 클 때만, 그것도 최대 1시간에 한 번만 알린다."""
    global _last_notified
    if _notifier_fn is None or abs(skew) < _NOTIFY_OFFSET_SEC:
        return
    with _lock:
        if (_time.monotonic() - _last_notified) < _NOTIFY_COOLDOWN:
            return
        _last_notified = _time.monotonic()
    try:
        _notifier_fn(skew)
    except Exception as exc:
        _log.warning("시계 오차 알림 실패: %s", exc)


# ── python_bithumb 패치 ───────────────────────────────────────────────────────

class _PatchedTime:
    """python_bithumb.private_api 전용 time 모듈 대체.

    JWT 타임스탬프를 만드는 time.time()만 NTP 보정값으로 바꾸고, 나머지 속성은
    원본 time 모듈에 그대로 위임한다.
    """

    def time(self) -> float:
        return now()

    def __getattr__(self, name: str):
        return getattr(_time, name)


def install_bithumb_time_patch() -> bool:
    """빗썸 라이브러리의 JWT 타임스탬프가 NTP 보정 시각을 쓰도록 주입한다.

    라이브러리 소스를 수정하지 않으므로 pip 업그레이드에도 영향받지 않는다.
    """
    try:
        from python_bithumb import private_api
    except Exception as exc:
        _log.warning("python_bithumb 로드 실패 — JWT 시각 보정 미적용: %s", exc)
        return False

    if getattr(private_api, "_cs_ntp_patched", False):
        return True
    private_api.time = _PatchedTime()
    private_api._cs_ntp_patched = True
    _log.info("빗썸 JWT 타임스탬프에 NTP 보정 적용 완료")
    return True


# ── 생명주기 ──────────────────────────────────────────────────────────────────

def _loop() -> None:
    while _running:
        _time.sleep(_interval)
        if not _running:
            break
        try:
            refresh(force=True)
        except Exception as exc:
            _log.error("NTP 주기 갱신 오류: %s", exc)


def start(cfg: dict) -> bool:
    """설정을 읽어 NTP 보정을 활성화하고 주기 갱신 스레드를 시작한다.

    Returns:
        활성화 여부 (설정으로 껐거나 라이브러리 패치 실패 시 False)
    """
    global _servers, _interval, _thread, _running

    sys_cfg = cfg.get("system", {}) or {}
    if not bool(sys_cfg.get("ntp_clock_enabled", True)):
        _log.info("NTP 시각 보정 비활성 (설정)")
        return False

    _interval = max(10.0, float(sys_cfg.get("ntp_refresh_interval_sec", 60)))
    servers = sys_cfg.get("ntp_servers") or _DEFAULT_SERVERS
    _servers = [str(s) for s in servers]

    if not refresh(force=True):
        _log.warning("초기 NTP 동기화 실패 — OS 시계로 동작하며 주기 갱신은 계속 시도한다")

    installed = install_bithumb_time_patch()

    _running = True
    _thread = threading.Thread(target=_loop, name="NtpClock", daemon=True)
    _thread.start()
    _log.info("NTP 시각 보정 시작 (갱신 주기 %.0f초, 서버 %s)", _interval, ", ".join(_servers))
    return installed


def stop() -> None:
    global _running
    _running = False
