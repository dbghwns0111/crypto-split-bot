"""텔레그램 메시지 포맷 — 순수 함수 모음 (9종)."""

_DIV = "─" * 17


# ── 봇 제어 알림 ──────────────────────────────────────────────────────────────

def build_bot_started(
    symbol: str,
    trigger_mode: str = "pct",
    trigger_pct: float = 0.5,
    trigger_krw: float = 0.0,
    order_amount: float = 50000,
    max_rounds: int = 50,
) -> str:
    """자동매매 첫 시작 알림 메시지."""
    total = int(order_amount * max_rounds)
    trig  = f"{int(trigger_krw):,}원 고정" if trigger_mode == "krw" else f"{trigger_pct}%"
    return (
        f"▶️ {symbol} 자동매매 시작\n"
        f"{_DIV}\n"
        f"트리거: {trig}\n"
        f"1회주문: {int(order_amount):,}원\n"
        f"최대차수: {max_rounds}차\n"
        f"총자금: {total:,}원"
    )


def build_bot_restarted(
    symbol: str,
    current_round: int,
    reference_price: float,
    trigger_mode: str = "pct",
    trigger_pct: float = 0.5,
    trigger_krw: float = 0.0,
    order_amount: float = 50000,
) -> str:
    """자동매매 재시작 알림 메시지."""
    price_str = f"{int(reference_price):,}원" if reference_price > 0 else "-"
    trig      = f"{int(trigger_krw):,}원 고정" if trigger_mode == "krw" else f"{trigger_pct}%"
    return (
        f"▶️ {symbol} 자동매매 재시작\n"
        f"{_DIV}\n"
        f"현재차수: {current_round}차\n"
        f"기준가: {price_str}\n"
        f"트리거: {trig}\n"
        f"1회주문: {int(order_amount):,}원"
    )


def build_bot_stopped(
    symbol: str,
    current_round: int = 0,
    reference_price: float = 0.0,
    cancelled_orders: int = 0,
) -> str:
    """자동매매 중지 알림 메시지."""
    price_str = f"{int(reference_price):,}원" if reference_price > 0 else "-"
    lines = [
        f"⏹ {symbol} 자동매매 중지",
        _DIV,
        f"종료차수: {current_round}차",
        f"기준가: {price_str}",
    ]
    if cancelled_orders > 0:
        lines.append(f"취소주문: {cancelled_orders}건")
    return "\n".join(lines)


# ── 거래 알림 ──────────────────────────────────────────────────────────────────

def build_buy_filled(
    symbol: str,
    price: float,
    quantity: float,
    round_no: int,
) -> str:
    """매수 체결 알림 메시지."""
    amount = int(price * quantity)
    return (
        f"🟢 {symbol} 매수 체결\n"
        f"{_DIV}\n"
        f"체결가: {price:,.0f}원\n"
        f"수량: {quantity:.6f}\n"
        f"금액: {amount:,}원\n"
        f"차수: {round_no}차"
    )


def build_sell_filled(
    symbol: str,
    price: float,
    quantity: float,
    round_no: int,
    net_pnl: float = 0.0,
    buy_price: float = 0.0,
) -> str:
    """매도 체결 알림 메시지."""
    amount = int(price * quantity)
    sign   = "+" if net_pnl >= 0 else ""
    lines  = [
        f"🔴 {symbol} 매도 체결",
        _DIV,
        f"체결가: {price:,.0f}원",
        f"수량: {quantity:.6f}",
        f"금액: {amount:,}원",
        f"차수: {round_no}차",
    ]
    if buy_price > 0:
        rate   = (price - buy_price) / buy_price * 100
        sign_r = "+" if rate >= 0 else ""
        lines.append(f"순수익: {sign}{int(net_pnl):,}원 ({sign_r}{rate:.2f}%)")
    else:
        lines.append(f"순수익: {sign}{int(net_pnl):,}원")
    return "\n".join(lines)


def build_max_round(
    symbol: str,
    round_no: int,
    max_rounds: int = 50,
) -> str:
    """최대 차수 도달 경고 메시지."""
    return (
        f"⚠️ {symbol} 최대 차수 도달\n"
        f"{_DIV}\n"
        f"차수: {round_no}/{max_rounds}차\n"
        f"매수 중단 · 매도만 진행"
    )


def build_order_cancelled(
    symbol: str,
    order_id: str,
    side: str,
    price: float,
    elapsed: float,
) -> str:
    """미체결 주문 자동 취소 알림 메시지."""
    side_kr = "매수" if side == "buy" else "매도"
    return (
        f"⏰ {symbol} 미체결 {side_kr} 취소\n"
        f"{_DIV}\n"
        f"취소가: {price:,.0f}원\n"
        f"경과: {elapsed:.0f}초\n"
        f"재주문 대기 중"
    )


def build_catchup(
    symbol: str,
    count: int,
    gap: int,
    price: float,
) -> str:
    """캐치업 매수 완료 알림 메시지."""
    return (
        f"🔄 {symbol} 캐치업 매수 완료\n"
        f"{_DIV}\n"
        f"누락레벨: {gap}단계\n"
        f"실행매수: {count}회\n"
        f"체결가: {price:,.0f}원"
    )


def build_api_error(symbol: str, error: str) -> str:
    """API 오류 알림 메시지."""
    return (
        f"🚨 {symbol} API 오류\n"
        f"{_DIV}\n"
        f"{error}"
    )


# ── 정기 알림 ──────────────────────────────────────────────────────────────────

def build_hourly_status(hour: int, statuses: list[dict]) -> str:
    """매 정각 가동 현황 메시지.

    statuses 각 원소:
        {"symbol": str, "running": bool,
         "current_price": float, "current_round": int,
         "today_pnl": float, "buy_count": int, "sell_count": int}
    """
    any_running = any(s.get("running") for s in statuses)
    icon = "🟢" if any_running else "🔴"
    lines = [f"{icon} CryptoSplit 가동현황 [{hour:02d}:00]"]

    for s in statuses:
        sym = s["symbol"]
        lines.append(_DIV)
        if s.get("running"):
            price = s.get("current_price", 0)
            rnd   = s.get("current_round", 0)
            pnl   = s.get("today_pnl", 0)
            buys  = s.get("buy_count", 0)
            sells = s.get("sell_count", 0)
            sign  = "+" if pnl >= 0 else ""
            lines += [
                f"[{sym}] 실행중",
                f"현재가: {price:,.0f}원 | 차수: {rnd}차",
                f"오늘수익: {sign}{pnl:,.0f}원 | 매수 {buys}회 / 매도 {sells}회",
            ]
        else:
            lines.append(f"[{sym}] 중지됨")

    return "\n".join(lines)


def build_daily_report(
    date: str,
    symbol_data: list[dict],
    total_krw: float,
    balances: dict[str, float],
) -> str:
    """일일 결산 리포트 메시지.

    symbol_data 각 원소:
        {"symbol": str, "realized_pnl": float,
         "trade_count": int, "fee_total": float,
         "buy_count": int, "sell_count": int}
    balances: {"ETH": float, "USDT": float, ...}
    """
    lines     = [f"📊 CryptoSplit 일일 결산 [{date}]"]
    grand_net = 0.0

    for s in symbol_data:
        sym      = s["symbol"]
        realized = float(s.get("realized_pnl", 0))
        fee      = float(s.get("fee_total", 0))
        net      = realized - fee
        grand_net += net

        buys   = int(s.get("buy_count",  0))
        sells  = int(s.get("sell_count", 0))
        sign_r = "+" if realized >= 0 else ""
        sign_n = "+" if net >= 0 else ""

        lines += [
            _DIV,
            f"[{sym}]",
            f"실현수익: {sign_r}{realized:,.0f}원",
            f"매수/매도: {buys}/{sells}회",
            f"수수료: -{fee:,.0f}원",
            f"순수익: {sign_n}{net:,.0f}원",
        ]

    sign_g = "+" if grand_net >= 0 else ""
    lines.append(_DIV)
    lines.append(f"전체순수익: {sign_g}{grand_net:,.0f}원")
    lines.append(f"KRW잔고: {total_krw:,.0f}원")
    for sym, bal in balances.items():
        lines.append(f"{sym}: {bal} {sym}")

    return "\n".join(lines)
