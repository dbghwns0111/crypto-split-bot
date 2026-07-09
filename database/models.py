"""테이블 생성 DDL."""

import sqlite3


def create_tables(conn: sqlite3.Connection) -> None:
    """orders / bot_state / daily_pnl 테이블과 인덱스를 생성한다."""
    conn.executescript("""
        -- ── 주문 내역 ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol        TEXT    NOT NULL,
            order_id      TEXT    NOT NULL UNIQUE,
            side          TEXT    NOT NULL,          -- 'buy' | 'sell'
            price         REAL    NOT NULL,
            quantity      REAL    NOT NULL,
            amount        REAL    NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'open',
            round_no      INTEGER NOT NULL DEFAULT 1,
            filled_price  REAL,
            filled_at     TEXT,
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_orders_symbol_status
            ON orders (symbol, status);

        CREATE INDEX IF NOT EXISTS idx_orders_order_id
            ON orders (order_id);

        -- ── 봇 실행 상태 ───────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS bot_state (
            symbol             TEXT    PRIMARY KEY,
            reference_price    REAL    NOT NULL DEFAULT 0,
            start_price        REAL    NOT NULL DEFAULT 0,
            current_round      INTEGER NOT NULL DEFAULT 0,
            is_running         INTEGER NOT NULL DEFAULT 0,
            circuit_breaker_on INTEGER NOT NULL DEFAULT 0,
            cb_until           TEXT,
            updated_at         TEXT    NOT NULL
        );

        -- ── 일별 손익 ──────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS daily_pnl (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT    NOT NULL,
            symbol       TEXT    NOT NULL,
            realized_pnl REAL    NOT NULL DEFAULT 0,
            trade_count  INTEGER NOT NULL DEFAULT 0,
            fee_total    REAL    NOT NULL DEFAULT 0,
            point_total  REAL    NOT NULL DEFAULT 0,
            UNIQUE (date, symbol)
        );

        CREATE INDEX IF NOT EXISTS idx_daily_pnl_date
            ON daily_pnl (date, symbol);

        -- ── 수동 입력 평균매수가 ───────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS manual_avg_prices (
            symbol        TEXT PRIMARY KEY,
            avg_buy_price REAL NOT NULL DEFAULT 0,
            updated_at    TEXT NOT NULL
        );

        -- ── 로그인 브루트포스 방어(실패 횟수/잠금) ────────────────────────
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip           TEXT PRIMARY KEY,
            fail_count   INTEGER NOT NULL DEFAULT 0,
            window_start REAL,
            locked_until REAL,
            updated_at   TEXT NOT NULL
        );
    """)
    conn.commit()

    # 기존 DB 마이그레이션: start_price 컬럼이 없으면 추가
    try:
        conn.execute("ALTER TABLE bot_state ADD COLUMN start_price REAL NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass

    # 기존 DB 마이그레이션: point_total 컬럼이 없으면 추가
    try:
        conn.execute("ALTER TABLE daily_pnl ADD COLUMN point_total REAL NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass

    # 기존 DB 마이그레이션: is_catchup 컬럼이 없으면 추가
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN is_catchup INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass
