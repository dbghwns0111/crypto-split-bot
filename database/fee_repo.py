"""거래 수수료율 변경 이력 레포지토리.

빗썸 거래 수수료율은 보통 고정이지만, 이벤트(예: API 거래 수수료 무료)로 특정
기간만 다르게 적용될 수 있다. 손익 재계산 시(get_pnl_rounds 등) "현재" 설정을
과거 전체에 소급 적용하면, 이벤트가 끝나 수수료율이 원래대로 돌아갈 때 이벤트
기간 중 거래의 과거 기록까지 표시가 바뀌어 버린다 — membership_history와 동일한
문제/해결 패턴이다.

다만 멤버십 등급은 월 단위로 바뀌어 "그 달 1일"로 기록하지만, 수수료 이벤트는
특정 날짜에 정확히 시작/종료되므로 저장 시점이 아니라 사용자가 지정한(또는
오늘) 정확한 날짜를 기준일로 기록한다.
"""

import sqlite3
from typing import Optional

from database.connection import get_connection

SENTINEL_DATE = "2000-01-01"  # 이력 기록 이전 과거 전체에 적용되는 기준일


def _conn() -> sqlite3.Connection:
    return get_connection()


def seed_fee_rate_history(fee_rate: float) -> None:
    """이력이 비어 있으면 현재 설정값을 과거 전체 기본값으로 1건 기록한다."""
    row = _conn().execute("SELECT COUNT(*) AS cnt FROM fee_rate_history").fetchone()
    if row and row["cnt"] > 0:
        return
    _conn().execute(
        "INSERT INTO fee_rate_history (effective_date, fee_rate) VALUES (?, ?)",
        (SENTINEL_DATE, fee_rate),
    )
    _conn().commit()


def record_fee_rate_change(fee_rate: float, effective_date: Optional[str] = None) -> None:
    """수수료율 변경을 이력에 기록한다.

    effective_date 미지정 시 오늘(KST) 날짜부터 적용된 것으로 기록한다.
    같은 날짜에 재변경 시 마지막 값으로 덮어쓴다.
    """
    from utils.time_utils import today_kst_str

    d = effective_date or today_kst_str()
    _conn().execute(
        """
        INSERT INTO fee_rate_history (effective_date, fee_rate) VALUES (?, ?)
        ON CONFLICT(effective_date) DO UPDATE SET fee_rate = excluded.fee_rate
        """,
        (d, fee_rate),
    )
    _conn().commit()


def get_history() -> list[dict]:
    """전체 수수료율 변경 이력을 최신순으로 반환한다. 설정 탭 이력 목록 표시용."""
    rows = _conn().execute(
        "SELECT effective_date, fee_rate FROM fee_rate_history ORDER BY effective_date DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_fee_rate_at(date_str: str, default: float = 0.04) -> float:
    """지정 날짜(YYYY-MM-DD)에 적용 중이던 수수료율을 반환한다.

    해당 날짜 이전(포함)의 가장 최근 변경 이력을 사용한다. 이력이 전혀 없으면
    (마이그레이션 직후 등) default를 반환한다.
    """
    row = _conn().execute(
        """
        SELECT fee_rate FROM fee_rate_history
        WHERE effective_date <= ?
        ORDER BY effective_date DESC
        LIMIT 1
        """,
        (date_str,),
    ).fetchone()
    if row:
        return float(row["fee_rate"])
    # 조회 날짜가 이력상 가장 이른 기록보다도 앞선 경우 — 가장 오래된 값을 사용
    row = _conn().execute(
        "SELECT fee_rate FROM fee_rate_history ORDER BY effective_date ASC LIMIT 1"
    ).fetchone()
    return float(row["fee_rate"]) if row else default
