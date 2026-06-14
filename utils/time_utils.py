"""KST 시각 유틸리티."""

from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """현재 KST datetime을 반환한다."""
    return datetime.now(KST)


def now_kst_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """현재 KST 시각을 포맷 문자열로 반환한다."""
    return now_kst().strftime(fmt)


def today_kst_str() -> str:
    """오늘 KST 날짜를 'YYYY-MM-DD' 형식으로 반환한다."""
    return now_kst().strftime("%Y-%m-%d")


def elapsed_seconds(since: datetime) -> float:
    """since부터 현재까지 경과 시간(초)을 반환한다.

    since가 timezone-aware이면 동일 tz 기준, naive이면 KST로 간주한다.
    """
    if since.tzinfo is None:
        since = since.replace(tzinfo=KST)
    return (datetime.now(since.tzinfo) - since).total_seconds()


def parse_kst(dt_str: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> datetime:
    """문자열을 KST datetime으로 파싱한다."""
    dt = datetime.strptime(dt_str, fmt)
    return dt.replace(tzinfo=KST)


def to_kst(dt: datetime) -> datetime:
    """임의 timezone-aware datetime을 KST로 변환한다."""
    return dt.astimezone(KST)
