"""스레드별 독립 SQLite 커넥션 관리."""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

_local = threading.local()
_db_path: Optional[str] = None


def configure(path: Optional[str]) -> None:
    """DB 경로를 설정하고 현재 스레드의 기존 커넥션을 닫는다.

    path=None 으로 호출하면 settings.yaml 기본값으로 복귀한다.
    테스트에서는 path=':memory:' 를 사용한다.
    """
    global _db_path
    _db_path = path
    close_connection()


def get_connection() -> sqlite3.Connection:
    """현재 스레드의 커넥션을 반환한다. 없으면 새로 생성한다."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _make_conn(_resolve_path())
    return _local.conn


def close_connection() -> None:
    """현재 스레드의 커넥션을 닫는다."""
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def close_all() -> None:
    """프로세스 종료 시 호출 — 현재 스레드 커넥션을 닫는다."""
    close_connection()


# ── 내부 헬퍼 ──────────────────────────────────────────────────────────────────

def _resolve_path() -> str:
    if _db_path is not None:
        return _db_path
    try:
        from config.loader import get
        return get("database.path", "data/cryptosplit.db")
    except Exception:
        return "data/cryptosplit.db"


def _make_conn(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
