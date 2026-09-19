"""텔레그램 메시지 포맷 — 순수 함수 모음.

전송은 HTML parse_mode 로 이뤄진다(notification/telegram.py). 그래서 이 모듈이
만드는 문자열에는 <b>/<pre> 태그가 들어간다. 두 가지 규칙이 중요하다:

1. **숫자는 전부 <pre> 블록 안에 넣는다.** 텔레그램 기본 폰트는 비례폭이라
   공백으로는 절대 열이 맞지 않는다 — 한글 라벨은 글자마다 폭이 달라 더 심하다.
   _w() 가 한글을 2칸으로 세어 패딩하므로 <pre> 안에서만 정렬이 성립한다.
2. **<pre> 안에는 이모지를 넣지 않는다.** 이모지 폭이 플랫폼마다 1~2칸을
   오가서 애써 맞춘 정렬이 깨진다. 색 이모지는 제목 줄과 <pre> 바깥 평문에만.

색은 웹 대시보드(web/static/css/style.css)와 같은 의미로 맞춘다:

    매수·매수트리거 🔴 (.cs-negative)   매도·매도트리거 🟢 (.cs-positive)
    이익 🟢 / 손실 🔴                    가동중 🔵 (.cs-badge-running) / 중지 ⚪

즉 빨강·초록은 "방향과 손익" 전용, 파랑·회색은 "가동 상태" 전용이다. 경고류는
색이 아니라 모양으로 구분한다(⚠️ 🛑 🚨 ⏰ 🔧). 이 규칙을 바꾸려면 웹 CSS의
.cs-positive/.cs-negative/.cs-badge-running 도 같이 바꿔야 한다.
"""

import html
import unicodedata

# ── 색 이모지 (웹 style.css 와 1:1 대응) ──────────────────────────────────────
ICON_BUY     = "🔴"   # 매수 · 매수 트리거   ← .cs-negative (#F04452)
ICON_SELL    = "🟢"   # 매도 · 매도 트리거   ← .cs-positive (#00A870)
ICON_RUNNING = "🔵"   # 가동중               ← .cs-badge-running (#3182F6)
ICON_STOPPED = "⚪"   # 중지                 ← .cs-badge-stopped (muted)

_LABEL_W = 8    # 라벨 열 기본 폭 (표시 폭 기준, 한글 2칸)
_VALUE_W = 15   # 값 열 기본 폭
_GAP     = 2    # 표 열 간격


# ── 폭 계산 · 패딩 ────────────────────────────────────────────────────────────

def _w(s) -> int:
    """고정폭 폰트 기준 표시 폭. 한글·전각 문자는 2칸으로 센다."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in str(s))


def _rpad(s, n: int) -> str:
    """좌측 정렬 (라벨용)."""
    return str(s) + " " * max(0, n - _w(s))


def _lpad(s, n: int) -> str:
    """우측 정렬 (숫자용)."""
    return " " * max(0, n - _w(s)) + str(s)


def _esc(s) -> str:
    """HTML 특수문자 이스케이프.

    parse_mode=HTML 로 보내므로 동적 문자열에 < > & 가 섞이면 텔레그램이 400을
    돌려주고 **메시지가 통째로 유실된다.** 체결 알림 유실은 치명적이라 card()가
    받는 모든 값을 예외 없이 이 함수에 통과시킨다. 따라서 이 모듈 바깥에서
    HTML 태그를 직접 만들어 넘기면 안 된다 — 태그는 card() 만 붙인다.
    """
    return html.escape(str(s), quote=False)


# ── 숫자 포맷 ─────────────────────────────────────────────────────────────────

def krw(v) -> str:
    """12345 → '12,345원'"""
    return f"{round(float(v or 0)):,}원"


def signed(v, unit: str = "원") -> str:
    """양수에 + 를 붙인다. 0도 '+0'. -1000 → '-1,000원'"""
    n = round(float(v or 0))
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:,}{unit}"


def qty(v) -> str:
    """수량 — 소수점 8자리 고정.

    자릿수를 고정해야 <pre> 안에서 소수점 위치가 세로로 맞는다. 거래소
    표기(8자리)와도 일치한다.
    """
    return f"{float(v or 0):.8f}"


def _num(v) -> str:
    """단위 없는 천단위 구분 숫자. 표 셀에서 쓴다."""
    return f"{round(float(v or 0)):,}"


# ── 블록 조립 ─────────────────────────────────────────────────────────────────

def rows(pairs, label_w: int = _LABEL_W, value_w: int = _VALUE_W,
         align: str = "r") -> list:
    """(라벨, 값) 목록 → 정렬된 줄 목록.

    값이 기본 열 폭보다 길면 열 폭을 자동으로 넓힌다. 값이 None 인 항목은
    건너뛴다 — 호출부에서 조건부 줄을 if 없이 표현할 수 있다.
    align="l" 이면 값을 좌측 정렬한다(설정 변경의 "A → B" 처럼 숫자가 아닌 값).
    """
    items = [(str(label), str(value)) for label, value in pairs if value is not None]
    if not items:
        return []
    lw = max(label_w, max(_w(label) for label, _ in items) + _GAP)
    if align == "l":
        return [_rpad(label, lw) + value for label, value in items]
    vw = max(value_w, max(_w(value) for _, value in items))
    return [_rpad(label, lw) + _lpad(value, vw) for label, value in items]


def table(headers: list, data: list) -> list:
    """헤더 1줄 + 데이터 줄들의 표. 첫 열은 좌측, 나머지는 우측 정렬."""
    if not data:
        return []
    grid   = [[str(c) for c in headers]] + [[str(c) for c in r] for r in data]
    widths = [max(_w(r[i]) for r in grid) for i in range(len(headers))]
    out    = []
    for r in grid:
        cells = [_rpad(c, widths[i]) if i == 0 else _lpad(c, widths[i])
                 for i, c in enumerate(r)]
        out.append((" " * _GAP).join(cells).rstrip())
    return out


def rule(lines: list) -> str:
    """표 아래 합계선 — 표에서 가장 긴 줄의 폭에 맞춘다."""
    return "─" * (max((_w(line) for line in lines), default=1) or 1)


def is_card(text: str) -> bool:
    """card() 가 만든 문자열인지 판별한다.

    card() 출력은 항상 <b> 로 시작한다. 전송부(telegram.py / telegram_handler.py)가
    이걸로 "이미 이스케이프까지 끝난 HTML"과 "그냥 평문"을 구분해, 평문은
    자동으로 이스케이프한 뒤 보낸다 — 새 평문 메시지를 추가할 때 이스케이프를
    잊어도 깨지지 않게 하기 위함이다.
    """
    return str(text).startswith("<b>")


def card(title: str, body: list = None, sub: str = None, foot: str = None) -> str:
    """제목(볼드) + 부제 + <pre> 본문 + 꼬리말.

    모든 알림이 이 함수 하나를 통과한다. 받은 값은 전부 이스케이프하므로
    호출부는 평문만 넘기면 된다.
    """
    parts = [f"<b>{_esc(title)}</b>"]
    if sub:
        parts.append(_esc(sub))
    if body:
        parts.append("<pre>" + _esc("\n".join(body)) + "</pre>")
    if foot:
        parts.append(_esc(foot))
    return "\n".join(parts)


# ── 봇 제어 알림 ──────────────────────────────────────────────────────────────

def _trigger_rows(trigger_mode, trigger_pct, trigger_krw,
                  trigger_pct_sell=0.0, trigger_krw_sell=0.0) -> list:
    """트리거 간격 (라벨, 값) 쌍. 매수·매도가 같으면 한 줄로 합친다."""
    if trigger_mode == "krw":
        buy  = krw(trigger_krw)
        sell = krw(trigger_krw_sell) if trigger_krw_sell else buy
    else:
        buy  = f"{trigger_pct}%"
        sell = f"{trigger_pct_sell}%" if trigger_pct_sell else buy
    if sell == buy:
        return [("트리거 간격", buy)]
    return [("매수 간격", buy), ("매도 간격", sell)]


def build_bot_started(
    symbol: str,
    trigger_mode: str = "pct",
    trigger_pct: float = 0.5,
    trigger_krw: float = 0.0,
    order_amount: float = 50000,
    max_rounds: int = 50,
    start_price: float = 0.0,
    trigger_pct_sell: float = 0.0,
    trigger_krw_sell: float = 0.0,
) -> str:
    """자동매매 첫 시작 알림."""
    pairs = [("시작가", krw(start_price) if start_price > 0 else None)]
    pairs += _trigger_rows(trigger_mode, trigger_pct, trigger_krw,
                           trigger_pct_sell, trigger_krw_sell)
    pairs += [
        ("1회 주문", krw(order_amount)),
        ("최대 차수", f"{max_rounds}차"),
        ("총 투자금", krw(order_amount * max_rounds)),
    ]
    return card(f"🚀 {symbol} 자동매매 시작", rows(pairs))


def build_bot_restarted(
    symbol: str,
    current_round: int,
    reference_price: float,
    trigger_mode: str = "pct",
    trigger_pct: float = 0.5,
    trigger_krw: float = 0.0,
    order_amount: float = 50000,
    trigger_pct_sell: float = 0.0,
    trigger_krw_sell: float = 0.0,
) -> str:
    """자동매매 재시작 알림 (이전 상태 복원)."""
    pairs = [
        ("현재 차수", f"{current_round}차"),
        ("기준가", krw(reference_price) if reference_price > 0 else "—"),
    ]
    pairs += _trigger_rows(trigger_mode, trigger_pct, trigger_krw,
                           trigger_pct_sell, trigger_krw_sell)
    pairs.append(("1회 주문", krw(order_amount)))
    return card(f"🔄 {symbol} 자동매매 재시작", rows(pairs))


def build_bot_stopped(
    symbol: str,
    current_round: int = 0,
    reference_price: float = 0.0,
    cancelled_orders: int = 0,
) -> str:
    """자동매매 중지 알림."""
    return card(f"⏹️ {symbol} 자동매매 중지", rows([
        ("종료 차수", f"{current_round}차"),
        ("기준가", krw(reference_price) if reference_price > 0 else "—"),
        ("취소 주문", f"{cancelled_orders}건" if cancelled_orders > 0 else None),
    ]))


# ── 거래 알림 ──────────────────────────────────────────────────────────────────

def build_buy_filled(symbol: str, price: float, quantity: float,
                     round_no: int) -> str:
    """매수 체결 알림."""
    return card(f"{ICON_BUY} {symbol} 매수 체결 · {round_no}차", rows([
        ("체결가", krw(price)),
        ("수량", f"{qty(quantity)} {symbol}"),
        ("매수금액", krw(price * quantity)),
    ]))


def build_sell_filled(
    symbol: str,
    price: float,
    quantity: float,
    round_no: int,
    net_pnl: float = 0.0,
    buy_price: float = 0.0,
) -> str:
    """매도 체결 알림. 실현손익은 수수료가 이미 반영된 순손익이다."""
    foot = None
    if buy_price > 0:
        rate = (price - buy_price) / buy_price * 100
        sign = "+" if rate >= 0 else ""
        foot = f"수익률 {sign}{rate:.2f}% · 차수 {round_no} → {round_no - 1}"
    return card(
        f"{ICON_SELL} {symbol} 매도 체결 · {round_no}차",
        rows([
            ("체결가", krw(price)),
            ("수량", f"{qty(quantity)} {symbol}"),
            ("매도금액", krw(price * quantity)),
            ("실현손익", signed(net_pnl)),
        ]),
        foot=foot,
    )


def build_max_round(symbol: str, round_no: int, max_rounds: int = 50) -> str:
    """최대 차수 도달 — 매수 중단 경고."""
    return card(
        f"⚠️ {symbol} 최대 차수 도달 · {round_no}/{max_rounds}차",
        foot="매수를 중단하고 매도만 진행합니다.",
    )


def build_order_cancelled(
    symbol: str, order_id: str, side: str, price: float, elapsed: float,
) -> str:
    """미체결 주문 자동 취소 알림."""
    side_kr = "매수" if side == "buy" else "매도"
    return card(
        f"⏰ {symbol} {side_kr} 미체결 취소",
        rows([("주문가", krw(price)), ("경과", f"{elapsed:.0f}초")]),
        foot="같은 조건으로 재주문합니다.",
    )


def build_catchup(symbol: str, count: int, gap: int, price: float) -> str:
    """캐치업(실제) 매수 완료 알림."""
    return card(f"⚡ {symbol} 캐치업 매수 완료", rows([
        ("누락 레벨", f"{gap}단계"),
        ("실행 매수", f"{count}회"),
        ("체결가", krw(price)),
    ]))


def build_virtual_catchup(
    symbol: str, count: int, gap: int, from_price: float, current_price: float,
) -> str:
    """가상 캐치업 완료 알림 — 실제 매수 없이 차수 기록만 만든 경우."""
    return card(
        f"🔮 {symbol} 가상 채우기 완료",
        rows([
            ("갭 구간", f"{_num(from_price)} → {_num(current_price)}"),
            ("갭 레벨", f"{gap}단계"),
            ("가상 체결", f"{count}회"),
        ]),
        foot="실제 매수 없이 차수 기록만 생성했습니다.",
    )


def build_clock_offset_alert(skew_sec: float) -> str:
    """OS 시계 오차 자동 보정 알림."""
    direction = "느림" if skew_sec > 0 else "빠름"
    return card(
        "⏱️ 시계 오차 자동 보정",
        rows([("OS 시계", f"NTP 기준 {abs(skew_sec):,.1f}초 {direction}")]),
        foot="API 요청 시각은 NTP 기준으로 보정 중이라 매매는 정상 진행됩니다.",
    )


def build_auto_rebase(symbol: str, old_price: float, new_price: float) -> str:
    """자동 시작가 트레일링(래칫) 발동 알림."""
    return card(
        f"📈 {symbol} 시작가 자동 상향",
        rows([("변경 전", krw(old_price)), ("변경 후", krw(new_price))]),
        foot="0차 대기 중 가격이 시작가 위로 유지되어 간격 1칸만큼 올렸습니다.",
    )


def build_api_error(symbol: str, error: str) -> str:
    """API 오류 알림. error 에 태그 문자가 섞여도 card() 가 이스케이프한다."""
    return card(f"🚨 {symbol} API 오류", foot=str(error))


def build_time_sync_alert(success: bool, reason: str = "") -> str:
    """시간 동기화 결과 알림 (주기 동기화는 실패 시에만 발송된다)."""
    if success:
        return card("✅ Windows 시간 동기화 완료",
                    foot=reason or "주기적 시간 동기화가 완료되었습니다.")
    detail = reason or "시간 동기화에 실패했습니다."
    return card("❌ Windows 시간 동기화 실패",
                foot=f"{detail}\n관리자 권한으로 실행: w32tm /resync /force")


def build_db_backup_alert(success: bool, detail: str = "") -> str:
    """DB 백업/무결성 결과 알림 (성공은 보통 발송하지 않는다)."""
    if success:
        return card("✅ DB 백업 완료",
                    foot=detail or "데이터베이스 백업이 완료되었습니다.")
    body = detail or "데이터베이스 백업 또는 무결성 검사에 실패했습니다."
    return card("🚨 DB 백업/무결성 경고",
                foot=f"{body}\ndata/backups 폴더와 디스크 용량을 확인해 주세요.")


def build_watchdog_alert(symbol: str, recovered: bool) -> str:
    """봇 스레드 사망 감지/복구 알림 (3분마다 점검)."""
    if recovered:
        return card(f"🔧 {symbol} 봇 자동 복구",
                    foot="봇 스레드 중단을 감지해 자동 재시작했습니다.\n"
                         "차수와 기준가는 DB에서 복원되었습니다.")
    return card(f"🚨 {symbol} 봇 복구 실패",
                foot="봇 스레드 사망을 감지했으나 자동 복구에 실패했습니다.\n"
                     "대시보드에서 수동으로 재시작해 주세요.")


def build_low_balance_alert(symbol: str, krw_balance: float,
                            order_amount: float) -> str:
    """KRW 잔고 부족 — 매수 자동 중단 알림."""
    return card(
        f"🛑 {symbol} 잔고 부족 — 매수 중단",
        rows([("주문 가능", krw(krw_balance)), ("1회 주문", krw(order_amount))]),
        foot="매도는 계속 진행됩니다.\n원화 충전 시 재시작 없이 자동 재개됩니다.",
    )


def build_partial_fill_alert(
    symbol: str, side: str, qty_filled: float, price: float,
    leftover: float = 0.0,
) -> str:
    """부분 체결 정산 알림 (타임아웃 취소 시)."""
    side_kr = "매수" if side == "buy" else "매도"
    return card(
        f"⚠️ {symbol} 부분 체결 정산 · {side_kr}",
        rows([
            ("체결 수량", qty(qty_filled)),
            ("체결가", krw(price) if price else None),
            ("미매도 잔량", qty(leftover) if leftover > 0 else None),
        ]),
        foot="그리드 정합을 위해 잔량을 수동 확인해 주세요." if leftover > 0 else None,
    )


# ── 정기 알림 ──────────────────────────────────────────────────────────────────

def status_table(statuses: list) -> list:
    """종목별 현황 표 — 정각 알림과 /status 명령이 공유한다.

    statuses 각 원소: symbol, running, current_price, current_round,
                     max_rounds, today_pnl
    """
    data = []
    for s in statuses:
        sym = s["symbol"]
        if s.get("running"):
            rnd = f"{s.get('current_round', 0)}/{s.get('max_rounds', 50)}차"
            pnl = signed(s.get("today_pnl", 0), unit="")
        else:
            rnd, pnl = "중지됨", "—"
        price = float(s.get("current_price") or 0)
        data.append([sym, rnd, _num(price) if price > 0 else "—", pnl])
    return table(["종목", "차수", "현재가", "오늘손익"], data)


def trigger_lines(statuses: list) -> list:
    """대기 트리거 줄 — 색 이모지가 필요해 <pre> 밖 평문으로 나간다."""
    out = []
    for s in statuses:
        if not s.get("running"):
            continue
        buy  = float(s.get("pending_buy_price") or 0)
        sell = float(s.get("pending_sell_price") or 0)
        if not (buy or sell):
            continue
        buy_s  = _num(buy) if buy else "—"
        sell_s = _num(sell) if sell else "—"
        out.append(f"{ICON_BUY} {s['symbol']} {buy_s}   {ICON_SELL} {sell_s}")
    return out


def build_hourly_status(hour: int, statuses: list) -> str:
    """정각 가동 현황 알림."""
    running = [s for s in statuses if s.get("running")]
    icon    = ICON_RUNNING if running else ICON_STOPPED
    title   = f"{icon} 가동현황 · {hour:02d}:00"

    if not statuses:
        return card(title, foot="등록된 종목이 없습니다.")

    total = sum(float(s.get("today_pnl", 0) or 0) for s in statuses)
    sub   = f"{len(running)}/{len(statuses)} 종목 실행중 · 오늘 {signed(total)}"

    trig = trigger_lines(statuses)
    foot = ("대기 트리거\n" + "\n".join(trig)) if trig else None
    return card(title, status_table(statuses), sub=sub, foot=foot)


def build_daily_report(
    date: str,
    symbol_data: list,
    total_krw: float,
    balances: dict,
    prices: dict = None,
) -> str:
    """일일 결산 리포트.

    symbol_data 의 realized_pnl 은 **이미 수수료 차감·포인트가 반영된 순손익**
    이다(database/pnl_repo.py). 여기서 수수료를 또 빼면 이중 차감이 된다 —
    과거에 실제로 있었던 버그이므로 절대 되돌리지 말 것.
    """
    grand_net = grand_fee = grand_pt = 0.0
    grand_trades = 0
    data = []
    for s in symbol_data:
        net    = float(s.get("realized_pnl", 0) or 0)
        fee    = float(s.get("fee_total", 0) or 0)
        points = float(s.get("point_total", 0) or 0)
        trades = int(s.get("trade_count", 0) or 0)
        grand_net    += net
        grand_fee    += fee
        grand_pt     += points
        grand_trades += trades
        # 거래가 없으면 "-0"/"+0" 대신 "0" — 부호가 붙으면 발생한 것처럼 보인다
        data.append([s["symbol"], signed(net, unit=""), f"{trades}회",
                     f"-{_num(fee)}" if fee else "0",
                     f"+{_num(points)}" if points else "0"])

    body = []
    if data:
        body = table(["종목", "손익", "거래", "수수료", "포인트"], data)
        if len(data) > 1:
            body.append(rule(body))
            total_row = table(["", "", "", "", ""], [[
                "합계", signed(grand_net, unit=""), f"{grand_trades}회",
                f"-{_num(grand_fee)}" if grand_fee else "0",
                f"+{_num(grand_pt)}" if grand_pt else "0",
            ]])
            body += total_row[1:]

    # 보유자산 — 수량과 평가금액을 같은 표에 둔다
    assets = [["KRW", "—", krw(total_krw)]]
    total_assets = float(total_krw or 0)
    for sym, bal in (balances or {}).items():
        price   = float((prices or {}).get(sym, 0) or 0)
        krw_val = float(bal or 0) * price
        total_assets += krw_val
        assets.append([sym, qty(bal), krw(krw_val) if price > 0 else "—"])
    assets.append(["총계", "—", krw(total_assets)])

    if body:
        body.append("")
    body += table(["자산", "수량", "평가금액"], assets)

    return card(
        f"📊 일일 결산 · {date}",
        body,
        sub=f"총손익 {signed(grand_net)}",
        foot="포인트는 비현금 적립분입니다." if grand_pt > 0 else None,
    )


# ── 설정 변경 알림 ────────────────────────────────────────────────────────────

def build_strategy_settings_changed(symbol: str, changes: dict) -> str:
    """전략 탭(심볼별) 변경 알림. changes: {라벨: (이전, 이후)}"""
    return card(
        f"⚙️ {symbol} 전략 설정 변경",
        rows([(label, f"{old} → {new}")
              for label, (old, new) in changes.items()], align="l"),
    )


def build_system_settings_changed(changes: list) -> str:
    """설정 탭 변경 알림.

    changes 는 이미 "항목: 이전 → 이후" 로 조립된 문자열 목록이다. API 키·토큰·
    비밀번호는 호출부(web/routes/api.py)에서 값 없이 항목명만 담아 보낸다 —
    이 함수는 받은 것을 그대로 표시하므로 그 규칙이 깨지지 않도록 주의할 것.

    길이를 예측할 수 없는 자유 문장이라 <pre> 에 넣지 않는다 — 고정폭 블록은
    긴 줄에 가로 스크롤이 붙어 오히려 읽기 불편해진다.
    """
    return card("⚙️ CryptoSplit 설정 변경",
                foot="\n".join(f"· {c}" for c in changes))
