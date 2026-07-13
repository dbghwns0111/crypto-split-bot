"""REST API 엔드포인트."""

import time

from flask import Blueprint, current_app, jsonify, request

from portfolio.balance import take_snapshot
from utils.logger import get_logger

_MARKET_COINS = [
    "BTC", "ETH", "XRP", "USDT",
]

_log    = get_logger("web.api")
api_bp  = Blueprint("api", __name__, url_prefix="/api")

# 가격 캐시: ticker → (price, monotonic_ts)
_price_cache: dict[str, tuple[float, float]] = {}
_PRICE_TTL = 4.0


def _get_price_cached(ticker: str, exchange) -> float:
    now   = time.monotonic()
    entry = _price_cache.get(ticker)
    if entry:
        price, ts = entry
        if now - ts < _PRICE_TTL:
            return price
    price = exchange.get_current_price(ticker)
    _price_cache[ticker] = (price, now)
    return price


def clear_price_cache() -> None:
    _price_cache.clear()


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@api_bp.route("/status")
def get_status():
    """모든 봇 상태 + 현재가 + 오늘 손익 조회."""
    from database.pnl_repo import get_daily_pnl

    bot_manager = current_app.config["BOT_MANAGER"]
    exchange    = current_app.config.get("EXCHANGE")

    result = {}
    for sym, status in bot_manager.get_all_status().items():
        ticker = status.get("ticker", "")
        if exchange and ticker:
            try:
                status["current_price"] = _get_price_cached(ticker, exchange)
            except Exception:
                status["current_price"] = 0
        else:
            status["current_price"] = 0

        pnl = get_daily_pnl(sym) or {}
        status["today_pnl"]    = float(pnl.get("realized_pnl", 0))
        status["today_trades"] = int(pnl.get("trade_count", 0))
        status["today_fee"]    = float(pnl.get("fee_total", 0))
        status["today_point"]  = float(pnl.get("point_total", 0))

        result[sym] = status

    return jsonify(result)


@api_bp.route("/bot/start", methods=["POST"])
def start_bot():
    """봇 시작. fresh=true이면 기존 상태를 초기화하고 처음부터 시작한다.
    virtual_catchup=true이면 갭 구간을 가상 체결로 채운다.
    """
    data            = request.get_json(silent=True) or {}
    symbol          = (data.get("symbol") or "").strip().upper()
    fresh           = bool(data.get("fresh", False))
    virtual_catchup = bool(data.get("virtual_catchup", False))

    if not symbol:
        return jsonify({"ok": False, "message": "symbol 파라미터가 필요합니다."}), 400

    bot_manager = current_app.config["BOT_MANAGER"]
    ok, msg     = bot_manager.start(symbol, fresh=fresh, virtual_catchup=virtual_catchup)
    return jsonify({"ok": ok, "message": msg})


@api_bp.route("/bot/gap")
def get_bot_gap():
    """재시작/신규 시작 전 가격 갭 미리보기.

    신규 시작·재시작 모두 전략 시작가(start_price) 기준 그리드로 통일 계산.
    gap = 시작가 기준 예상 총 차수 − 현재 차수

    Query params:
        symbol: 심볼 (ETH, USDT, ...)
    """
    import math as _math
    from database.state_repo import get_state
    from utils.math_utils import calc_quantity, round_price

    symbol = request.args.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    exchange = current_app.config.get("EXCHANGE")
    cfg      = current_app.config.get("CFG", {})

    state         = get_state(symbol) or {}
    start_price   = float(state.get("start_price") or 0)
    current_round = int(state.get("current_round") or 0)
    is_fresh_start = (current_round == 0)

    _empty = {
        "gap": 0, "filled": 0, "levels": [], "is_fresh_start": is_fresh_start,
        "current_round": current_round, "start_price": 0, "current_price": 0,
    }
    if start_price <= 0:
        return jsonify(_empty)

    pair         = cfg.get("pairs", {}).get(symbol, {})
    trigger_mode = pair.get("trigger_mode", "pct")
    trigger_pct  = float(pair.get("trigger_pct", 0.5))
    trigger_krw  = float(pair.get("trigger_krw", 10000))
    order_amount = float(pair.get("order_amount", 50000))
    max_rounds   = int(pair.get("max_rounds", 50))

    current_price = 0.0
    if exchange:
        try:
            current_price = exchange.get_current_price(f"KRW-{symbol}")
        except Exception as exc:
            _log.warning("갭 미리보기 현재가 조회 실패 (%s): %s", symbol, exc)

    if current_price <= 0:
        _empty["start_price"] = round(start_price)
        return jsonify(_empty)

    # 시작가 기준 예상 총 차수 (1차 = 시작가 레벨 자체)
    if current_price >= start_price:
        expected_total = 0
    elif trigger_mode == "krw":
        expected_total = 1 + int((start_price - current_price) // trigger_krw)
    else:
        n = _math.log(current_price / start_price) / _math.log(1 - trigger_pct / 100)
        expected_total = 1 + int(n)

    gap = max(0, expected_total - current_round)

    if gap <= 0:
        return jsonify({**_empty,
                        "start_price":   round(start_price),
                        "current_price": round(current_price)})

    room   = max_rounds - current_round
    filled = min(gap, room)

    # 시작가 기준 그리드 레벨 생성: n차 가격 = start_price - (n-1) * trigger
    levels = []
    for i in range(filled):
        n = current_round + i + 1
        if trigger_mode == "krw":
            gp = start_price - (n - 1) * trigger_krw
        else:
            gp = start_price * ((1 - trigger_pct / 100) ** (n - 1))
        grid_price = float(round_price(gp))
        quantity   = calc_quantity(order_amount, grid_price)
        levels.append({
            "round_no":   n,
            "grid_price": round(grid_price),
            "quantity":   quantity,
            "amount":     round(grid_price * quantity),
        })

    return jsonify({
        "gap":           gap,
        "filled":        filled,
        "is_fresh_start": is_fresh_start,
        "current_round": current_round,
        "start_price":   round(start_price),
        "current_price": round(current_price),
        "levels":        levels,
    })


@api_bp.route("/bot/stop", methods=["POST"])
def stop_bot():
    """봇 중지."""
    data   = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()

    if not symbol:
        return jsonify({"ok": False, "message": "symbol 파라미터가 필요합니다."}), 400

    bot_manager = current_app.config["BOT_MANAGER"]
    ok, msg     = bot_manager.stop(symbol)
    return jsonify({"ok": ok, "message": msg})


@api_bp.route("/bot/reset", methods=["POST"])
def reset_bot():
    """전략 상태 초기화 (봇 시작 없이 차수/기준가만 리셋)."""
    data   = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()

    if not symbol:
        return jsonify({"ok": False, "message": "symbol 파라미터가 필요합니다."}), 400

    bot_manager = current_app.config["BOT_MANAGER"]
    ok, msg     = bot_manager.reset(symbol)
    return jsonify({"ok": ok, "message": msg})


@api_bp.route("/bot/stop_all", methods=["POST"])
def stop_all_bots():
    """전체 제어 ON(bulk_enabled)인 봇 전체 중지. OFF 종목은 제외."""
    from web.auth import reauth_ok

    body = request.get_json(silent=True) or {}
    if not reauth_ok(body.get("_reauth_password")):
        return jsonify({"ok": False, "error": "reauth_required", "message": "비밀번호를 다시 확인해주세요."}), 403

    bot_manager = current_app.config["BOT_MANAGER"]
    stopped     = bot_manager.stop_all()
    return jsonify({"ok": True, "stopped": stopped, "count": len(stopped)})


@api_bp.route("/bot/start_all", methods=["POST"])
def start_all_bots():
    """전체 제어 ON(bulk_enabled)인 봇 전체 재시작(이전 상태 복원). OFF 종목은 제외."""
    from web.auth import reauth_ok

    body = request.get_json(silent=True) or {}
    if not reauth_ok(body.get("_reauth_password")):
        return jsonify({"ok": False, "error": "reauth_required", "message": "비밀번호를 다시 확인해주세요."}), 403

    bot_manager = current_app.config["BOT_MANAGER"]
    resumed, failed, skipped = bot_manager.start_all(fresh=False)
    return jsonify({
        "ok": True, "resumed": resumed, "failed": failed, "skipped": skipped,
        "count": len(resumed),
    })


@api_bp.route("/bot/bulk_enabled", methods=["POST"])
def set_bulk_enabled():
    """종목별 전체 제어 ON/OFF 토글. settings.yaml에 영구 저장 + 메모리 즉시 반영."""
    import os
    import yaml

    body    = request.get_json(silent=True) or {}
    symbol  = (body.get("symbol") or "").upper().strip()
    enabled = bool(body.get("enabled"))

    bot_manager = current_app.config["BOT_MANAGER"]
    if not bot_manager.set_bulk_enabled(symbol, enabled):
        return jsonify({"ok": False, "message": f"알 수 없는 심볼: {symbol}"}), 400

    try:
        yaml_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "config", "settings.yaml")
        )
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw.setdefault("pairs", {}).setdefault(symbol, {})["bulk_enabled"] = enabled
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        _log.error("bulk_enabled 저장 오류: %s", exc)
        return jsonify({"ok": True, "message": f"{symbol} 전체제어 {'ON' if enabled else 'OFF'} (메모리만, 저장실패)"})

    _log.info("[전체제어] %s = %s", symbol, "ON" if enabled else "OFF")
    return jsonify({"ok": True, "message": f"{symbol} 전체제어 {'ON' if enabled else 'OFF'}"})


@api_bp.route("/pnl/today")
def get_today_pnl():
    """오늘 손익 + 오늘 매수/매도 총액 조회 (홈 KPI 스트립)."""
    from database.order_repo import get_today_totals
    from database.pnl_repo   import get_daily_summary
    from utils.time_utils    import today_kst_str

    bot_manager = current_app.config["BOT_MANAGER"]
    date        = today_kst_str()
    summary     = get_daily_summary(date)
    totals      = get_today_totals(list(bot_manager.symbols), date)
    net_pnl     = sum(
        float(s.get("realized_pnl", 0) or 0) - float(s.get("fee_total", 0) or 0)
        for s in summary
    )

    return jsonify({
        "date":       date,
        "symbols":    summary,
        "total_buy":  round(totals["total_buy"]),
        "total_sell": round(totals["total_sell"]),
        "net_pnl":    round(net_pnl),
    })


@api_bp.route("/portfolio")
def get_portfolio():
    """포트폴리오 잔고 조회."""
    exchange    = current_app.config.get("EXCHANGE")
    bot_manager = current_app.config["BOT_MANAGER"]

    if not exchange:
        return jsonify({"error": "Exchange not configured"}), 503

    try:
        snap = take_snapshot(exchange, bot_manager.symbols)
        return jsonify(snap)
    except Exception as exc:
        _log.error("Portfolio snapshot 오류: %s", exc)
        return jsonify({"error": str(exc)}), 500


@api_bp.route("/market/prices")
def get_market_prices():
    """주요 코인 현재가 조회 (4초 캐시)."""
    exchange = current_app.config.get("EXCHANGE")
    if not exchange:
        return jsonify({"error": "Exchange not configured"}), 503

    result = {}
    for symbol in _MARKET_COINS:
        ticker = f"KRW-{symbol}"
        try:
            price = _get_price_cached(ticker, exchange)
            result[symbol] = {"symbol": symbol, "price": price}
        except Exception as exc:
            _log.warning("시세 조회 실패 (%s): %s", symbol, exc)
            result[symbol] = {"symbol": symbol, "price": None}

    return jsonify(result)


@api_bp.route("/investment")
def get_investment():
    """투자현황 탭: 심볼별 현재 포지션 평가 정보."""
    from database.order_repo import get_position_cost

    bot_manager = current_app.config["BOT_MANAGER"]
    exchange    = current_app.config.get("EXCHANGE")
    cfg         = current_app.config.get("CFG", {})

    all_status = bot_manager.get_all_status()
    symbols    = list(all_status.keys())

    # 포트폴리오 잔고 (가용 + 잠금 합산 — 매도주문에 묶인 코인도 포함)
    portfolio = {}
    if exchange:
        try:
            for b in exchange.get_balances():
                cur = b.get("currency", "")
                if cur:
                    portfolio[cur] = float(b.get("balance", 0) or 0) + float(b.get("locked", 0) or 0)
        except Exception as exc:
            _log.warning("포트폴리오 조회 오류: %s", exc)

    summary_buy  = 0.0
    summary_eval = 0.0
    active_count = 0
    rows = []

    for sym, st in all_status.items():
        ticker = st.get("ticker", f"KRW-{sym}")

        # 현재 포지션 순 투입금
        position_cost = get_position_cost(sym)

        # 현재가 및 평가금액
        current_price = 0.0
        if exchange and ticker:
            try:
                current_price = _get_price_cached(ticker, exchange)
            except Exception:
                pass

        qty      = float(portfolio.get(sym, 0) or 0)
        eval_amt = qty * current_price
        pnl      = eval_amt - position_cost
        pct      = (pnl / position_cost * 100) if position_cost > 0 else 0.0

        # max_splits 설정값
        sym_lower  = sym.lower()
        max_splits = (
            cfg.get(sym_lower, {}).get("max_splits")
            or cfg.get("eth", {}).get("max_splits", 50)
        )

        # 매매 이력이 없는 종목은 제외
        if position_cost <= 0 and st.get("current_round", 0) <= 0:
            continue

        if st.get("running"):
            active_count += 1

        summary_buy  += position_cost
        summary_eval += eval_amt

        rows.append({
            "symbol":        sym,
            "running":       st.get("running", False),
            "current_round": st.get("current_round", 0),
            "max_splits":    max_splits,
            "total_buy":     round(position_cost),
            "total_eval":    round(eval_amt),
            "unrealized_pnl": round(pnl),
            "pct":           round(pct, 2),
        })

    summary_pnl = summary_eval - summary_buy
    summary_pct = (summary_pnl / summary_buy * 100) if summary_buy > 0 else 0.0

    return jsonify({
        "summary": {
            "total_buy":      round(summary_buy),
            "total_eval":     round(summary_eval),
            "unrealized_pnl": round(summary_pnl),
            "pct":            round(summary_pct, 2),
            "active_count":   active_count,
            "total_count":    len(rows),
        },
        "symbols": rows,
    })


def _resolve_days(days: int, symbols: list) -> int:
    """days>0 이면 [1,365]로 클램프, days<=0(전체)이면 최초 체결일 기준 일수로 환산."""
    if days > 0:
        return max(1, min(days, 365))
    from datetime import datetime

    from database.order_repo import get_first_filled_date
    from utils.time_utils import now_kst

    first = get_first_filled_date(symbols)
    if not first:
        return 1
    try:
        fd = datetime.strptime(first, "%Y-%m-%d").date()
        return max(1, min((now_kst().date() - fd).days + 1, 365))
    except Exception:
        return 1


@api_bp.route("/pnl/cumulative")
def get_cumulative_pnl():
    """누적손익 탭: 기간별 심볼별 매수/매도 합계. ?days=0(전체) 기본."""
    from datetime import timedelta

    from database.order_repo import get_cumulative_totals
    from utils.time_utils import now_kst

    # 기간 파싱: days<=0 = 전체(all-time, 날짜 필터 없음)
    try:
        days = int(request.args.get("days", 0))
    except (TypeError, ValueError):
        days = 0

    start = end = None
    if days > 0:
        days  = min(days, 3650)
        today = now_kst().date()
        start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        end   = today.strftime("%Y-%m-%d")

    rows = get_cumulative_totals(start, end)

    sym_filter = (request.args.get("symbol") or "").upper().strip()
    if sym_filter:
        rows = [r for r in rows if r.get("symbol") == sym_filter]

    grand_buy  = 0.0
    grand_sell = 0.0
    symbols    = []

    for r in rows:
        buy  = float(r.get("total_buy",  0) or 0)
        sell = float(r.get("total_sell", 0) or 0)
        pnl  = sell - buy
        pct  = (pnl / buy * 100) if buy > 0 else 0.0

        grand_buy  += buy
        grand_sell += sell

        symbols.append({
            "symbol":       r["symbol"],
            "total_buy":    round(buy),
            "total_sell":   round(sell),
            "realized_pnl": round(pnl),
            "pct":          round(pct, 2),
            "buy_count":    r.get("buy_count", 0),
            "sell_count":   r.get("sell_count", 0),
        })

    grand_pnl = grand_sell - grand_buy
    grand_pct = (grand_pnl / grand_buy * 100) if grand_buy > 0 else 0.0

    return jsonify({
        "summary": {
            "total_buy":    round(grand_buy),
            "total_sell":   round(grand_sell),
            "realized_pnl": round(grand_pnl),
            "pct":          round(grand_pct, 2),
        },
        "symbols": symbols,
    })


@api_bp.route("/pnl/daily")
def get_pnl_daily():
    """일별 손익 시계열 (차트용). ?days=30 기본, 누락 날짜는 0으로 채움."""
    from datetime import timedelta

    from database.pnl_repo import get_period_pnl
    from utils.time_utils import now_kst

    bot_manager = current_app.config["BOT_MANAGER"]
    symbols     = list(bot_manager.symbols)
    sym_filter  = (request.args.get("symbol") or "").upper().strip()
    if sym_filter and sym_filter in symbols:
        symbols = [sym_filter]

    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = _resolve_days(days, symbols)

    today      = now_kst().date()
    start_date = today - timedelta(days=days - 1)
    labels     = [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    label_idx  = {d: i for i, d in enumerate(labels)}

    total: list[float]            = [0.0] * days
    by_symbol: dict[str, list]    = {}
    for sym in symbols:
        series = [0.0] * days
        for row in get_period_pnl(sym, labels[0], labels[-1]):
            i = label_idx.get(row.get("date"))
            if i is not None:
                val       = float(row.get("realized_pnl", 0) or 0)
                series[i] = round(val)
                total[i] += val
        by_symbol[sym] = series

    total = [round(t) for t in total]
    cumulative: list[float] = []
    running = 0.0
    for t in total:
        running += t
        cumulative.append(round(running))

    return jsonify({
        "labels":     labels,
        "total":      total,
        "cumulative": cumulative,
        "by_symbol":  by_symbol,
    })


@api_bp.route("/pnl/hourly")
def get_pnl_hourly():
    """시간대별(0-23시) 평균손익 통계 (기간 합산을 일수로 나눈 값). ?days=30 기본."""
    from datetime import timedelta

    from database.order_repo import get_hourly_pnl
    from utils.time_utils import now_kst

    bot_manager = current_app.config["BOT_MANAGER"]
    symbols     = list(bot_manager.symbols)
    sym_filter  = (request.args.get("symbol") or "").upper().strip()
    if sym_filter and sym_filter in symbols:
        symbols = [sym_filter]

    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = _resolve_days(days, symbols)

    today = now_kst().date()
    start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")

    rows = get_hourly_pnl(symbols, start, end)
    # 시간대별 평균손익: 기간 내 매일 각 시간대가 1회씩 존재하므로 days로 나눈다.
    avg_pnl = [round(r["realized_pnl"] / days) for r in rows]
    return jsonify({
        "days":         days,
        "hours":        [r["hour"] for r in rows],
        "trade_counts": [r["trade_count"] for r in rows],
        "avg_pnl":      avg_pnl,
    })


@api_bp.route("/pnl/weekday")
def get_pnl_weekday():
    """요일별(월-일) 평균손익 통계 (기간 합산을 해당 요일 등장 횟수로 나눈 값). ?days=30 기본."""
    from datetime import timedelta

    from database.order_repo import get_weekday_pnl
    from utils.time_utils import now_kst

    bot_manager = current_app.config["BOT_MANAGER"]
    symbols     = list(bot_manager.symbols)
    sym_filter  = (request.args.get("symbol") or "").upper().strip()
    if sym_filter and sym_filter in symbols:
        symbols = [sym_filter]

    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = _resolve_days(days, symbols)

    today = now_kst().date()
    start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")

    rows = get_weekday_pnl(symbols, start, end)

    # 요일별 평균손익: 기간 내 해당 요일이 등장한 횟수로 나눈다 (0=월 ... 6=일, date.weekday()와 동일).
    from datetime import datetime as _dt
    s_date = _dt.strptime(start, "%Y-%m-%d").date()
    e_date = _dt.strptime(end, "%Y-%m-%d").date()
    occurrence = [0] * 7
    d = s_date
    while d <= e_date:
        occurrence[d.weekday()] += 1
        d += timedelta(days=1)

    avg_pnl = [
        round(r["realized_pnl"] / occurrence[r["weekday"]]) if occurrence[r["weekday"]] > 0 else 0
        for r in rows
    ]

    return jsonify({
        "days":         days,
        "weekdays":     [r["weekday"] for r in rows],
        "trade_counts": [r["trade_count"] for r in rows],
        "avg_pnl":      avg_pnl,
    })


@api_bp.route("/pnl/stats")
def get_pnl_stats():
    """성과 통계 요약 (승률/프로핏팩터/평균손익 등). ?days=30 기본."""
    from datetime import timedelta

    from database.order_repo import get_pnl_rounds
    from utils.time_utils import now_kst

    bot_manager = current_app.config["BOT_MANAGER"]
    symbols     = list(bot_manager.symbols)
    sym_filter  = (request.args.get("symbol") or "").upper().strip()
    if sym_filter and sym_filter in symbols:
        symbols = [sym_filter]

    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = _resolve_days(days, symbols)

    today = now_kst().date()
    start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    end   = today.strftime("%Y-%m-%d")

    rounds = get_pnl_rounds(symbols, start, end, limit=100000)

    def _calc(rows: list[dict], period_days: int) -> dict:
        pnls = [float(r["pnl"]) for r in rows if r.get("pnl") is not None]
        if not pnls:
            return {
                "total_trades": 0, "avg_pnl": 0, "total_pnl": 0,
                "best_trade": 0, "worst_trade": 0,
                "daily_avg_pnl": 0, "daily_cycles": 0.0,
            }
        d = max(period_days, 1)
        return {
            "total_trades":  len(pnls),
            "avg_pnl":       round(sum(pnls) / len(pnls)),
            "total_pnl":     round(sum(pnls)),
            "best_trade":    round(max(pnls)),
            "worst_trade":   round(min(pnls)),
            "daily_avg_pnl": round(sum(pnls) / d),
            "daily_cycles":  round(len(pnls) / d, 1),
        }

    return jsonify({
        "days":      days,
        "overall":   _calc(rounds, days),
        "by_symbol": {sym: _calc([r for r in rounds if r.get("symbol") == sym], days) for sym in symbols},
    })


@api_bp.route("/assets/exchange")
def get_exchange_assets():
    """거래소자산 탭: 빗썸 보유 자산 현황."""

    exchange = current_app.config.get("EXCHANGE")
    if not exchange:
        return jsonify({"error": "Exchange not configured"}), 503

    try:
        balances = exchange.get_balances()
    except Exception as exc:
        _log.error("잔고 조회 오류: %s", exc)
        return jsonify({"error": str(exc)}), 500

    bal_map: dict[str, dict] = {}
    for b in balances:
        cur = b.get("currency", "").upper()
        if cur:
            bal_map[cur] = b

    krw_entry      = bal_map.get("KRW", {})
    krw_available  = float(krw_entry.get("balance", 0) or 0)
    krw_locked     = float(krw_entry.get("locked", 0) or 0)
    krw_total      = krw_available + krw_locked

    p_entry       = bal_map.get("P", {})
    bithumb_points = float(p_entry.get("balance", 0) or 0) + float(p_entry.get("locked", 0) or 0)

    coin_list = []
    total_coin_krw = 0.0

    try:
        from database.order_repo import get_avg_buy_price, get_manual_avg_buy_price

        for cur, b in bal_map.items():
            if cur in ("KRW", "P"):
                continue
            qty = float((b.get("balance") or 0)) + float((b.get("locked") or 0))
            if qty <= 0:
                continue

            ticker = f"KRW-{cur}"
            try:
                current_price = _get_price_cached(ticker, exchange)
            except Exception:
                current_price = 0.0

            try:
                bot_avg    = get_avg_buy_price(cur)
                manual_avg = get_manual_avg_buy_price(cur) if bot_avg == 0 else 0.0
            except Exception:
                bot_avg = manual_avg = 0.0

            avg_buy  = bot_avg if bot_avg > 0 else manual_avg
            cost     = avg_buy * qty if avg_buy > 0 else 0.0
            eval_amt = qty * current_price
            pnl      = eval_amt - cost if cost > 0 else 0.0
            pct      = (pnl / cost * 100) if cost > 0 else 0.0

            total_coin_krw += eval_amt

            coin_list.append({
                "symbol":           cur,
                "quantity":         qty,
                "avg_buy_price":    round(avg_buy),
                "manual_avg_price": round(manual_avg),
                "cost":             round(cost),
                "eval":             round(eval_amt),
                "unrealized_pnl":   round(pnl),
                "pct":              round(pct, 2),
                "editable":         bot_avg == 0,
            })
    except Exception as exc:
        _log.error("거래소자산 코인 목록 조회 오류: %s", exc)

    total_assets = krw_total + total_coin_krw

    return jsonify({
        "exchange":       "빗썸",
        "total_assets":   round(total_assets),
        "krw_total":      round(krw_total),
        "krw_available":  round(krw_available),
        "bithumb_points": round(bithumb_points),
        "coins":          coin_list,
    })


@api_bp.route("/strategy")
def get_strategy():
    """전략현황 탭: 심볼별 전략 파라미터 + 모니터링 데이터."""
    import os
    from datetime import datetime

    from database.order_repo import get_open_orders
    from database.pnl_repo import get_daily_pnl
    from database.state_repo import get_state
    from utils.math_utils import round_price

    bot_manager = current_app.config["BOT_MANAGER"]
    exchange    = current_app.config.get("EXCHANGE")
    cfg         = current_app.config.get("CFG", {})

    all_status   = bot_manager.get_all_status()
    symbols_data = []

    for sym, status in all_status.items():
        ticker = status.get("ticker", f"KRW-{sym}")

        state           = get_state(sym) or {}
        reference_price = float(state.get("reference_price") or 0)
        start_price     = float(state.get("start_price") or 0)
        current_round   = int(state.get("current_round") or 0)
        cb_on           = bool(state.get("circuit_breaker_on") or 0)
        cb_until_str    = state.get("cb_until")

        cb_seconds_left = 0
        if cb_on and cb_until_str:
            try:
                diff = (datetime.fromisoformat(cb_until_str) - datetime.now()).total_seconds()
                cb_seconds_left = max(0, int(diff))
            except Exception:
                pass

        current_price = 0.0
        if exchange and ticker:
            try:
                current_price = _get_price_cached(ticker, exchange)
            except Exception:
                pass

        pairs_cfg    = cfg.get("pairs", {})
        pair_cfg     = pairs_cfg.get(sym, cfg.get(sym.lower(), {}))
        trigger_mode = pair_cfg.get("trigger_mode", "pct")
        trigger_pct  = float(pair_cfg.get("trigger_pct", 0.5))
        trigger_krw  = float(pair_cfg.get("trigger_krw", 10000))
        max_rounds   = int(pair_cfg.get("max_rounds", 50))
        order_amount = int(pair_cfg.get("order_amount", 50000))

        ref = reference_price
        if current_round == 0:
            # 0차: 시작가(또는 기준가) 이하 도달 시 매수 — buy_trig = base 그대로
            base = start_price if start_price > 0 else ref
            if base > 0:
                buy_trig = round(base)
                if trigger_mode == "krw":
                    sell_trig = round(base + trigger_krw)
                else:
                    sell_trig = round(base * (1 + trigger_pct / 100))
            else:
                buy_trig = sell_trig = 0
        elif ref > 0:
            if trigger_mode == "krw":
                buy_trig  = round(ref - trigger_krw)
                sell_trig = round(ref + trigger_krw)
            else:
                buy_trig  = round(ref * (1 - trigger_pct / 100))
                sell_trig = round(ref * (1 + trigger_pct / 100))
        else:
            buy_trig = sell_trig = 0

        open_orders = []
        for o in get_open_orders(sym):
            elapsed = 0
            placed  = o.get("created_at", "")
            if placed:
                try:
                    elapsed = int((datetime.now() - datetime.fromisoformat(placed)).total_seconds())
                except Exception:
                    pass
            open_orders.append({
                "side":        o["side"],
                "price":       o["price"],
                "quantity":    o["quantity"],
                "elapsed_sec": elapsed,
            })

        pnl_data      = get_daily_pnl(sym) or {}
        today_net_pnl = float(pnl_data.get("realized_pnl", 0)) - float(pnl_data.get("fee_total", 0))

        symbols_data.append({
            "symbol":           sym,
            "ticker":           ticker,
            "running":          status.get("running", False),
            "bulk_enabled":     status.get("bulk_enabled", True),
            "current_price":    round(current_price),
            "reference_price":  round(reference_price),
            "start_price":      round(start_price),
            "current_round":    current_round,
            "max_rounds":       max_rounds,
            "order_amount":     order_amount,
            "trigger_mode":     trigger_mode,
            "trigger_pct":      trigger_pct,
            "trigger_krw":      trigger_krw,
            "buy_trigger_price":  buy_trig,
            "sell_trigger_price": sell_trig,
            "circuit_breaker":  cb_on,
            "cb_seconds_left":  cb_seconds_left,
            "open_orders":      open_orders,
            "today_net_pnl":    round(today_net_pnl),
            "today_trades":     int(pnl_data.get("trade_count", 0)),
            "today_point":      round(float(pnl_data.get("point_total", 0)), 2),
        })

    return jsonify({"symbols": symbols_data})


@api_bp.route("/trades/fills")
def get_trade_fills():
    """투자내역 - 주문체결 내역: 기간 필터링된 체결 주문 목록."""
    from database.order_repo import get_filled_orders_range
    from utils.time_utils import today_kst_str

    bot_manager = current_app.config["BOT_MANAGER"]
    symbols     = list(bot_manager.get_all_status().keys())

    today = today_kst_str()
    start = request.args.get("start", today)
    end   = request.args.get("end",   today)
    sym   = request.args.get("symbol", "").strip().upper() or None
    if sym == "ALL":
        sym = None

    rows = get_filled_orders_range(symbols, start, end, symbol_filter=sym, limit=100000)
    fills = []
    for o in rows:
        fills.append({
            "id":           o["id"],
            "symbol":       o["symbol"],
            "side":         o["side"],
            "filled_price": float(o.get("filled_price") or o.get("price") or 0),
            "quantity":     float(o.get("quantity") or 0),
            "round_no":     o.get("round_no", 0),
            "amount":       float(o.get("amount") or 0),
            "filled_at":    o.get("filled_at") or o.get("created_at", ""),
        })
    return jsonify({"fills": fills, "start": start, "end": end})


@api_bp.route("/trades/pnl")
def get_trade_pnl():
    """투자내역 - 실현손익 내역: 기간 필터링된 차수별 손익."""
    from database.order_repo import get_pnl_rounds
    from utils.time_utils import today_kst_str

    bot_manager = current_app.config["BOT_MANAGER"]
    symbols     = list(bot_manager.get_all_status().keys())

    today = today_kst_str()
    start = request.args.get("start", today)
    end   = request.args.get("end",   today)
    sym   = request.args.get("symbol", "").strip().upper() or None
    if sym == "ALL":
        sym = None

    rows = get_pnl_rounds(symbols, start, end, symbol_filter=sym, limit=100000)
    rounds = []
    for r in rows:
        rounds.append({
            "sell_id":     r.get("sell_id"),
            "symbol":      r["symbol"],
            "round_no":    r["round_no"],
            "buy_price":   float(r.get("buy_price")   or 0),
            "sell_price":  float(r.get("sell_price")  or 0),
            "quantity":    float(r.get("quantity")    or 0),
            "buy_amount":  float(r.get("buy_amount")  or 0),
            "sell_amount": float(r.get("sell_amount") or 0),
            "buy_is_virtual": bool(r.get("buy_is_virtual")),
            "pnl":         r.get("pnl"),
            "filled_at":   r.get("filled_at", ""),
        })
    return jsonify({"rounds": rounds, "start": start, "end": end})


@api_bp.route("/strategy/settings", methods=["POST"])
def save_strategy_settings():
    """전략 설정을 저장하고 메모리 cfg를 즉시 반영한다."""
    import os
    import yaml

    body   = request.get_json(silent=True) or {}
    symbol = (body.get("symbol") or "").upper().strip()

    if not symbol:
        return jsonify({"ok": False, "message": "symbol 파라미터가 필요합니다."}), 400

    cfg   = current_app.config.get("CFG", {})
    pairs = cfg.get("pairs", {})

    if symbol not in pairs:
        return jsonify({"ok": False, "message": f"알 수 없는 심볼: {symbol}"}), 400

    pair = pairs[symbol]
    allowed = {
        "trigger_mode": str,
        "trigger_pct":  float,
        "trigger_krw":  float,
        "max_rounds":   int,
        "order_amount": int,
    }
    for key, cast in allowed.items():
        if key in body:
            pair[key] = cast(body[key])

    try:
        yaml_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "config", "settings.yaml")
        )
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        raw_pair = raw.get("pairs", {}).get(symbol, {})
        for key in allowed:
            if key in body:
                raw_pair[key] = pair[key]
        raw["pairs"][symbol] = raw_pair

        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # 시작가가 전달된 경우 저장 — 가동 중이면 BotManager를 통해 살아있는
        # 인스턴스에 반영한다(차수 0이면 즉시, 차수>0이면 다음 0차 복귀 시
        # 적용되도록 DB에만 예약). 중지 상태면 기존처럼 DB에 직접 저장한다.
        start_price_note = None
        start_price_raw = body.get("start_price")
        if start_price_raw is not None:
            sp = float(start_price_raw)
            if sp > 0:
                bot_manager = current_app.config.get("BOT_MANAGER")
                if bot_manager and bot_manager.is_running(symbol):
                    _, start_price_note = bot_manager.update_start_price(symbol, sp)
                else:
                    from database.state_repo import set_start_price, set_reference_price
                    set_start_price(symbol, sp)
                    # 봇이 중지 상태일 때만 기준가도 함께 업데이트 (모니터링 카드 반영)
                    set_reference_price(symbol, sp)

        _log.info("[설정변경] %s 전략 설정 저장: %s", symbol,
                  {k: body[k] for k in allowed if k in body})
        return jsonify({"ok": True, "message": start_price_note or "저장 완료"})
    except Exception as exc:
        _log.error("전략 설정 저장 오류: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500


@api_bp.route("/strategy/planner")
def get_strategy_planner():
    """그리드 플래너: 차수별 매수가·수량·매도가·순수익 + 자본/소진가/예상수익 요약.

    실제 봇과 동일한 순수 함수(trigger/math_utils)로 계산해 매매 결과와 일치시킨다.
    매매 알고리즘은 변경하지 않고 읽기만 한다.
    """
    from strategy.trigger import (
        buy_order_price,
        buy_order_price_krw,
        sell_order_price,
        sell_order_price_krw,
    )
    from utils.math_utils import (
        calc_pnl,
        calc_quantity,
        get_point_rate,
        round_price,
    )

    _MAX_ROUNDS_GUARD = 500

    cfg = current_app.config.get("CFG", {})

    def _farg(name: str, default: float = 0.0) -> float:
        try:
            return float(request.args.get(name, default))
        except (TypeError, ValueError):
            return default

    symbol       = (request.args.get("symbol") or "").upper().strip()
    trigger_mode = (request.args.get("trigger_mode") or "pct").lower()
    trigger_krw  = _farg("trigger_krw")
    trigger_pct  = _farg("trigger_pct")
    order_amount = _farg("order_amount")
    max_rounds   = int(_farg("max_rounds"))
    start_price  = _farg("start_price")

    # 시작가 미입력 시 현재가로 폴백
    if start_price <= 0 and symbol:
        exchange = current_app.config.get("EXCHANGE")
        pair_cfg = cfg.get("pairs", {}).get(symbol, {})
        ticker   = pair_cfg.get("ticker", f"KRW-{symbol}")
        if exchange and ticker:
            try:
                start_price = float(_get_price_cached(ticker, exchange))
            except Exception:
                start_price = 0.0

    empty = {"summary": {"required_capital": 0, "exhaust_price": 0,
                         "exhaust_drop_pct": 0, "total_net": 0, "avg_net": 0, "rounds": 0},
             "rows": []}
    if start_price <= 0 or order_amount <= 0 or max_rounds <= 0:
        return jsonify(empty)

    if trigger_mode == "krw" and trigger_krw <= 0:
        return jsonify(empty)
    if trigger_mode != "krw" and trigger_pct <= 0:
        return jsonify(empty)

    rounds      = min(max_rounds, _MAX_ROUNDS_GUARD)
    sell_extra  = float(cfg.get("strategy", {}).get("sell_extra_pct", 0.08))
    membership  = cfg.get("exchange", {}).get("membership", "화이트")
    fee_rate    = 0.04
    point_rate  = get_point_rate(membership)

    rows: list[dict] = []
    required_capital = 0.0
    total_net        = 0.0
    ref = round_price(start_price)  # 1차 매수 = 시작가(반올림)

    for n in range(1, rounds + 1):
        buy = ref if n == 1 else (
            round_price(buy_order_price_krw(ref, trigger_krw)) if trigger_mode == "krw"
            else round_price(buy_order_price(ref, trigger_pct))
        )
        if buy <= 0:
            break
        sell = (round_price(sell_order_price_krw(buy, trigger_krw)) if trigger_mode == "krw"
                else round_price(sell_order_price(buy, trigger_pct, sell_extra)))
        qty  = calc_quantity(order_amount, buy)
        net  = calc_pnl(buy, sell, qty, fee_rate, point_rate)["net"]
        drop = (buy - start_price) / start_price * 100.0

        rows.append({
            "round":      n,
            "buy_price":  buy,
            "quantity":   qty,
            "sell_price": sell,
            "drop_pct":   round(drop, 2),
            "net":        round(net),
        })
        required_capital += buy * qty
        total_net        += net
        ref = buy  # 다음 차수 기준가 = 직전 매수가(반올림값)

    exhaust_price = rows[-1]["buy_price"] if rows else 0
    exhaust_drop  = rows[-1]["drop_pct"] if rows else 0
    n_rows        = len(rows)

    return jsonify({
        "summary": {
            "required_capital": round(required_capital),
            "exhaust_price":    exhaust_price,
            "exhaust_drop_pct": exhaust_drop,
            "total_net":        round(total_net),
            "avg_net":          round(total_net / n_rows) if n_rows else 0,
            "rounds":           n_rows,
        },
        "rows": rows,
    })


def _get_local_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@api_bp.route("/settings")
def get_settings():
    """설정 탭: 현재 설정값 반환 (API 키는 마스킹)."""
    cfg = current_app.config.get("CFG", {})

    def _mask(val: str, show: int = 4) -> str:
        if not val:
            return ""
        return val[:show] + "****" if len(val) > show else "****"

    access_key = cfg.get("exchange", {}).get("access_key", "")
    secret_key = cfg.get("exchange", {}).get("secret_key", "")
    bot_token  = cfg.get("telegram", {}).get("bot_token", "")
    chat_id    = cfg.get("telegram", {}).get("chat_id", "")

    port = cfg.get("web", {}).get("port", 5000)

    membership = cfg.get("exchange", {}).get("membership", "화이트")
    auth_cfg = (cfg.get("web", {}) or {}).get("auth", {}) or {}
    return jsonify({
        "server": {
            "ip":   _get_local_ip(),
            "port": port,
        },
        "security": {
            "enabled":      bool(auth_cfg.get("enabled", True)),
            "has_password": bool(auth_cfg.get("password", "")),
            "ip_whitelist": auth_cfg.get("ip_whitelist", []) or [],
        },
        "exchange": {
            "access_key": _mask(access_key, 4),
            "secret_key": "****" if secret_key else "",
            "membership": membership,
        },
        "telegram": {
            "bot_token": _mask(bot_token, 6),
            "chat_id":   chat_id,
        },
        "strategy": {
            "order_timeout":       cfg.get("strategy", {}).get("order_timeout", 300),
            "price_poll_interval": cfg.get("strategy", {}).get("price_poll_interval", 1),
        },
        "report": {
            "hour":                  cfg.get("report", {}).get("hour", 0),
            "minute":                cfg.get("report", {}).get("minute", 0),
            "hourly_interval_hours": cfg.get("report", {}).get("hourly_interval_hours", 1),
        },
        "system": {
            "time_sync_interval_hours": cfg.get("system", {}).get("time_sync_interval_hours", 6),
        },
    })


@api_bp.route("/settings", methods=["POST"])
def save_settings():
    """설정 탭: 설정값 저장. .env 항목은 파일 반영 + 앱 재시작 안내."""
    import os
    import yaml

    body = request.get_json(silent=True) or {}
    cfg  = current_app.config.get("CFG", {})

    root      = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env_path  = os.path.join(root, ".env")
    yaml_path = os.path.join(root, "config", "settings.yaml")

    needs_restart = False

    # ── .env 항목 (API 키 / 텔레그램 token+chat_id) ────────────────────────────
    env_fields = {
        "access_key": "BITHUMB_ACCESS_KEY",
        "secret_key": "BITHUMB_SECRET_KEY",
        "bot_token":  "TELEGRAM_BOT_TOKEN",
        "chat_id":    "TELEGRAM_CHAT_ID",
    }
    cfg_targets = {
        "access_key": ("exchange", "access_key"),
        "secret_key": ("exchange", "secret_key"),
        "bot_token":  ("telegram", "bot_token"),
        "chat_id":    ("telegram", "chat_id"),
    }
    try:
        from dotenv import set_key as _dotenv_set_key
        for field, env_key in env_fields.items():
            val = (body.get(field) or "").strip()
            if not val:
                continue
            _dotenv_set_key(env_path, env_key, val)
            os.environ[env_key] = val
            section, attr = cfg_targets[field]
            cfg.setdefault(section, {})[attr] = val
            needs_restart = True
    except Exception as exc:
        _log.error("env 저장 오류: %s", exc)
        return jsonify({"ok": False, "message": f"env 저장 실패: {exc}"}), 500

    # ── 웹 접근 비밀번호 (.env WEB_PASSWORD) — 재시작 없이 즉시 적용 ──────────────
    if "web_password" in body:
        try:
            from dotenv import set_key as _dotenv_set_key
            pwd = (body.get("web_password") or "").strip()
            _dotenv_set_key(env_path, "WEB_PASSWORD", pwd)
            os.environ["WEB_PASSWORD"] = pwd
            cfg.setdefault("web", {}).setdefault("auth", {})["password"] = pwd
            _log.info("[설정변경] 웹 접근 비밀번호 %s", "설정" if pwd else "해제")
        except Exception as exc:
            _log.error("웹 비밀번호 저장 오류: %s", exc)
            return jsonify({"ok": False, "message": f"비밀번호 저장 실패: {exc}"}), 500

    # ── settings.yaml 항목 ────────────────────────────────────────────────────
    yaml_fields = {
        "order_timeout":            ("strategy", "order_timeout",            int),
        "price_poll_interval":      ("strategy", "price_poll_interval",      int),
        "report_hour":              ("report",   "hour",                     int),
        "report_minute":            ("report",   "minute",                   int),
        "hourly_interval_hours":    ("report",   "hourly_interval_hours",    int),
        "time_sync_interval_hours": ("system",   "time_sync_interval_hours", int),
        "membership":               ("exchange", "membership",               str),
    }

    for field in ("hourly_interval_hours", "time_sync_interval_hours"):
        if body.get(field) is not None:
            try:
                h = int(body[field])
            except (TypeError, ValueError):
                return jsonify({"ok": False, "message": "간격은 숫자(시간)여야 합니다."}), 400
            if not (1 <= h <= 23):
                return jsonify({"ok": False, "message": "간격은 1~23시간 사이여야 합니다."}), 400

    yaml_changed = any(body.get(k) is not None for k in yaml_fields)
    if yaml_changed:
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            for field, (section, key, cast) in yaml_fields.items():
                val = body.get(field)
                if val is None:
                    continue
                val = cast(val)
                raw.setdefault(section, {})[key] = val
                cfg.setdefault(section, {})[key]  = val

            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
        except Exception as exc:
            _log.error("yaml 저장 오류: %s", exc)
            return jsonify({"ok": False, "message": f"yaml 저장 실패: {exc}"}), 500

        # 정각 알림/시계 동기화 간격은 스케줄러에 즉시 반영 (재시작 불필요)
        sched = current_app.config.get("SCHEDULER")
        if sched:
            try:
                if body.get("hourly_interval_hours") is not None:
                    sched.reschedule_hourly_status(cfg["report"]["hourly_interval_hours"])
                if body.get("time_sync_interval_hours") is not None:
                    sched.reschedule_time_sync(cfg["system"]["time_sync_interval_hours"])
            except Exception as exc:
                _log.warning("스케줄 갱신 실패: %s", exc)

    # ── 웹 보안 설정 (web.auth.ip_whitelist / enabled) — 즉시 적용 ──────────────
    if ("ip_whitelist" in body) or ("auth_enabled" in body):
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            raw.setdefault("web", {}).setdefault("auth", {})
            cfg.setdefault("web", {}).setdefault("auth", {})

            if "ip_whitelist" in body:
                import ipaddress

                wl_raw = body.get("ip_whitelist") or []
                if isinstance(wl_raw, str):
                    wl_raw = [p for p in wl_raw.replace("\n", ",").split(",")]
                wl = [str(ip).strip() for ip in wl_raw if str(ip).strip()]

                invalid = []
                for entry in wl:
                    try:
                        ipaddress.ip_network(entry, strict=False)
                    except ValueError:
                        invalid.append(entry)
                if invalid:
                    return jsonify({
                        "ok": False,
                        "message": f"잘못된 IP/CIDR 형식: {', '.join(invalid)}",
                    }), 400

                raw["web"]["auth"]["ip_whitelist"] = wl
                cfg["web"]["auth"]["ip_whitelist"] = wl

            if "auth_enabled" in body:
                en = bool(body.get("auth_enabled"))
                raw["web"]["auth"]["enabled"] = en
                cfg["web"]["auth"]["enabled"] = en

            with open(yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
        except Exception as exc:
            _log.error("웹 보안 설정 저장 오류: %s", exc)
            return jsonify({"ok": False, "message": f"보안 설정 저장 실패: {exc}"}), 500

    msg = "저장 완료"
    if needs_restart:
        msg += " — API 키/토큰 변경은 앱 재시작 후 적용됩니다"
    _log.info("[설정변경] 시스템 설정 저장 (재시작필요=%s)", needs_restart)
    return jsonify({"ok": True, "message": msg, "needs_restart": needs_restart})


@api_bp.route("/settings/telegram/test", methods=["POST"])
def test_telegram():
    """현재 저장된 텔레그램 설정으로 테스트 메시지 발송."""
    from notification.telegram import TelegramNotifier

    cfg      = current_app.config.get("CFG", {})
    token    = cfg.get("telegram", {}).get("bot_token", "")
    chat_id  = cfg.get("telegram", {}).get("chat_id", "")

    if not token or not chat_id:
        return jsonify({
            "ok":      False,
            "message": "Bot Token과 Chat ID를 먼저 저장(재시작)해주세요.",
        }), 400

    try:
        notifier = TelegramNotifier(token, chat_id)
        notifier.send("🔔 CryptoSplit 테스트 메시지입니다.")
        return jsonify({"ok": True, "message": "테스트 메시지를 전송했습니다."})
    except Exception as exc:
        _log.error("텔레그램 테스트 전송 실패: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500


@api_bp.route("/assets/manual-price", methods=["POST"])
def set_asset_manual_price():
    """코인 수동 평균매수가 저장 후 해당 코인 재계산 결과를 반환한다."""
    from database.order_repo import (
        get_avg_buy_price,
        get_manual_avg_buy_price,
        set_manual_avg_buy_price,
    )

    body   = request.get_json(silent=True) or {}
    symbol = (body.get("symbol") or "").upper().strip()
    try:
        price = float(body.get("avg_buy_price", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid price"}), 400

    if not symbol or price <= 0:
        return jsonify({"error": "symbol and avg_buy_price required"}), 400

    set_manual_avg_buy_price(symbol, price)
    _log.info("[설정변경] %s 수동 평균매수가 저장: %s원", symbol, price)

    exchange = current_app.config.get("EXCHANGE")
    qty = 0.0
    if exchange:
        try:
            for b in exchange.get_balances():
                if b.get("currency", "").upper() == symbol:
                    qty = float(b.get("balance", 0) or 0) + float(b.get("locked", 0) or 0)
                    break
        except Exception as exc:
            _log.warning("잔고 조회 실패 (%s): %s", symbol, exc)

    current_price = 0.0
    if exchange and qty > 0:
        try:
            current_price = _get_price_cached(f"KRW-{symbol}", exchange)
        except Exception:
            pass

    bot_avg  = get_avg_buy_price(symbol)
    avg_buy  = bot_avg if bot_avg > 0 else price
    cost     = avg_buy * qty if avg_buy > 0 else 0.0
    eval_amt = qty * current_price
    pnl      = eval_amt - cost if cost > 0 else 0.0
    pct      = (pnl / cost * 100) if cost > 0 else 0.0

    return jsonify({
        "symbol":           symbol,
        "quantity":         qty,
        "avg_buy_price":    round(avg_buy),
        "manual_avg_price": round(price),
        "cost":             round(cost),
        "eval":             round(eval_amt),
        "unrealized_pnl":   round(pnl),
        "pct":              round(pct, 2),
        "editable":         bot_avg == 0,
    })


@api_bp.route("/trades/orders", methods=["DELETE"])
def delete_trade_orders():
    """투자내역 삭제: ids 목록 또는 기간+심볼로 체결 주문을 삭제한다."""
    from database.order_repo import delete_orders_by_ids, delete_orders_by_range
    from database.pnl_repo   import delete_daily_pnl

    body        = request.get_json(silent=True) or {}
    bot_manager = current_app.config["BOT_MANAGER"]
    symbols     = list(bot_manager.get_all_status().keys())

    ids = body.get("ids")
    if ids is not None:
        count, affected = delete_orders_by_ids([int(i) for i in ids])
    else:
        from web.auth import reauth_ok
        if not reauth_ok(body.get("_reauth_password")):
            return jsonify({"ok": False, "error": "reauth_required", "message": "비밀번호를 다시 확인해주세요."}), 403

        start = (body.get("start") or "").strip()
        end   = (body.get("end")   or "").strip()
        sym   = (body.get("symbol") or "").upper().strip() or None
        if sym == "ALL":
            sym = None
        if not start or not end:
            return jsonify({"ok": False, "message": "start/end 날짜가 필요합니다."}), 400
        count, affected = delete_orders_by_range(symbols, start, end, sym)

    for symbol, date in affected:
        try:
            delete_daily_pnl(symbol, date)
        except Exception as exc:
            _log.warning("daily_pnl 삭제 실패 (%s %s): %s", symbol, date, exc)

    _log.info("주문 삭제: %d건 (daily_pnl %d일 초기화)", count, len(set(affected)))
    return jsonify({"ok": True, "count": count, "message": f"{count}건 삭제되었습니다."})
