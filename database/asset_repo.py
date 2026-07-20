"""일별 보유자산(KRW + 코인 평가금액) 스냅샷 레포지토리.

실현손익만으로는 보유 중인 코인의 평가금액 등락(미실현 손익)이 반영되지
않아 "원금이 실제로 얼마나 늘고 줄었는지"를 알기 어렵다. 매일 한 번 총
보유자산을 스냅샷으로 남겨 변동 추이를 볼 수 있게 한다.
"""

import json
import sqlite3
from typing import Optional

from database.connection import get_connection
from utils.time_utils import now_kst_str, today_kst_str


def _conn() -> sqlite3.Connection:
    return get_connection()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["holdings"] = json.loads(d.get("holdings") or "{}")
    except (TypeError, ValueError):
        d["holdings"] = {}
    return d


def save_snapshot(
    total_assets: float, krw: float, holdings: dict, date: Optional[str] = None
) -> None:
    """오늘(또는 지정 날짜)의 보유자산 스냅샷을 저장한다(같은 날짜면 덮어씀)."""
    d = date or today_kst_str()
    _conn().execute(
        """
        INSERT INTO asset_snapshots (date, total_assets, krw, holdings, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            total_assets = excluded.total_assets,
            krw          = excluded.krw,
            holdings     = excluded.holdings,
            created_at   = excluded.created_at
        """,
        (d, total_assets, krw, json.dumps(holdings, ensure_ascii=False), now_kst_str()),
    )
    _conn().commit()


def get_snapshot_history(start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    """기간 내 일별 보유자산 스냅샷을 날짜순으로 반환한다. start/end 미지정 시 전체."""
    where, params = "", []
    if start and end:
        where = "WHERE date BETWEEN ? AND ?"
        params = [start, end]
    rows = _conn().execute(
        f"SELECT date, total_assets, krw, holdings FROM asset_snapshots {where} ORDER BY date",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_latest_snapshot() -> Optional[dict]:
    """가장 최근 스냅샷을 반환한다. 없으면 None."""
    row = _conn().execute(
        "SELECT date, total_assets, krw, holdings FROM asset_snapshots ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return _row_to_dict(row) if row else None
