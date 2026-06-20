"""일별 손익 누적 레포지토리."""

import sqlite3
from typing import Optional

from database.connection import get_connection
from utils.time_utils import today_kst_str


def _conn() -> sqlite3.Connection:
    return get_connection()


def add_trade_pnl(
    symbol: str,
    realized_pnl: float,
    fee: float,
    points: float = 0.0,
    date: Optional[str] = None,
) -> None:
    """거래 손익을 당일 누적 레코드에 더한다 (upsert)."""
    d = date or today_kst_str()
    _conn().execute(
        """
        INSERT INTO daily_pnl (date, symbol, realized_pnl, trade_count, fee_total, point_total)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(date, symbol) DO UPDATE SET
            realized_pnl = realized_pnl + excluded.realized_pnl,
            trade_count  = trade_count  + 1,
            fee_total    = fee_total    + excluded.fee_total,
            point_total  = point_total  + excluded.point_total
        """,
        (d, symbol, realized_pnl, fee, points),
    )
    _conn().commit()


def get_daily_pnl(symbol: str, date: Optional[str] = None) -> Optional[dict]:
    """특정 날짜의 symbol 손익을 반환한다. 없으면 None."""
    d = date or today_kst_str()
    row = _conn().execute(
        "SELECT * FROM daily_pnl WHERE date = ? AND symbol = ?", (d, symbol)
    ).fetchone()
    return dict(row) if row else None


def get_daily_summary(date: Optional[str] = None) -> list[dict]:
    """날짜의 전체 symbol 손익 목록을 반환한다."""
    d = date or today_kst_str()
    rows = _conn().execute(
        "SELECT * FROM daily_pnl WHERE date = ? ORDER BY symbol", (d,)
    ).fetchall()
    return [dict(r) for r in rows]


def delete_daily_pnl(symbol: str, date: str) -> None:
    """특정 날짜의 symbol 일별 손익 레코드를 삭제한다."""
    _conn().execute("DELETE FROM daily_pnl WHERE date = ? AND symbol = ?", (date, symbol))
    _conn().commit()


def get_period_pnl(symbol: str, start: str, end: str) -> list[dict]:
    """start ~ end(포함) 기간의 symbol 일별 손익을 반환한다."""
    rows = _conn().execute(
        """
        SELECT * FROM daily_pnl
        WHERE symbol = ? AND date BETWEEN ? AND ?
        ORDER BY date
        """,
        (symbol, start, end),
    ).fetchall()
    return [dict(r) for r in rows]
