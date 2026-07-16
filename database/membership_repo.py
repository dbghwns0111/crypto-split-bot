"""멤버십 등급 변경 이력 레포지토리.

빗썸 멤버십 등급(포인트 적립률 결정)은 보통 월 단위로 바뀐다. 손익 재계산 시
"현재" 등급을 과거 전체 거래에 소급 적용하면 실제와 달라지므로, 등급이
바뀐 날짜를 기록해두고 거래 체결일 기준으로 그 시점에 적용 중이던 등급을
조회한다.
"""

import sqlite3
from typing import Optional

from database.connection import get_connection

_SENTINEL_DATE = "2000-01-01"  # 이력 기록 이전 과거 전체에 적용되는 기준일


def _conn() -> sqlite3.Connection:
    return get_connection()


def seed_membership_history(membership: str) -> None:
    """이력이 비어 있으면 현재 설정값을 과거 전체 기본값으로 1건 기록한다."""
    row = _conn().execute("SELECT COUNT(*) AS cnt FROM membership_history").fetchone()
    if row and row["cnt"] > 0:
        return
    _conn().execute(
        "INSERT INTO membership_history (effective_date, membership) VALUES (?, ?)",
        (_SENTINEL_DATE, membership),
    )
    _conn().commit()


def record_membership_change(membership: str, effective_date: Optional[str] = None) -> None:
    """멤버십 등급 변경을 이력에 기록한다(같은 날짜에 재변경 시 마지막 값으로 덮어씀)."""
    from utils.time_utils import today_kst_str

    d = effective_date or today_kst_str()
    _conn().execute(
        """
        INSERT INTO membership_history (effective_date, membership) VALUES (?, ?)
        ON CONFLICT(effective_date) DO UPDATE SET membership = excluded.membership
        """,
        (d, membership),
    )
    _conn().commit()


def get_membership_at(date_str: str, default: str = "화이트") -> str:
    """지정 날짜(YYYY-MM-DD)에 적용 중이던 멤버십 등급을 반환한다.

    해당 날짜 이전(포함)의 가장 최근 변경 이력을 사용한다. 이력이 전혀 없으면
    (마이그레이션 직후 등) default를 반환한다.
    """
    row = _conn().execute(
        """
        SELECT membership FROM membership_history
        WHERE effective_date <= ?
        ORDER BY effective_date DESC
        LIMIT 1
        """,
        (date_str,),
    ).fetchone()
    if row:
        return row["membership"]
    # 조회 날짜가 이력상 가장 이른 기록보다도 앞선 경우 — 가장 오래된 값을 사용
    row = _conn().execute(
        "SELECT membership FROM membership_history ORDER BY effective_date ASC LIMIT 1"
    ).fetchone()
    return row["membership"] if row else default
