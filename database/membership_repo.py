"""멤버십 등급 변경 이력 레포지토리.

빗썸 멤버십 등급(포인트 적립률 결정)은 매월 1일 단위로 정해진다. 이 앱에서는
사용자가 등급 변경을 수동으로 입력하는데, 예를 들어 8월 1일에 이미 등급이
바뀌었어도 사용자가 깜빡하고 10일에야 설정 탭에서 갱신할 수 있다 — 이 경우
"오늘(10일) 날짜"로 이력을 기록하면 1~9일 거래가 여전히 7월 등급으로 잘못
계산된다. 그래서 실제 저장 시각과 무관하게 항상 "그 달의 1일"을 기준일로
기록한다 — 즉 이력은 날짜 단위가 아니라 사실상 월 단위 등급 기록이다.
손익 재계산 시 "현재" 등급을 과거 전체 거래에 소급 적용하지 않도록, 거래
체결월 기준으로 그 시점에 적용 중이던 등급을 조회한다.
"""

import sqlite3
from typing import Optional

from database.connection import get_connection

SENTINEL_DATE = "2000-01-01"  # 이력 기록 이전 과거 전체에 적용되는 기준일


def _conn() -> sqlite3.Connection:
    return get_connection()


def seed_membership_history(membership: str) -> None:
    """이력이 비어 있으면 현재 설정값을 과거 전체 기본값으로 1건 기록한다."""
    row = _conn().execute("SELECT COUNT(*) AS cnt FROM membership_history").fetchone()
    if row and row["cnt"] > 0:
        return
    _conn().execute(
        "INSERT INTO membership_history (effective_date, membership) VALUES (?, ?)",
        (SENTINEL_DATE, membership),
    )
    _conn().commit()


def record_membership_change(membership: str, effective_month: Optional[str] = None) -> None:
    """멤버십 등급 변경을 이력에 기록한다.

    저장 시점의 날짜가 언제든(월 중순에 뒤늦게 저장해도) 항상 "이번 달 1일부터"
    적용된 것으로 기록한다 — 같은 달 안에서 늦게 저장해도 그 달 1~저장일 거래가
    소급으로 새 등급이 적용되도록. 같은 달에 재변경 시 마지막 값으로 덮어쓴다.

    effective_month: 'YYYY-MM' 형식으로 명시적 지정 가능(테스트/과거 보정용).
    기본은 현재 KST 기준 이번 달.
    """
    from utils.time_utils import now_kst

    m = effective_month or now_kst().strftime("%Y-%m")
    d = f"{m}-01"
    _conn().execute(
        """
        INSERT INTO membership_history (effective_date, membership) VALUES (?, ?)
        ON CONFLICT(effective_date) DO UPDATE SET membership = excluded.membership
        """,
        (d, membership),
    )
    _conn().commit()


def get_history() -> list[dict]:
    """전체 등급 변경 이력을 최신순으로 반환한다. 설정 탭 이력 목록 표시용."""
    rows = _conn().execute(
        "SELECT effective_date, membership FROM membership_history ORDER BY effective_date DESC"
    ).fetchall()
    return [dict(r) for r in rows]


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
