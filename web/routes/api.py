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
    """재시작 전 가격 갭 미리보기 (가상 캐치업 예상 내역 반환)."""
    from database.state_repo import get_state
    from utils.math_utils import calc_quantity, round_price

    symbol = request.args.get("symbol", "").upper().strip()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    exchange = current_app.config.get("EXCHANGE")
    cfg      = current_app.config.get("CFG", {})

    state           = get_state(symbol) or {}
    reference_price = float(state.get("reference_price") or 0)
    current_round   = int(state.get("current_round") or 0)

    if reference_price <= 0:
        return jsonify({"gap": 0, "filled": 0, "levels": [],
                        "current_price": 0, "reference_price": 0,
                        "current_round": current_round})

    pair         = cfg.get("pairs", {}).get(symbol, {})
    trigger_mode = pair.get("trigger_mode", "pct")
    trigger_pct  = float(pair.get("trigger_pct", 0.5))
    trigger_krw  = float(pair.get("trigger_krw", 10000))
    order_amount = float(pair.get("order_amount", 50000))
    max_rounds   = int(pair.get("max_rounds", 50))

    current_price = 0.0
    if exchange:
        try:
            ticker        = f"KRW-{symbol}"
            current_price = exchange.get_current_price(ticker)
        except Exception as exc:
            _log.warning("갭 미리보기 현재가 조회 실패 (%s): %s", symbol, exc)

    if current_price <= 0:
        return jsonify({"gap": 0, "filled": 0, "levels": [],
                        "current_price": 0,
                        "reference_price": round(reference_price),
                        "current_round": current_round})

    if trigger_mode == "krw":
        gap = int((reference_price - current_price) // trigger_krw)
    else:
        step = reference_price * trigger_pct / 100
        gap  = int((reference_price - current_price) // step) if step > 0 else 0

    if gap <= 0:
        return jsonify({"gap": 0, "filled": 0, "levels": [],
                        "current_price": round(current_price),
                        "reference_price": round(reference_price),
                        "current_round": current_round})

    room   = max_rounds - current_round
    filled = min(gap, room)

    levels = []
    g = reference_price
    for i in range(filled):
        if trigger_mode == "krw":
            g = g - trigger_krw
        else:
            g = g * (1 - trigger_pct / 100)
        grid_price = float(round_price(g))
        quantity   = calc_quantity(order_amount, grid_price)
        levels.append({
            "round_no":   current_round + i + 1,
            "grid_price": round(grid_price),
            "quantity":   quantity,
            "amount":     round(grid_price * quantity),
        })

    return jsonify({
        "gap":             gap,
        "filled":          filled,
        "current_round":   current_round,
        "reference_price": round(reference_price),
        "current_price":   round(current_price),
        "levels":          levels,
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


@api_bp.route("/bot/stop_all", methods=["POST"])
def stop_all_bots():
    """모든 봇 중지."""
    bot_manager = current_app.config["BOT_MANAGER"]
    stopped     = bot_manager.stop_all()
    return jsonify({"ok": True, "stopped": stopped, "count": len(stopped)})


@api_bp.route("/pnl/today")
def get_today_pnl():
    """오늘 손익 조회."""
    from database.pnl_repo  import get_daily_summary
    from utils.time_utils   import today_kst_str

    date    = today_kst_str()
    summary = get_daily_summary(date)
    return jsonify({"date": date, "symbols": summary})


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

    # 포트폴리오 잔고 (코인 수량)
    portfolio = {}
    if exchange:
        try:
            portfolio = take_snapshot(exchange, symbols)
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


@api_bp.route("/pnl/cumulative")
def get_cumulative_pnl():
    """누적손익 탭: 전체 체결 기준 심볼별 매수/매도 합계."""
    from database.order_repo import get_cumulative_totals

    rows = get_cumulative_totals()

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

        from database.order_repo import get_avg_buy_price, get_manual_avg_buy_price
        bot_avg    = get_avg_buy_price(cur)
        manual_avg = get_manual_avg_buy_price(cur) if bot_avg == 0 else 0.0
        avg_buy    = bot_avg if bot_avg > 0 else manual_avg
        cost       = avg_buy * qty if avg_buy > 0 else 0.0
        eval_amt   = qty * current_price
        pnl        = eval_amt - cost if cost > 0 else 0.0
        pct        = (pnl / cost * 100) if cost > 0 else 0.0

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

    rows = get_filled_orders_range(symbols, start, end, symbol_filter=sym)
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

    rows = get_pnl_rounds(symbols, start, end, symbol_filter=sym)
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

        # 시작가가 전달된 경우 저장
        start_price_raw = body.get("start_price")
        if start_price_raw is not None:
            sp = float(start_price_raw)
            if sp > 0:
                from database.state_repo import set_start_price, set_reference_price
                set_start_price(symbol, sp)
                # 봇이 중지 상태일 때만 기준가도 함께 업데이트 (모니터링 카드 반영)
                bot_manager = current_app.config.get("BOT_MANAGER")
                if bot_manager and not bot_manager.is_running(symbol):
                    set_reference_price(symbol, sp)

        _log.info("[설정변경] %s 전략 설정 저장: %s", symbol,
                  {k: body[k] for k in allowed if k in body})
        return jsonify({"ok": True, "message": "저장 완료"})
    except Exception as exc:
        _log.error("전략 설정 저장 오류: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500


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
    return jsonify({
        "server": {
            "ip":   _get_local_ip(),
            "port": port,
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
            "hour":   cfg.get("report", {}).get("hour", 0),
            "minute": cfg.get("report", {}).get("minute", 0),
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

    # ── settings.yaml 항목 ────────────────────────────────────────────────────
    yaml_fields = {
        "order_timeout":       ("strategy",  "order_timeout",       int),
        "price_poll_interval": ("strategy",  "price_poll_interval", int),
        "report_hour":         ("report",    "hour",                int),
        "report_minute":       ("report",    "minute",              int),
        "membership":          ("exchange",  "membership",          str),
    }
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
