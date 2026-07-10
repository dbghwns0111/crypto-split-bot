"""주문 CRUD 레포지토리."""

import sqlite3
import time
from typing import Optional

from database.connection import get_connection
from utils.logger import get_logger
from utils.time_utils import now_kst_str

_log = get_logger("database.order_repo")

# save_order 일시적 오류(락 등) 재시도 간격(초)
_SAVE_ORDER_RETRY_DELAYS = (0.1, 0.3)


def _conn() -> sqlite3.Connection:
    return get_connection()


def save_order(
    symbol: str,
    order_id: str,
    side: str,
    price: float,
    quantity: float,
    amount: float,
    round_no: int,
) -> int:
    """신규 주문을 저장하고 row id를 반환한다.

    거래소 주문은 이미 체결/접수된 상태에서 호출되므로, DB 저장 실패로
    주문 기록이 유실되지 않도록 일시적 오류(락 등)에 짧게 재시도하고,
    그래도 실패하면 주문 정보를 CRITICAL 로그로 남긴 뒤 예외를 올린다.
    """
    now = now_kst_str()
    attempts = len(_SAVE_ORDER_RETRY_DELAYS) + 1
    last_exc: Optional[Exception] = None

    for attempt in range(attempts):
        try:
            cur = _conn().execute(
                """
                INSERT INTO orders
                    (symbol, order_id, side, price, quantity, amount,
                     status, round_no, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (symbol, order_id, side, price, quantity, amount, round_no, now, now),
            )
            _conn().commit()
            return cur.lastrowid
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = _SAVE_ORDER_RETRY_DELAYS[attempt]
                _log.warning(
                    "save_order DB 쓰기 실패 (시도 %d/%d), %.1f초 후 재시도: %s %s %s",
                    attempt + 1, attempts, delay, symbol, order_id, exc,
                )
                time.sleep(delay)

    _log.critical(
        "save_order 최종 실패 — 거래소에는 주문이 존재할 수 있으나 DB 기록 유실: "
        "symbol=%s order_id=%s side=%s price=%s quantity=%s amount=%s round_no=%s 오류=%s",
        symbol, order_id, side, price, quantity, amount, round_no, last_exc,
    )
    raise last_exc


def update_order(
    order_id: str,
    status: str,
    filled_price: Optional[float] = None,
    filled_at: Optional[str] = None,
) -> None:
    """주문 상태를 업데이트한다."""
    now = now_kst_str()
    _conn().execute(
        """
        UPDATE orders
        SET status = ?, filled_price = ?, filled_at = ?, updated_at = ?
        WHERE order_id = ?
        """,
        (status, filled_price, filled_at, now, order_id),
    )
    _conn().commit()


def update_order_fill(
    order_id: str,
    filled_price: float,
    filled_qty: float,
    filled_at: Optional[str] = None,
) -> None:
    """부분 체결 정산용 — 체결 수량/금액/가격으로 행을 보정하고 filled 처리한다.

    부분 체결 시 주문 행에는 원래 주문 수량이 저장돼 있으므로, 실제 체결 수량으로
    quantity/amount를 정정해 이후 매도가 올바른 수량을 사용하도록 한다.
    """
    now = filled_at or now_kst_str()
    _conn().execute(
        """
        UPDATE orders
        SET status = 'filled', filled_price = ?, quantity = ?, amount = ?,
            filled_at = ?, updated_at = ?
        WHERE order_id = ?
        """,
        (filled_price, filled_qty, round(filled_price * filled_qty, 2), now, now, order_id),
    )
    _conn().commit()


def get_order(order_id: str) -> Optional[dict]:
    """order_id로 단건 조회. 없으면 None."""
    row = _conn().execute(
        "SELECT * FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    return dict(row) if row else None


def get_open_orders(symbol: str) -> list[dict]:
    """symbol의 미체결 주문 목록을 반환한다."""
    rows = _conn().execute(
        "SELECT * FROM orders WHERE symbol = ? AND status = 'open' ORDER BY created_at",
        (symbol,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_active_buy_count(symbol: str) -> int:
    """symbol의 체결된 매수 주문 수(= 현재 차수)를 반환한다."""
    row = _conn().execute(
        """
        SELECT COUNT(*) AS cnt FROM orders
        WHERE symbol = ? AND side = 'buy' AND status = 'filled'
        """,
        (symbol,),
    ).fetchone()
    return row["cnt"] if row else 0


def get_latest_filled_buy(symbol: str) -> Optional[dict]:
    """가장 최근 체결된 매수 주문을 반환한다."""
    row = _conn().execute(
        """
        SELECT * FROM orders
        WHERE symbol = ? AND side = 'buy' AND status = 'filled'
        ORDER BY filled_at DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    return dict(row) if row else None


def get_filled_buy_for_round(symbol: str, round_no: int) -> Optional[dict]:
    """특정 차수의 가장 최근 체결된 매수 주문을 반환한다."""
    row = _conn().execute(
        """
        SELECT * FROM orders
        WHERE symbol = ? AND side = 'buy' AND status = 'filled' AND round_no = ?
        ORDER BY filled_at DESC
        LIMIT 1
        """,
        (symbol, round_no),
    ).fetchone()
    return dict(row) if row else None


def get_today_trade_counts(symbol: str, date: Optional[str] = None) -> dict:
    """오늘 날짜의 매수/매도 체결 횟수를 반환한다."""
    from utils.time_utils import today_kst_str
    d = date or today_kst_str()
    row = _conn().execute(
        """
        SELECT
            SUM(CASE WHEN side = 'buy'  THEN 1 ELSE 0 END) AS buy_count,
            SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) AS sell_count
        FROM orders
        WHERE symbol = ? AND status = 'filled'
          AND DATE(filled_at) = ?
          AND order_id NOT LIKE 'virt-%'
        """,
        (symbol, d),
    ).fetchone()
    return {
        "buy":  int(row["buy_count"]  or 0) if row else 0,
        "sell": int(row["sell_count"] or 0) if row else 0,
    }


def get_today_totals(symbols: list, date: Optional[str] = None) -> dict:
    """지정 심볼들의 오늘 매수/매도 체결 총액을 반환한다 (가상 캐치업 제외)."""
    from utils.time_utils import today_kst_str
    if not symbols:
        return {"total_buy": 0.0, "total_sell": 0.0}
    d = date or today_kst_str()
    placeholders = ",".join("?" * len(symbols))
    row = _conn().execute(
        f"""
        SELECT
            SUM(CASE WHEN side = 'buy'  THEN amount ELSE 0 END) AS total_buy,
            SUM(CASE WHEN side = 'sell' THEN amount ELSE 0 END) AS total_sell
        FROM orders
        WHERE symbol IN ({placeholders}) AND status = 'filled'
          AND DATE(filled_at) = ?
          AND order_id NOT LIKE 'virt-%'
        """,
        (*symbols, d),
    ).fetchone()
    return {
        "total_buy":  float(row["total_buy"]  or 0) if row else 0.0,
        "total_sell": float(row["total_sell"] or 0) if row else 0.0,
    }


def get_filled_orders(symbol: str, date: str) -> list[dict]:
    """특정 날짜(YYYY-MM-DD)의 체결 완료 주문 목록을 반환한다."""
    rows = _conn().execute(
        """
        SELECT * FROM orders
        WHERE symbol = ? AND status = 'filled'
          AND filled_at LIKE ?
        ORDER BY filled_at
        """,
        (symbol, f"{date}%"),
    ).fetchall()
    return [dict(r) for r in rows]


def get_orders(symbol: str, limit: int = 100) -> list[dict]:
    """symbol의 최근 주문 목록을 반환한다."""
    rows = _conn().execute(
        "SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_cumulative_totals(
    start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    """심볼별 실현손익 합계 — 완료된 매도 주문과 매칭된 매수 금액만 집계한다.
    미체결 포지션(아직 팔지 않은 매수)은 포함하지 않는다.

    start/end (YYYY-MM-DD) 를 주면 해당 기간에 '매도 체결'된 건만 집계한다.
    둘 중 하나라도 없으면 전체 기간(all-time)을 집계한다.

    가상 캐치업(order_id 'virt-' 접두)으로 생성된 매수는 실제 거래소 주문이
    아니므로 total_buy/buy_count에서 제외한다 — 매도(sell)는 가상으로 생성되지
    않으므로 total_sell은 그대로 둔다.
    """
    where  = "WHERE s.side = 'sell' AND s.status = 'filled'"
    params: list = []
    if start and end:
        where += " AND date(s.filled_at) BETWEEN ? AND ?"
        params += [start, end]

    rows = _conn().execute(
        f"""
        SELECT s.symbol,
               COALESCE(SUM(CASE WHEN b.order_id LIKE 'virt-%' THEN 0 ELSE b.amount END), 0) AS total_buy,
               COALESCE(SUM(s.amount), 0) AS total_sell,
               COUNT(s.id)                AS sell_count,
               COUNT(CASE WHEN b.order_id LIKE 'virt-%' THEN NULL ELSE b.id END) AS buy_count
        FROM orders s
        LEFT JOIN orders b ON (
            b.symbol   = s.symbol
            AND b.round_no = s.round_no
            AND b.side     = 'buy'
            AND b.status   = 'filled'
            AND b.id = (
                SELECT id FROM orders b2
                WHERE b2.symbol   = s.symbol
                  AND b2.round_no = s.round_no
                  AND b2.side     = 'buy'
                  AND b2.status   = 'filled'
                  AND b2.filled_at <= s.filled_at
                ORDER BY b2.filled_at DESC, b2.id DESC
                LIMIT 1
            )
        )
        {where}
        GROUP BY s.symbol
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_first_filled_date(symbols: list) -> Optional[str]:
    """지정 심볼들 중 가장 이른 체결일(YYYY-MM-DD)을 반환한다. 없으면 None."""
    if not symbols:
        return None
    placeholders = ",".join("?" * len(symbols))
    row = _conn().execute(
        f"""
        SELECT MIN(date(filled_at)) AS d
        FROM orders
        WHERE status = 'filled' AND filled_at IS NOT NULL
          AND symbol IN ({placeholders})
        """,
        list(symbols),
    ).fetchone()
    return row["d"] if row and row["d"] else None


def get_position_cost(symbol: str) -> float:
    """현재 포지션 순 투입금 (총 매수금액 - 총 매도금액)을 반환한다."""
    row = _conn().execute(
        """
        SELECT
            SUM(CASE WHEN side = 'buy'  THEN amount ELSE 0 END) -
            SUM(CASE WHEN side = 'sell' THEN amount ELSE 0 END) AS net_cost
        FROM orders
        WHERE symbol = ? AND status = 'filled'
        """,
        (symbol,),
    ).fetchone()
    if row and row["net_cost"] is not None:
        return max(0.0, float(row["net_cost"]))
    return 0.0


def get_avg_buy_price(symbol: str) -> float:
    """체결된 매수 주문 기반 가중평균 매수가를 반환한다."""
    row = _conn().execute(
        """
        SELECT
            SUM(filled_price * quantity) AS weighted_sum,
            SUM(quantity) AS total_qty
        FROM orders
        WHERE symbol = ? AND side = 'buy' AND status = 'filled'
          AND filled_price IS NOT NULL AND quantity > 0
        """,
        (symbol,),
    ).fetchone()
    if row and row["total_qty"] and row["total_qty"] > 0:
        return float(row["weighted_sum"]) / float(row["total_qty"])
    return 0.0


def get_manual_avg_buy_price(symbol: str) -> float:
    """수동 입력된 평균매수가를 반환한다."""
    row = _conn().execute(
        "SELECT avg_buy_price FROM manual_avg_prices WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    return float(row["avg_buy_price"]) if row else 0.0


def get_recent_filled_orders(symbols: list, limit: int = 20) -> list:
    """복수 심볼의 최근 체결 주문 목록을 반환한다."""
    if not symbols:
        return []
    ph = ",".join("?" * len(symbols))
    rows = _conn().execute(
        f"""
        SELECT * FROM orders
        WHERE symbol IN ({ph}) AND status = 'filled'
        ORDER BY filled_at DESC
        LIMIT ?
        """,
        (*symbols, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_filled_orders_range(
    symbols: list,
    start: str,
    end: str,
    symbol_filter: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """기간(start~end, YYYY-MM-DD) 내 체결 주문 목록을 반환한다."""
    if not symbols:
        return []
    if symbol_filter and symbol_filter in symbols:
        where = "symbol = ?"
        params: list = [symbol_filter, start, end, limit]
    else:
        ph = ",".join("?" * len(symbols))
        where = f"symbol IN ({ph})"
        params = [*symbols, start, end, limit]
    rows = _conn().execute(
        f"SELECT * FROM orders WHERE {where} AND status = 'filled'"
        " AND DATE(filled_at) BETWEEN ? AND ? ORDER BY filled_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_pnl_rounds(
    symbols: list,
    start: str,
    end: str,
    symbol_filter: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """기간 내 완료된 차수별 실현손익을 반환한다 (매도-매수 LEFT JOIN).

    각 행에 buy_is_virtual(가상 캐치업 매수 여부)을 포함한다 — 실제 거래대금
    집계 시 가상 매수분을 제외하기 위함(손익 계산 자체는 그대로 유지).
    """
    if not symbols:
        return []
    if symbol_filter and symbol_filter in symbols:
        sym_clause = "s.symbol = ?"
        params: list = [symbol_filter, start, end, limit]
    else:
        ph = ",".join("?" * len(symbols))
        sym_clause = f"s.symbol IN ({ph})"
        params = [*symbols, start, end, limit]
    rows = _conn().execute(
        f"""
        SELECT s.id AS sell_id, s.symbol, s.round_no,
               b.filled_price AS buy_price,  s.filled_price AS sell_price,
               s.quantity,
               b.amount AS buy_amount, s.amount AS sell_amount,
               b.order_id AS buy_order_id,
               s.filled_at
        FROM orders s
        LEFT JOIN orders b
            ON b.id = (
                SELECT id FROM orders
                WHERE symbol  = s.symbol
                  AND round_no = s.round_no
                  AND side    = 'buy'
                  AND status  = 'filled'
                  AND filled_at <= s.filled_at
                ORDER BY filled_at DESC
                LIMIT 1
            )
        WHERE s.side   = 'sell'
          AND s.status = 'filled'
          AND {sym_clause}
          AND DATE(s.filled_at) BETWEEN ? AND ?
        ORDER BY s.filled_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    result = []
    for r in [dict(x) for x in rows]:
        buy_a  = float(r.get("buy_amount")  or 0)
        sell_a = float(r.get("sell_amount") or 0)
        buy_p  = float(r.get("buy_price")   or 0)
        sell_p = float(r.get("sell_price")  or 0)
        qty    = float(r.get("quantity")    or 0)
        fee    = (buy_a + sell_a) * 0.0005
        r["pnl"] = round((sell_p - buy_p) * qty - fee, 0) if buy_p > 0 else None
        r["buy_is_virtual"] = bool((r.get("buy_order_id") or "").startswith("virt-"))
        result.append(r)
    return result


def get_hourly_pnl(symbols: list, start: str, end: str) -> list[dict]:
    """기간 내 시간대별(0-23시) 실현손익을 반환한다. 누락 시간은 0으로 채워 24개 반환."""
    empty = [{"hour": h, "trade_count": 0, "realized_pnl": 0.0} for h in range(24)]
    if not symbols:
        return empty
    ph = ",".join("?" * len(symbols))
    rows = _conn().execute(
        f"""
        SELECT
            CAST(strftime('%H', s.filled_at) AS INTEGER) AS hour,
            COUNT(*) AS trade_count,
            SUM(
                CASE WHEN b.filled_price IS NOT NULL AND b.filled_price > 0
                     THEN (s.filled_price - b.filled_price) * s.quantity
                          - (b.amount + s.amount) * 0.0005
                     ELSE 0 END
            ) AS realized_pnl
        FROM orders s
        LEFT JOIN orders b ON (
            b.id = (
                SELECT id FROM orders
                WHERE symbol  = s.symbol
                  AND round_no = s.round_no
                  AND side    = 'buy'
                  AND status  = 'filled'
                  AND filled_at <= s.filled_at
                ORDER BY filled_at DESC
                LIMIT 1
            )
        )
        WHERE s.side   = 'sell'
          AND s.status = 'filled'
          AND s.symbol IN ({ph})
          AND DATE(s.filled_at) BETWEEN ? AND ?
        GROUP BY hour
        ORDER BY hour
        """,
        (*symbols, start, end),
    ).fetchall()
    by_hour = {
        r["hour"]: {
            "hour":        r["hour"],
            "trade_count": int(r["trade_count"] or 0),
            "realized_pnl": round(float(r["realized_pnl"] or 0)),
        }
        for r in rows
    }
    return [by_hour.get(h, {"hour": h, "trade_count": 0, "realized_pnl": 0.0}) for h in range(24)]


def get_weekday_pnl(symbols: list, start: str, end: str) -> list[dict]:
    """기간 내 요일별(월~일) 실현손익을 반환한다. 누락 요일은 0으로 채워 7개 반환.

    weekday: 0=월 ... 6=일 (SQLite strftime('%w')는 0=일이므로 +6 % 7로 변환).
    """
    empty = [{"weekday": w, "trade_count": 0, "realized_pnl": 0.0} for w in range(7)]
    if not symbols:
        return empty
    ph = ",".join("?" * len(symbols))
    rows = _conn().execute(
        f"""
        SELECT
            (CAST(strftime('%w', s.filled_at) AS INTEGER) + 6) % 7 AS weekday,
            COUNT(*) AS trade_count,
            SUM(
                CASE WHEN b.filled_price IS NOT NULL AND b.filled_price > 0
                     THEN (s.filled_price - b.filled_price) * s.quantity
                          - (b.amount + s.amount) * 0.0005
                     ELSE 0 END
            ) AS realized_pnl
        FROM orders s
        LEFT JOIN orders b ON (
            b.id = (
                SELECT id FROM orders
                WHERE symbol  = s.symbol
                  AND round_no = s.round_no
                  AND side    = 'buy'
                  AND status  = 'filled'
                  AND filled_at <= s.filled_at
                ORDER BY filled_at DESC
                LIMIT 1
            )
        )
        WHERE s.side   = 'sell'
          AND s.status = 'filled'
          AND s.symbol IN ({ph})
          AND DATE(s.filled_at) BETWEEN ? AND ?
        GROUP BY weekday
        ORDER BY weekday
        """,
        (*symbols, start, end),
    ).fetchall()
    by_weekday = {
        r["weekday"]: {
            "weekday":      r["weekday"],
            "trade_count":  int(r["trade_count"] or 0),
            "realized_pnl": round(float(r["realized_pnl"] or 0)),
        }
        for r in rows
    }
    return [by_weekday.get(w, {"weekday": w, "trade_count": 0, "realized_pnl": 0.0}) for w in range(7)]


def delete_orders_by_ids(ids: list) -> tuple:
    """DB id 목록으로 주문을 삭제한다. (삭제 건수, 영향받은 [(symbol, date)] 목록) 반환."""
    if not ids:
        return 0, []
    ph = ",".join("?" * len(ids))
    rows = _conn().execute(
        f"SELECT symbol, DATE(filled_at) AS d FROM orders WHERE id IN ({ph})",
        ids,
    ).fetchall()
    affected = list({(r["symbol"], r["d"]) for r in rows if r["d"]})
    cur = _conn().execute(f"DELETE FROM orders WHERE id IN ({ph})", ids)
    _conn().commit()
    return cur.rowcount, affected


def delete_orders_by_range(
    symbols: list,
    start: str,
    end: str,
    symbol_filter: Optional[str] = None,
) -> tuple:
    """기간 + 심볼 필터로 주문을 삭제한다. (삭제 건수, 영향받은 [(symbol, date)] 목록) 반환."""
    if not symbols:
        return 0, []
    if symbol_filter and symbol_filter in symbols:
        where = "symbol = ?"
        params: list = [symbol_filter, start, end]
    else:
        ph = ",".join("?" * len(symbols))
        where = f"symbol IN ({ph})"
        params = [*symbols, start, end]
    rows = _conn().execute(
        f"SELECT symbol, DATE(filled_at) AS d FROM orders WHERE {where}"
        " AND DATE(filled_at) BETWEEN ? AND ?",
        params,
    ).fetchall()
    affected = list({(r["symbol"], r["d"]) for r in rows if r["d"]})
    cur = _conn().execute(
        f"DELETE FROM orders WHERE {where}"
        " AND DATE(filled_at) BETWEEN ? AND ?",
        params,
    )
    _conn().commit()
    return cur.rowcount, affected


def save_catchup_order(
    symbol: str,
    order_id: str,
    side: str,
    grid_price: float,
    filled_price: float,
    quantity: float,
    amount: float,
    round_no: int,
    filled_at: Optional[str] = None,
) -> int:
    """캐치업 매수 주문을 즉시 체결 상태로 저장한다.

    grid_price: 차수 테이블 표시용 의도 그리드 가격
    filled_price: 실제 체결가 (PnL 계산 기준)
    filled_at: 체결시각(미지정 시 현재). 가상 캐치업은 차수 순서대로 증가하는 값을 전달해
               체결내역이 1차(아래)→고차수(위)로 시간순 정렬되도록 한다.
    """
    now = now_kst_str()
    ts  = filled_at or now
    cur = _conn().execute(
        """
        INSERT INTO orders
            (symbol, order_id, side, price, quantity, amount,
             status, round_no, filled_price, filled_at,
             created_at, updated_at, is_catchup)
        VALUES (?, ?, ?, ?, ?, ?, 'filled', ?, ?, ?, ?, ?, 1)
        """,
        (symbol, order_id, side, grid_price, quantity, amount,
         round_no, filled_price, ts, now, now),
    )
    _conn().commit()
    return cur.lastrowid


def set_manual_avg_buy_price(symbol: str, price: float) -> None:
    """수동 평균매수가를 저장(UPSERT)한다."""
    now = now_kst_str()
    _conn().execute(
        """
        INSERT INTO manual_avg_prices (symbol, avg_buy_price, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            avg_buy_price = excluded.avg_buy_price,
            updated_at    = excluded.updated_at
        """,
        (symbol, price, now),
    )
    _conn().commit()
