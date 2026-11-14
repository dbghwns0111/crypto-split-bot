"""터미널용 자동매매 현황 — cs-status 가 호출한다 (읽기 전용).

DB(bot_state/orders/daily_pnl)는 읽기 전용으로 열고, 현재가는 빗썸 공개 시세 API로 1회 조회한다.
봇 프로세스와 무관하게 동작하므로 서버가 꺼져 있어도 마지막 기록 상태를 보여준다.
※ 다음 매수/매도가는 설정값 기준 계산이다. pct 모드의 ATR 동적 간격은 반영하지 않아 '≈'로 표시한다.
"""

import json
import sqlite3
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))


def _w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s, n: int, right: bool = True) -> str:
    s = str(s)
    gap = max(0, n - _w(s))
    return " " * gap + s if right else s + " " * gap


def _num(v) -> str:
    if v is None or v == "":
        return "-"
    v = float(v)
    if v == 0:
        return "0"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")


def _load_pairs() -> dict:
    try:
        import yaml
        with open(ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("pairs", {}) or {}
    except Exception:
        return {}


def _prices(tickers: list[str]) -> dict:
    try:
        url = "https://api.bithumb.com/v1/ticker?markets=" + ",".join(tickers)
        with urllib.request.urlopen(url, timeout=4) as r:
            return {d["market"]: float(d["trade_price"]) for d in json.load(r)}
    except Exception:
        return {}


def main() -> int:
    db = ROOT / "data" / "cryptosplit.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_state)")}
    has_desired = "desired_running" in cols
    states = conn.execute("SELECT * FROM bot_state ORDER BY symbol").fetchall()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    pnl = {r["symbol"]: r for r in conn.execute(
        "SELECT symbol, realized_pnl, trade_count FROM daily_pnl WHERE date = ?", (today,))}
    opens = dict(conn.execute(
        "SELECT symbol, COUNT(*) FROM orders WHERE status='open' GROUP BY symbol").fetchall())
    last_fill = dict(conn.execute(
        "SELECT symbol, MAX(filled_at) FROM orders WHERE status='filled' GROUP BY symbol").fetchall())

    pairs = _load_pairs()
    order = [s for s in ("BTC", "ETH", "XRP", "USDT") if any(r["symbol"] == s for r in states)]
    order += [r["symbol"] for r in states if r["symbol"] not in order]
    by_sym = {r["symbol"]: r for r in states}
    prices = _prices([pairs.get(s, {}).get("ticker", f"KRW-{s}") for s in order])

    head = ["종목", "상태", "차수", "현재가", "기준가", "다음매수", "다음매도", "미체결", "오늘손익", "마지막 체결"]
    widths = [5, 8, 8, 13, 13, 13, 13, 6, 14, 11]
    print(f"CryptoSplit 자동매매 현황  ({datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')})")
    print("  ".join(_pad(h, w, right=i not in (0, 1)) for i, (h, w) in enumerate(zip(head, widths))))
    print("─" * (sum(widths) + 2 * (len(widths) - 1)))

    total_pnl, total_cnt, running = 0.0, 0, 0
    for sym in order:
        st = by_sym[sym]
        pair = pairs.get(sym, {})
        ticker = pair.get("ticker", f"KRW-{sym}")
        rnd = int(st["current_round"] or 0)
        ref = float(st["reference_price"] or 0)
        start = float(st["start_price"] or 0)
        is_run = bool(st["is_running"])
        running += is_run
        desired = bool(st["desired_running"]) if has_desired else is_run
        status = "가동" if is_run else ("중지*" if desired else "중지")

        mode = pair.get("trigger_mode", "pct")
        approx = "" if mode == "krw" else "≈"
        if mode == "krw":
            b_gap = float(pair.get("trigger_krw") or 0)
            s_gap = float(pair.get("trigger_krw_sell") or 0) or b_gap
            nxt_buy = (start if rnd == 0 else ref - b_gap) if ref > 0 else None
            nxt_sell = ref + s_gap if rnd > 0 and ref > 0 else None
        else:
            b_pct = float(pair.get("trigger_pct") or 0)
            s_pct = float(pair.get("trigger_pct_sell") or 0) or b_pct
            nxt_buy = (start if rnd == 0 else ref * (1 - b_pct / 100)) if ref > 0 else None
            nxt_sell = ref * (1 + (s_pct + 0.08) / 100) if rnd > 0 and ref > 0 else None

        p = pnl.get(sym)
        day_pnl = float(p["realized_pnl"]) if p else 0.0
        day_cnt = int(p["trade_count"]) if p else 0
        total_pnl += day_pnl
        total_cnt += day_cnt
        lf = last_fill.get(sym)
        row = [
            sym, status, f"{rnd}/{pair.get('max_rounds', '?')}",
            _num(prices.get(ticker)), _num(ref),
            (approx + _num(nxt_buy)) if nxt_buy else "-",
            (approx + _num(nxt_sell)) if nxt_sell else "-",
            str(opens.get(sym, 0)),
            f"{day_pnl:+,.0f}({day_cnt})",
            lf[5:16] if lf else "-",
        ]
        print("  ".join(_pad(v, w, right=i not in (0, 1)) for i, (v, w) in enumerate(zip(row, widths))))

    print("─" * (sum(widths) + 2 * (len(widths) - 1)))
    print(f"가동 {running}/{len(order)}종목 · 오늘 실현손익 {total_pnl:+,.0f}원 ({total_cnt}건)"
          + ("" if prices else " · 현재가 조회 실패"))
    if any(not by_sym[s]["is_running"] and has_desired and by_sym[s]["desired_running"] for s in order):
        print("※ 중지* = 사용자가 켜둔 종목 (서버가 다시 뜨면 자동 재가동 대상)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
