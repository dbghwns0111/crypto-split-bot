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
    start_price: float = 0.0,
) -> str:
    """자동매매 첫 시작 알림 메시지."""
    total = int(order_amount * max_rounds)
    trig  = f"{int(trigger_krw):,}원" if trigger_mode == "krw" else f"{trigger_pct}%"
    lines = [
        f"🚀 {symbol} 자동매매 시작",
        _DIV,
    ]
    if start_price > 0:
        lines.append(f"📍 시작가     {int(start_price):,}원")
    lines += [
        f"⚡ 트리거     {trig}",
        f"💵 매수금액   {int(order_amount):,}원",
        f"🔢 최대차수   {max_rounds}차",
        f"💼 총투자금   {total:,}원",
    ]
    return "\n".join(lines)


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
    trig      = f"{int(trigger_krw):,}원" if trigger_mode == "krw" else f"{trigger_pct}%"
    return "\n".join([
        f"🔄 {symbol} 자동매매 재시작",
        _DIV,
        f"📌 현재차수   {current_round}차",
        f"📍 기준가     {price_str}",
        f"⚡ 트리거     {trig}",
        f"💵 매수금액   {int(order_amount):,}원",
    ])


def build_bot_stopped(
    symbol: str,
    current_round: int = 0,
    reference_price: float = 0.0,
    cancelled_orders: int = 0,
) -> str:
    """자동매매 중지 알림 메시지."""
    price_str = f"{int(reference_price):,}원" if reference_price > 0 else "-"
    lines = [
        f"⏹️ {symbol} 자동매매 중지",
        _DIV,
        f"📌 종료차수   {current_round}차",
        f"📍 기준가     {price_str}",
    ]
    if cancelled_orders > 0:
        lines.append(f"🗑️ 취소주문   {cancelled_orders}건")
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
    return "\n".join([
        f"🔵 {symbol} 매수 체결 · {round_no}차",
        _DIV,
        f"💲 체결가     {price:,.0f}원",
        f"📦 수량       {quantity}개",
        f"💰 매수금액   {amount:,}원",
    ])


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
        f"🟠 {symbol} 매도 체결 · {round_no}차",
        _DIV,
        f"💲 체결가     {price:,.0f}원",
        f"📦 수량       {quantity}개",
        f"💰 매도금액   {amount:,}원",
    ]
    if buy_price > 0:
        rate   = (price - buy_price) / buy_price * 100
        sign_r = "+" if rate >= 0 else ""
        icon   = "📈" if net_pnl >= 0 else "📉"
        lines.append(f"{icon} 순수익     {sign}{int(net_pnl):,}원 ({sign_r}{rate:.2f}%)")
    else:
        icon = "📈" if net_pnl >= 0 else "📉"
        lines.append(f"{icon} 순수익     {sign}{int(net_pnl):,}원")
    return "\n".join(lines)


def build_max_round(
    symbol: str,
    round_no: int,
    max_rounds: int = 50,
) -> str:
    """최대 차수 도달 경고 메시지."""
    return "\n".join([
        f"⚠️ {symbol} 최대 차수 도달",
        _DIV,
        f"🔢 차수       {round_no}/{max_rounds}차",
        f"🛑 매수 중단 · 매도만 진행",
    ])


def build_order_cancelled(
    symbol: str,
    order_id: str,
    side: str,
    price: float,
    elapsed: float,
) -> str:
    """미체결 주문 자동 취소 알림 메시지."""
    side_kr = "매수" if side == "buy" else "매도"
    return "\n".join([
        f"⏰ {symbol} 미체결 {side_kr} 취소",
        _DIV,
        f"💲 취소가     {price:,.0f}원",
        f"🕐 경과       {elapsed:.0f}초",
        f"🔁 재주문 대기",
    ])


def build_catchup(
    symbol: str,
    count: int,
    gap: int,
    price: float,
) -> str:
    """캐치업 매수 완료 알림 메시지."""
    return "\n".join([
        f"⚡ {symbol} 캐치업 매수 완료",
        _DIV,
        f"📉 누락레벨   {gap}단계",
        f"✅ 실행매수   {count}회",
        f"💲 체결가     {price:,.0f}원",
    ])


def build_virtual_catchup(
    symbol: str,
    count: int,
    gap: int,
    from_price: float,
    current_price: float,
) -> str:
    """가상 캐치업 완료 알림 메시지."""
    return "\n".join([
        f"🔮 {symbol} 가상 채우기 완료",
        _DIV,
        f"📉 갭 구간   {int(from_price):,}원 → {int(current_price):,}원",
        f"📊 갭 레벨   {gap}단계",
        f"✅ 가상체결  {count}회",
        f"ℹ️ 실제 매수 없이 차수 기록이 생성되었습니다.",
    ])


def build_api_error(symbol: str, error: str) -> str:
    """API 오류 알림 메시지."""
    return "\n".join([
        f"🚨 {symbol} API 오류",
        _DIV,
        error,
    ])


def build_time_sync_alert(success: bool, reason: str = "") -> str:
    """시간 동기화 결과 알림 메시지."""
    if success:
        lines = ["✅ Windows 시간 동기화 완료", _DIV]
        if reason:
            lines.append(reason)
        else:
            lines.append("주기적 시간 동기화가 완료되었습니다.")
        return "\n".join(lines)
    lines = ["❌ Windows 시간 동기화 실패", _DIV]
    if reason:
        lines.append(reason)
    else:
        lines.append("시간 동기화에 실패했습니다.")
    lines.append("관리자 권한으로 아래 명령어를 실행해 주세요:")
    lines.append("w32tm /resync /force")
    return "\n".join(lines)


def build_db_backup_alert(success: bool, detail: str = "") -> str:
    """DB 백업/무결성 결과 알림 메시지."""
    if success:
        lines = ["✅ DB 백업 완료", _DIV]
        lines.append(detail or "데이터베이스 백업이 완료되었습니다.")
        return "\n".join(lines)
    lines = ["🚨 DB 백업/무결성 경고", _DIV]
    lines.append(detail or "데이터베이스 백업 또는 무결성 검사에 실패했습니다.")
    lines.append("data/backups 폴더와 디스크 용량을 확인해 주세요.")
    return "\n".join(lines)


def build_watchdog_alert(symbol: str, recovered: bool) -> str:
    """봇 스레드 사망 감지/복구 알림 메시지."""
    if recovered:
        return "\n".join([
            f"🔧 {symbol} 봇 자동 복구",
            _DIV,
            "봇 스레드가 중단된 것을 감지해 자동 재시작했습니다.",
            "차수와 기준가는 DB에서 복원되었습니다.",
        ])
    return "\n".join([
        f"🚨 {symbol} 봇 복구 실패",
        _DIV,
        "봇 스레드 사망을 감지했으나 자동 복구에 실패했습니다.",
        "대시보드에서 수동으로 재시작해 주세요.",
    ])


def build_partial_fill_alert(
    symbol: str, side: str, qty: float, price: float, leftover: float = 0.0
) -> str:
    """부분 체결 정산 알림 메시지."""
    side_kr = "매수" if side == "buy" else "매도"
    lines = [
        f"⚠️ {symbol} 부분 체결 정산 · {side_kr}",
        _DIV,
        f"📦 체결수량   {qty}",
    ]
    if price:
        lines.append(f"💲 체결가     {price:,.0f}원")
    if leftover > 0:
        lines.append(f"🔸 미매도잔량  {leftover}")
        lines.append("그리드 정합을 위해 잔량을 수동 확인해 주세요.")
    return "\n".join(lines)


# ── 정기 알림 ──────────────────────────────────────────────────────────────────

def build_hourly_status(hour: int, statuses: list[dict]) -> str:
    """매 정각 가동 현황 메시지.

    statuses 각 원소:
        {"symbol": str, "running": bool,
         "current_price": float, "current_round": int, "max_rounds": int,
         "start_price": float, "reference_price": float,
         "pending_buy_price": float, "pending_sell_price": float,
         "today_pnl": float, "buy_count": int, "sell_count": int}
    """
    any_running = any(s.get("running") for s in statuses)
    icon  = "🟢" if any_running else "🔴"
    lines = [f"{icon} CryptoSplit 가동현황 [{hour:02d}:00]"]

    for s in statuses:
        sym = s["symbol"]
        lines.append(_DIV)
        if s.get("running"):
            rnd        = s.get("current_round", 0)
            max_rnd    = s.get("max_rounds", 50)
            price      = s.get("current_price", 0)
            start_p    = s.get("start_price", 0)
            ref_p      = s.get("reference_price", 0)
            buy_p      = s.get("pending_buy_price", 0)
            sell_p     = s.get("pending_sell_price", 0)
            pnl        = s.get("today_pnl", 0)
            buys       = s.get("buy_count", 0)
            sells      = s.get("sell_count", 0)
            sign       = "+" if pnl >= 0 else ""
            pnl_icon   = "📈" if pnl >= 0 else "📉"

            lines.append(f"📊 {sym}  실행중 · {rnd}/{max_rnd}차")
            if start_p > 0:
                lines.append(f"🚀 시작가   {start_p:,.0f}원")
            if ref_p > 0:
                lines.append(f"📌 기준가   {ref_p:,.0f}원")
            lines.append(f"💲 현재가   {price:,.0f}원")
            if buy_p > 0:
                lines.append(f"🔵 매수대기  {buy_p:,.0f}원")
            else:
                lines.append(f"🔵 매수대기  -")
            if sell_p > 0:
                lines.append(f"🟠 매도대기  {sell_p:,.0f}원")
            else:
                lines.append(f"🟠 매도대기  -")
            lines.append(f"{pnl_icon} 일일수익   {sign}{pnl:,.0f}원  (매수 {buys} / 매도 {sells})")
        else:
            lines.append(f"📊 {sym}  중지됨")

    return "\n".join(lines)


def build_daily_report(
    date: str,
    symbol_data: list[dict],
    total_krw: float,
    balances: dict[str, float],
    prices: dict[str, float] | None = None,
) -> str:
    """일일 결산 리포트 메시지.

    symbol_data 각 원소:
        {"symbol": str, "realized_pnl": float,
         "trade_count": int, "fee_total": float,
         "buy_count": int, "sell_count": int}
    balances: {"ETH": float, "USDT": float, ...}
    prices:   {"ETH": float, "USDT": float, ...}  # 결산 시점 현재가
    """
    lines     = [f"📊 CryptoSplit 일일 결산 [{date}]"]
    grand_net = 0.0

    for s in symbol_data:
        sym   = s["symbol"]
        net   = float(s.get("realized_pnl", 0))   # realized_pnl = 이미 수수료 차감된 순수익
        fee   = float(s.get("fee_total", 0))
        gross = net + fee                           # 수수료 차감 전 실현수익 역산
        grand_net += net

        buys   = int(s.get("buy_count",  0))
        sells  = int(s.get("sell_count", 0))
        sign_g = "+" if gross >= 0 else ""
        sign_n = "+" if net >= 0 else ""
        net_icon = "📈" if net >= 0 else "📉"

        lines += [
            _DIV,
            f"[ {sym} ]",
            f"💰 실현수익   {sign_g}{gross:,.0f}원",
            f"🔄 매수/매도  {buys}/{sells}회",
            f"💸 수수료     -{fee:,.0f}원",
            f"{net_icon} 순수익     {sign_n}{net:,.0f}원",
        ]

    sign_g   = "+" if grand_net >= 0 else ""
    net_icon = "📈" if grand_net >= 0 else "📉"
    lines += [
        _DIV,
        f"{net_icon} 전체순수익  {sign_g}{grand_net:,.0f}원",
        f"💵 KRW잔고   {total_krw:,.0f}원",
    ]
    for sym, bal in balances.items():
        bal_str = f"{bal:.8f}"
        price   = (prices or {}).get(sym, 0)
        krw_val = int(bal * price) if price > 0 else 0
        if krw_val > 0:
            lines.append(f"🪙 {sym}   {bal_str} {sym}")
            lines.append(f"     └ ≈ {krw_val:,}원  (현재가 {int(price):,}원)")
        else:
            lines.append(f"🪙 {sym}   {bal_str} {sym}")

    return "\n".join(lines)
