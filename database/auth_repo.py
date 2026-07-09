"""로그인 실패/잠금 상태 영속화 레포지토리 (브루트포스 방어)."""

import sqlite3
from typing import Optional

from database.connection import get_connection
from utils.time_utils import now_kst_str


def _conn() -> sqlite3.Connection:
    return get_connection()


def get_attempt(ip: str) -> Optional[dict]:
    """ip의 로그인 시도 기록을 반환한다. 없으면 None."""
    row = _conn().execute(
        "SELECT * FROM login_attempts WHERE ip = ?", (ip,)
    ).fetchone()
    return dict(row) if row else None


def upsert_attempt(ip: str, fail_count: int, window_start: float, locked_until: float) -> None:
    """ip의 로그인 실패/잠금 기록을 저장한다 (없으면 생성, 있으면 갱신)."""
    _conn().execute(
        """
        INSERT INTO login_attempts (ip, fail_count, window_start, locked_until, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ip) DO UPDATE SET
            fail_count   = excluded.fail_count,
            window_start = excluded.window_start,
            locked_until = excluded.locked_until,
            updated_at   = excluded.updated_at
        """,
        (ip, fail_count, window_start, locked_until, now_kst_str()),
    )
    _conn().commit()


def clear_attempt(ip: str) -> None:
    """ip의 로그인 실패/잠금 기록을 삭제한다 (로그인 성공 시)."""
    _conn().execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
    _conn().commit()
