"""봇 실행 상태 영속화 레포지토리."""

import sqlite3
from typing import Optional

from database.connection import get_connection
from utils.time_utils import now_kst_str


def _conn() -> sqlite3.Connection:
    return get_connection()


def get_state(symbol: str) -> Optional[dict]:
    """symbol의 봇 상태를 반환한다. 없으면 None."""
    row = _conn().execute(
        "SELECT * FROM bot_state WHERE symbol = ?", (symbol,)
    ).fetchone()
    return dict(row) if row else None


def upsert_state(
    symbol: str,
    reference_price: float = 0.0,
    current_round: int = 0,
    is_running: bool = False,
    circuit_breaker_on: bool = False,
    cb_until: Optional[str] = None,
) -> None:
    """봇 상태를 저장한다. 없으면 INSERT, 있으면 전체 UPDATE."""
    now = now_kst_str()
    _conn().execute(
        """
        INSERT INTO bot_state
            (symbol, reference_price, current_round, is_running,
             circuit_breaker_on, cb_until, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            reference_price    = excluded.reference_price,
            current_round      = excluded.current_round,
            is_running         = excluded.is_running,
            circuit_breaker_on = excluded.circuit_breaker_on,
            cb_until           = excluded.cb_until,
            updated_at         = excluded.updated_at
        """,
        (symbol, reference_price, current_round, int(is_running),
         int(circuit_breaker_on), cb_until, now),
    )
    _conn().commit()


def set_reference_price(symbol: str, price: float) -> None:
    """기준가만 업데이트한다."""
    _conn().execute(
        "UPDATE bot_state SET reference_price = ?, updated_at = ? WHERE symbol = ?",
        (price, now_kst_str(), symbol),
    )
    _conn().commit()


def set_running(symbol: str, is_running: bool) -> None:
    """실행 상태만 업데이트한다."""
    _conn().execute(
        "UPDATE bot_state SET is_running = ?, updated_at = ? WHERE symbol = ?",
        (int(is_running), now_kst_str(), symbol),
    )
    _conn().commit()


def set_circuit_breaker(
    symbol: str,
    active: bool,
    cb_until: Optional[str] = None,
) -> None:
    """서킷브레이커 상태만 업데이트한다."""
    _conn().execute(
        """
        UPDATE bot_state
        SET circuit_breaker_on = ?, cb_until = ?, updated_at = ?
        WHERE symbol = ?
        """,
        (int(active), cb_until, now_kst_str(), symbol),
    )
    _conn().commit()


def increment_round(symbol: str) -> int:
    """차수를 1 증가시키고 새 차수를 반환한다."""
    _conn().execute(
        """
        UPDATE bot_state
        SET current_round = current_round + 1, updated_at = ?
        WHERE symbol = ?
        """,
        (now_kst_str(), symbol),
    )
    _conn().commit()
    row = _conn().execute(
        "SELECT current_round FROM bot_state WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["current_round"] if row else 0


def set_start_price(symbol: str, price: float) -> None:
    """전략 시작가를 저장한다."""
    now = now_kst_str()
    _conn().execute(
        """
        INSERT INTO bot_state (symbol, start_price, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            start_price = excluded.start_price,
            updated_at  = excluded.updated_at
        """,
        (symbol, price, now),
    )
    _conn().commit()


def decrement_round(symbol: str) -> int:
    """차수를 1 감소시키고 새 차수를 반환한다 (최소 0)."""
    _conn().execute(
        """
        UPDATE bot_state
        SET current_round = MAX(0, current_round - 1), updated_at = ?
        WHERE symbol = ?
        """,
        (now_kst_str(), symbol),
    )
    _conn().commit()
    row = _conn().execute(
        "SELECT current_round FROM bot_state WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["current_round"] if row else 0
