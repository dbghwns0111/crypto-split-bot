"""스레드별 독립 SQLite 커넥션 관리."""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

_log = get_logger("database.connection")

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


# ── 백업 / 무결성 ──────────────────────────────────────────────────────────────

def backup_database(backup_dir: str = "data/backups", keep: int = 14) -> Optional[str]:
    """DB를 온라인 백업(WAL-safe)하고 오래된 백업을 정리한다.

    SQLite의 connection.backup() API는 WAL 모드에서도 일관된 스냅샷을 만들어
    봇 가동 중에도 안전하게 백업할 수 있다.

    Returns:
        생성된 백업 파일 경로. 메모리 DB이거나 실패 시 None.
    """
    src_path = _resolve_path()
    if src_path == ":memory:":
        return None

    try:
        out_dir = Path(backup_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = out_dir / f"cryptosplit_{ts}.db"

        dst = sqlite3.connect(str(backup_path))
        try:
            get_connection().backup(dst)
        finally:
            dst.close()

        _prune_backups(out_dir, keep)
        _log.info("DB 백업 완료: %s", backup_path)
        return str(backup_path)
    except Exception as exc:
        _log.error("DB 백업 실패: %s", exc)
        return None


def _prune_backups(out_dir: Path, keep: int) -> None:
    """오래된 백업 파일을 keep개만 남기고 삭제한다."""
    backups = sorted(out_dir.glob("cryptosplit_*.db"))
    excess = len(backups) - keep
    for old in backups[:max(0, excess)]:
        try:
            old.unlink()
            _log.debug("오래된 백업 삭제: %s", old)
        except Exception as exc:
            _log.warning("백업 삭제 실패 (%s): %s", old, exc)


def check_integrity() -> bool:
    """PRAGMA integrity_check 를 실행한다. 결과가 'ok'면 True."""
    try:
        row = get_connection().execute("PRAGMA integrity_check").fetchone()
        result = row[0] if row else None
        if result == "ok":
            return True
        _log.error("DB 무결성 검사 실패: %s", result)
        return False
    except Exception as exc:
        _log.error("DB 무결성 검사 오류: %s", exc)
        return False
