# CryptoSplit — 프로젝트 컨텍스트

## 프로젝트 개요
- 빗썸 거래소에서 **BTC, ETH, XRP, USDT** (KRW 페어) 를 대상으로
  매직스플릿(분할매수/분할매도) 전략을 자동 실행하는 Python 봇
- 웹 대시보드(Flask)로 모바일/PC에서 설정 및 모니터링
- 텔레그램으로 매매 알림 수신 + 원격 명령어 제어

## 실행 흐름 (중요)
```
main.py 실행
  → DB 초기화 + 무결성 검사 + 시작 백업 1회
  → 시계 동기화 1회 (Windows NTP)
  → BotManager / Notifier / Scheduler / Flask / CmdHandler 초기화
  → Flask 웹서버 시작 (포트 5000), 봇은 중지 상태로 대기
  → APScheduler 자동 시작 (정각 현황 / 00시 결산 / 6h DB백업 / 3분 워치독)

사용자가 브라우저에서 접속 (ZeroTier VPN 경유, 아래 "웹 대시보드" 참고)
  → 전략 탭에서 파라미터 확인 및 저장
  → 홈에서 [시작] 버튼 클릭 → 확인 팝업 → 봇 Thread 시작, 매매 가동

사용자가 홈에서 [중지] 버튼 클릭
  → 확인 팝업 → 봇 Thread 중지, 미체결 주문 취소
```

## 핵심 매매 방식
1초마다 현재가 조회 → 트리거 조건 판단 → 주문 실행
(미리 주문 올려두는 방식 X, 트리거 감지 후 즉시 주문 방식 O)

### 트리거 모드 (2가지)
- **pct 모드**: 기준가 대비 퍼센트 변동으로 트리거 계산
- **krw 모드**: 고정 금액 단위로 트리거 계산 (현재 기본 모드)

### 매수 조건
- pct: 현재가 ≤ 기준가 × (1 - trigger_pct%) 이면 매수
- krw: 현재가 ≤ 기준가 − trigger_krw 이면 매수

### 매도 조건
- pct: 현재가 ≥ 기준가 × (1 + trigger_pct_sell% + 0.08%) 이면 매도
- krw: 현재가 ≥ 기준가 + trigger_krw_sell 이면 매도
(수수료 0.04% × 2회 반영, 반드시 수익이 나는 가격에만 매도)

### 매수/매도 간격 독립 설정
- `trigger_krw_sell`/`trigger_pct_sell`을 매수(`trigger_krw`/`trigger_pct`)와 별도로
  설정 가능 — 예: 기준가 10,000원에서 매수간격 100원/매도간격 200원이면 매수트리거
  9,900원, 매도트리거 10,200원
- 두 값 모두 캐치업(재시작 시 갭 채우기)에는 영향 없음 — 캐치업은 항상 매수
  간격만 사용(매도가 발생하지 않는 로직이므로)
- 매도 간격 미설정/0이면 매수 간격과 동일하게 적용(기존 대칭 동작과 100%
  호환) — pct 모드에서 매수 간격은 ATR 기반으로 동적 조정되는데
  (`strategy/volatility.py`), 매도 간격을 비워두면 이 동적값을 그대로 따라가고
  명시적으로 설정하면 고정값으로 동작함

### 체결 후 처리
- 매수 체결: 차수 +1, 기준가 = 매수 체결가
- 매도 체결: 차수 -1, 기준가 = 매도 체결가
- 최대 차수 도달 시: 매수 중단, 매도만 유지, 텔레그램 알림
- 미체결 주문 300초 초과 시: 자동 취소 → 부분 체결 여부 확인 후 재주문

### 서킷브레이커
- `circuit_breaker_pct`% 이상 급변이 `circuit_breaker_window`초 내 발생 시 매수 자동 중단
- 300초 후 자동 해제 (또는 봇 재시작 시 해제)

### 자동 시작가 트레일링 (래칫, 선택 기능)
- 배경: 0차(무포지션) 상태의 매수 조건은 "현재가 ≤ 시작가"뿐이라, 가격이
  시작가 위로 올라간 채 안 내려오면 봇이 영원히 대기만 함(현물이라 숏
  불가 — 그 상승분 자체는 취할 수 없음). 매도로 0차에 복귀할 때마다
  기준가는 리셋되고 오래된 고정 `start_price`로 재초기화되는 구조라 급등
  후 반복적으로 재발함
- 심볼별 `auto_rebase_enabled: true`로 켜면(기본 false), 0차이고 현재가가
  시작가보다 위일 때 `auto_rebase_interval_minutes`(기본 30분, 전역 설정)
  주기마다 조건을 재확인해 **정확히 매수 간격 1칸만큼만** 시작가·기준가를
  올림(`strategy/magic_split.py::_maybe_auto_rebase`) — 한 번에 관측된
  고점까지 몰아서 올리지 않는 래칫 방식이라, 반짝 급등 후 바로 급락해도
  노출되는 손해가 "경과한 주기 수 × 간격" 이하로 제한됨
- 포지션이 없는 0차에서만 개입하므로 자금 리스크 없음, 1차 이상 진행
  중인 그리드의 매수/매도 판단에는 전혀 영향 없음. 켜져 있어도 가동 중
  즉시 반영(재시작 불필요), 텔레그램 알림 발송(`send_auto_rebase`)

### 캐치업 / 가상 캐치업
- 봇 재시작 후 현재가와 시작가 사이에 갭이 있으면 캐치업 매수 실행
  - **실제 캐치업**: 갭 레벨 수만큼 실제 매수 주문 연속 실행
  - **가상 캐치업**: 실제 매수 없이 차수 기록만 생성 (virtual_catchup=true)

## 사용자 설정 가능 항목 (전략 탭에서 변경)
- 트리거 모드 (pct / krw)
- 트리거 간격 (기본: krw=15,000원 / pct=0.5%) — 가동 중에도 즉시 반영(재시작 불필요)
- 매도 간격 (선택, 미설정 시 매수 간격과 동일) — 매수/매도를 독립적으로 설정 가능,
  가동 중에도 즉시 반영(재시작 불필요)
- 최대 차수 (기본값: 50차) — 가동 중에도 즉시 반영(재시작 불필요)
- 1회 주문금액 (기본값: 10,000원) — 가동 중에도 즉시 반영(재시작 불필요)
- 시작가 (수동 입력 가능)
  - **차수 0(매수 전)**: 저장 즉시 반영(재시작 불필요) — 기준가도 함께 갱신
  - **차수 > 0(매수 진행 중)**: 즉시 반영 안 됨. 이미 실제 매수된 차수 뒤에
    새 시작가 그리드를 안전하게 이어붙일 방법이 없어서(차수-가격 역전 또는
    실제 원가 왜곡) 저장한 값은 DB에만 예약되고 다음에 전량 매도돼 0차로
    복귀했을 때 적용됨. 지금 바로 반영하고 싶으면 전략 탭의 **"시작가
    재조정"** 버튼(중단→저장→초기화→가상/실제 캐치업 재시작을 한 번에 진행)
    사용
- 자동 시작가 트레일링 사용 여부 (기본값: 꺼짐) — 가동 중에도 즉시 반영
  (재시작 불필요). 판단 주기(분)는 설정 탭에서 전역으로 조정 (기본 30분)

## 기술 스택
- 언어: Python
- 거래소 API: python-bithumb (BithumbClient 래퍼)
- 웹: Flask + Bootstrap 5 (라이트/토스 스타일 디자인 시스템, Pretendard 폰트, 반응형, 모바일 최적화)
- DB: SQLite (WAL 모드, 자동 백업)
- 알림: 텔레그램 Bot API (큐 기반 비동기, rate-limit 처리)
- 스케줄러: APScheduler (BackgroundScheduler)
- 실행환경: Windows Server 2012 R2 — "몽클라우드"(국내 클라우드PC 임대 서비스)에서 월 비용을 지불하고 임대한 클라우드PC 1대에서 24시간 상시 구동
- 개발환경: 개인 PC → GitHub 커밋 → 클라우드 PC pull → 실행

## 폴더 구조
```
crypto-split-bot/
├── .env                       # API 키, 텔레그램 토큰 (git 제외)
├── .env.example
├── .gitignore
├── requirements.txt
├── CLAUDE.md
├── main.py                    # 진입점
├── bot_manager.py             # 봇 생명주기 관리 (start/stop/reset/health)
├── config/
│   ├── __init__.py
│   ├── settings.default.yaml  # 기본 설정 템플릿
│   ├── settings.yaml          # 실제 운영 설정 (git 제외)
│   └── loader.py              # 싱글턴 설정 로더 + .env 주입
├── exchange/
│   ├── __init__.py
│   ├── base.py
│   ├── bithumb_client.py      # 빗썸 API 래퍼 (주문/잔고/현재가)
│   └── retry.py               # API 재시도 데코레이터
├── strategy/
│   ├── __init__.py
│   ├── magic_split.py         # 핵심 매매 전략 로직 ★변경 최소화
│   ├── trigger.py             # 트리거 가격 계산 (pct/krw)
│   ├── circuit_breaker.py     # 급락 감지 서킷브레이커
│   └── volatility.py          # 변동성 계산
├── order/
│   ├── __init__.py
│   ├── executor.py
│   ├── tracker.py
│   └── canceller.py
├── portfolio/
│   ├── __init__.py
│   ├── balance.py
│   └── position.py
├── database/
│   ├── __init__.py
│   ├── connection.py          # DB 연결 + backup_database() + check_integrity()
│   ├── models.py              # DDL + 마이그레이션
│   ├── order_repo.py          # 주문 CRUD
│   ├── state_repo.py          # bot_state 읽기/쓰기
│   ├── pnl_repo.py            # 손익 집계
│   └── auth_repo.py           # 로그인 브루트포스 방어 상태 영속화
├── notification/
│   ├── __init__.py
│   ├── telegram.py            # 큐 기반 비동기 발송 (TelegramNotifier)
│   ├── message_builder.py     # 메시지 포맷 함수 모음 (순수 함수)
│   ├── scheduler.py           # APScheduler 잡 (현황/결산/백업/워치독/시계동기화)
│   └── telegram_handler.py    # 텔레그램 명령어 수신 (long-polling)
├── web/
│   ├── __init__.py
│   ├── app.py
│   ├── auth.py                # 접근 보안 (로그인/CSRF/화이트리스트/재인증/브루트포스)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py       # 페이지 라우트
│   │   └── api.py             # REST API 엔드포인트
│   ├── templates/
│   │   ├── base.html
│   │   └── dashboard.html
│   └── static/
│       ├── css/style.css
│       ├── images/
│       └── js/
│           ├── dashboard.js
│           └── chart.umd.min.js  # Chart.js v4.4.1 로컬 번들
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   ├── time_utils.py
│   ├── time_sync.py           # Windows NTP 시계 동기화
│   └── math_utils.py
├── tests/
│   ├── __init__.py
│   └── test_stage1~11.py
└── data/
    ├── cryptosplit.db         # SQLite DB
    └── backups/               # 자동 백업 파일 (최대 14개)
```

## settings.yaml 구조
```yaml
exchange:
  name: bithumb
  membership: 화이트       # 화이트/일반 (수수료율에 영향)

pairs:
  ETH:
    ticker: KRW-ETH
    enabled: true
    capital: 2500000
    order_amount: 10000
    max_rounds: 50
    trigger_pct: 0.5
    trigger_mode: krw       # pct | krw
    trigger_krw: 15000.0
    trigger_pct_sell: 0.5      # 매도 전용(선택). 미설정/0이면 trigger_pct와 동일
    trigger_krw_sell: 15000.0  # 매도 전용(선택). 미설정/0이면 trigger_krw와 동일
    auto_rebase_enabled: false # 자동 시작가 트레일링(래칫) 사용 여부, 기본 꺼짐
  USDT:
    ticker: KRW-USDT
    enabled: true
    capital: 2500000
    order_amount: 10000
    max_rounds: 50
    trigger_pct: 0.5
    trigger_mode: krw
    trigger_krw: 10.0
  # BTC, XRP 동일 구조

strategy:
  trigger_min_pct: 0.3
  trigger_max_pct: 1.0
  sell_extra_pct: 0.08      # 수수료 커버 추가 매도 여유 (%)
  circuit_breaker_pct: 5.0  # 급변 감지 임계값 (%)
  circuit_breaker_window: 300
  order_timeout: 300        # 미체결 자동취소 시간 (초)
  price_poll_interval: 1    # 현재가 조회 주기 (초)
  catchup_max: 10           # 캐치업 최대 실행 횟수
  auto_rebase_interval_minutes: 30  # 자동 시작가 트레일링 판단 주기 (분)

database:
  path: data/cryptosplit.db

telegram:
  enabled: true

web:
  host: 0.0.0.0
  port: 5000

logging:
  level: INFO
  dir: logs
  max_bytes: 10485760
  backup_count: 5

report:
  hour: 0
  minute: 0
  hourly_interval_hours: 1  # 정각 현황 알림 간격(시간, 0시는 항상 제외) — 설정 탭에서 즉시 반영(재시작 불필요)
  hourly_enabled: true      # 정각 현황 알림 사용 여부 — 설정 탭 토글로 즉시 반영(재시작 불필요)

system:
  time_sync_interval_hours: 6  # 설정 탭에서 즉시 반영(재시작 불필요)
  db_backup_interval_hours: 6
  db_backup_keep: 14
  watchdog_interval_min: 3
```

## DB 테이블
### orders
- id, symbol, order_id(UNIQUE), side(buy/sell)
- price, quantity, amount, status(open/filled/cancelled)
- round_no, filled_price, filled_at, is_catchup
- created_at, updated_at

### bot_state
- symbol (PK), reference_price, start_price, current_round
- is_running, circuit_breaker_on, cb_until, updated_at

### daily_pnl
- id, date, symbol(UNIQUE per date)
- realized_pnl, trade_count, fee_total, point_total

### manual_avg_prices
- symbol (PK), avg_buy_price, updated_at
  (봇 매매 이력 없는 코인에 수동 평균매수가 입력용)

### asset_snapshots
- date (PK), total_assets, krw, holdings(JSON), created_at
  (일별 보유자산 스냅샷 — 매일 23:59 자동 기록(notification/scheduler.py::
  _job_asset_snapshot) + 스케줄러 시작 시 1회 즉시 기록. 실현손익만으로는
  보유 코인 평가금액 등락(미실현 손익)이 반영되지 않아, 원금이 실제로 얼마나
  늘고 줄었는지 보려면 이 총자산 추이를 봐야 함)

### login_attempts
- ip (PK), fail_count, window_start, locked_until, updated_at
  (웹 로그인 브루트포스 방어 — 앱 재시작 후에도 잠금 상태 유지)

### membership_history
- effective_date (PK), membership
  (멤버십 등급 변경 이력 — 설정 탭에서 등급을 바꾸면 저장 시점과 무관하게
  항상 "이번 달 1일"을 effective_date로 기록(사실상 월 단위 이력). 사용자가
  월초 등급 변경을 깜빡하고 월 중순에야 저장해도 그 달 1일~저장일 거래가
  새 등급으로 소급 적용되도록 하기 위함. 손익 재계산 시 "현재" 등급을 과거
  전체에 소급 적용하지 않고 거래 체결월 기준으로 그 시점 등급을 조회하기
  위함. 최초 실행 시 이력이 비어 있으면 그 시점의 설정값을 과거 전체
  기본값(effective_date='2000-01-01')으로 1건 자동 기록함 — 이 기능 도입
  이전 과거는 정확한 이력이 없어 최선으로 근사)

## 텔레그램 알림 항목
- 봇 시작 / 재시작 / 중지
- 매수 체결 / 매도 체결 (차수/수익 포함)
- 최대 차수 도달 (매수 중단)
- 미체결 자동취소 + 재주문 안내
- 부분 체결 정산 (타임아웃 취소 시 부분체결 분 처리 결과)
- 캐치업 / 가상 캐치업 완료
- 자동 시작가 트레일링(래칫) 발동 (심볼별, 변경 전→후 시작가 표시)
- 전략 탭 설정 변경 (트리거/최대차수/주문금액/시작가, 심볼별, 변경 전→후 값 표시)
- 설정 탭 설정 변경 (변경 전→후 값 표시. API키/텔레그램/비밀번호 등 민감정보는
  항목명만 표시하고 값은 비공개)
- API 오류
- 봇 워치독 자동 복구 / 복구 실패 (3분마다 점검)
- DB 백업 실패 경고 (성공은 무알림)
- 시간 동기화 결과

- 정각 가동 현황 리포트 (running 봇만 표시, 기본 매시간(0시 제외) — 설정 탭에서
  간격(시간) 조정 및 사용 여부(토글) 설정 가능)
- 일일 결산 리포트 (00:00, 전날 기준)

## 텔레그램 명령어
```
/help          — 명령어 목록
/status        — 차수·트리거·수익 상세 현황
/balance       — 잔고 + 코인 평가금액
/start ETH     — ETH 새로 시작 (초기화)
/resume ETH    — ETH 재시작 (이전 상태 복원)
/stop ETH      — ETH 중지 + 미체결 취소
/report        — 오늘 결산 즉시 전송
/stopall       — 전체 봇 중지 + 미체결 취소
/resumeall     — 중지된 전체 봇 재시작 (상태 복원)
```

## 웹 대시보드
접속: `http://<클라우드PC의 ZeroTier IP>:5000` (예: `http://10.73.56.28:5000`)

> **외부 접속 방식 (2026-07-10 확정)**: 몽클라우드가 고객 VM에 인바운드 포트 개방 기능을 제공하지
> 않아(저가 클라우드PC 임대 서비스, 커스텀 방화벽/보안그룹 설정 불가), 클라우드PC의 공인 IP로는
> 외부에서 절대 접속할 수 없다. 대신 **ZeroTier**(무료 P2P VPN 메시)로 클라우드PC·로컬PC·폰을
> 사설 가상망(`10.73.56.0/24`)으로 묶어서 그 안에서만 접속하는 구조로 해결함.
> - ZeroTier 네트워크: `cryptosplit-net` (Network ID는 ZeroTier Central 콘솔에서 확인)
> - 클라우드PC(Windows Server 2012 R2)는 **ZeroTier 1.6.6**(구버전)만 설치 가능 — 2023년 Go
>   런타임이 Windows Server 2012/2012 R2 지원을 끊으면서 Tailscale·Cloudflare Tunnel·최신
>   ZeroTier가 전부 이 OS에서 동작 안 함(공통 원인). 1.6.6은 더 이상 패치되지 않는 구버전이므로,
>   Windows 방화벽 인바운드 규칙(TCP 5000)의 원격 IP 범위를 `10.73.56.0/24`로 제한해 ZeroTier
>   가상망 밖 트래픽은 차단해둠 — 공인 인터넷에는 노출되지 않음.
> - 새 기기(추가 폰/PC)에서 접속하려면: ZeroTier 앱 설치 → 같은 Network ID로 Join → ZeroTier
>   Central(my.zerotier.com)에서 Authorize 필요.
> - 텔레그램 명령어(`telegram_handler.py`, long-polling)는 이 문제와 무관하게 항상 정상 동작 —
>   봇이 텔레그램 서버로 아웃바운드 연결만 하기 때문.

하단 네비 4탭 구조 — 각 탭이 하나의 작업(job)에 대응하도록 재설계됨
(2026-07-09 IA 재설계). 서브탭 없는 탭은 별도 표기 없음.

### 홈 — 운영 현황 (모니터링 + 제어, 서브탭 없음)
- 상단 KPI 스트립: 오늘 매수금액 / 오늘 매도금액 / 오늘 총손익
- 전체 가동/중지 바 (ON 종목만 대상, 종목별 전체제어 ON/OFF 토글)
- 코인별 모니터링 카드: 현재가·기준가·시작가·차수 진행바·매수/매도 트리거
  (트리거 임박 게이지 포함)·미체결 주문·오늘 순수익·[신규시작]/[이어서시작]/
  [초기화]/[자동매매 중지] 버튼(확인 팝업 후 실행)

### 전략 — 설정 (심볼별 탭)
- 전략 파라미터(트리거 방식/간격, 최대 차수, 1회 주문금액, 시작가) 확인 및 저장
  — 트리거/주문금액/최대차수는 가동 중에도 즉시 반영, 시작가는 차수 0일 때만
  즉시 반영(차수>0이면 "시작가 재조정" 버튼으로 중단→저장→초기화→재시작을
  한 번에 진행)
- 그리드 플래너(회차별 매수가/매도가 표)

### 손익 — 분석 + 내역 + 자산 (서브탭 4개)
1. **요약**: 총 매수/매도금액, 누적손익/수익률, 오늘·이번주 손익(전일/전주
   대비 비교), 일평균수익/일완료횟수/평균손익/총거래수, 일별 손익 차트,
   시간대별/요일별 수익 분포
2. **주문체결 내역**: 기간·코인 필터(프리셋 저장 가능) + CSV 내보내기
3. **실현손익 내역**: 차수별 실현손익, 누적 거래금액(가상 캐치업 매수는
   제외하고 집계) + CSV 내보내기
4. **보유자산**: 빗썸 보유 전체 자산 (KRW + 코인 평가액, 수동 평균매수가 입력),
   전일/전주 대비 + 일별 보유자산(원금) 변동 추이 차트(매일 23:59 자동 스냅샷)

체결/실현손익 두 서브탭은 필터바 공유(기간 퀵버튼/코인 선택/프리셋 저장,
삭제모드) — 기간 전체 삭제는 재인증(비밀번호 재확인) 필요.

### 설정 탭
- 접근 보안 (접속 비밀번호, IP 화이트리스트 — 단일 IP 또는 CIDR 표기 지원)
- API 키 / 텔레그램 설정 / 미체결 취소 시간 / 결산 시각
- 정각 현황 알림 사용 여부(토글) / 간격 / 시계 동기화 간격 (시간 단위, 1~23 —
  저장 즉시 반영, 재시작 불필요). 알림은 봇 가동 여부와 무관하게 항상 이
  간격대로 발송되므로, 전 종목이 매도 완료 등으로 매매가 멈춰 있을 때
  알림만 끄고 싶으면 이 토글 사용. 간격에 무관하게 00시 정각은 항상
  제외됨(00시 결산 리포트와 중복 방지)
- 텔레그램 테스트 메시지 발송

## REST API (/api/...)
> 로그인 인증이 활성 상태(비밀번호 설정됨)면, 상태 변경(POST/PUT/PATCH/DELETE)
> 요청은 전부 `X-CSRF-Token` 헤더가 필요하다(불일치/누락 시 403). 프론트엔드는
> `_apiFetch()` 헬퍼가 자동으로 부여한다 — 새 상태변경 엔드포인트를 추가하면
> 프론트에서 반드시 이 헬퍼를 통해 호출할 것(직접 `fetch()` 쓰면 403 남).

```
GET  /api/status              → 전체 봇 상태 + 오늘 손익
GET  /api/portfolio           → KRW + 코인 잔고 스냅샷
GET  /api/market/prices       → BTC/ETH/XRP/USDT 현재가 (4초 캐시)
GET  /api/investment          → 심볼별 포지션 평가
GET  /api/pnl/today           → 오늘 손익 + 오늘 매수/매도 총액(홈 KPI 스트립용,
                                 가상 캐치업 매수 제외) + net_pnl
GET  /api/pnl/cumulative      → 누적 손익 (총 매수/매도금액은 실제 체결액 그대로,
                                 실현손익은 get_pnl_rounds() 차수별 합산 — 단순
                                 총매도-총매수가 아님. 시작가를 현재가보다 높게 잡아
                                 가상 캐치업이 잦은 경우 단순 차감식은 손익이
                                 과대계상되는 문제가 있어 교체함. fee/points 필드 포함)
GET  /api/pnl/daily?days=30   → 일별 손익 시계열 (차트용, days=0은 전체기간)
GET  /api/pnl/hourly?days=30  → 시간대별 평균손익
GET  /api/pnl/weekday?days=30 → 요일별 평균손익
GET  /api/pnl/stats?days=30   → 승률/프로핏팩터 등 성과 통계
GET  /api/strategy            → 심볼별 전략 파라미터 + 모니터링 (홈+전략 공용)
GET  /api/assets/exchange     → 빗썸 보유 자산 전체 현황
GET  /api/assets/history      → 일별 보유자산(원금) 변동 추이 (days=0은 전체기간,
                                 매일 23:59 자동 스냅샷)
GET  /api/bot/gap?symbol=ETH  → 재시작 전 캐치업 갭 미리보기
GET  /api/settings            → 현재 설정값 (API 키/화이트리스트 마스킹)
GET  /api/trades/fills        → 기간별 체결 주문 내역 (is_virtual: 가상 캐치업 매수 여부)
GET  /api/trades/pnl          → 기간별 차수별 실현손익 (buy_is_virtual, fee, points 포함 —
                                 포인트 적립률은 매도 체결일 기준 membership_history 조회값,
                                 "현재" 설정을 과거에 소급 적용하지 않음)

POST /api/bot/start           → 봇 시작 {symbol, fresh, virtual_catchup}
POST /api/bot/stop            → 봇 중지 + 미체결 취소 {symbol}
POST /api/bot/reset           → 차수/기준가만 리셋 (봇 시작 없음) {symbol}
POST /api/bot/stop_all        → 전체 봇 중지 {_reauth_password} — 재인증 필요
POST /api/bot/start_all       → 전체 봇 재시작 {_reauth_password} — 재인증 필요
POST /api/bot/bulk_enabled    → 종목별 전체제어 ON/OFF {symbol, enabled}
POST /api/strategy/settings   → 전략 설정 저장 (trigger_pct_sell/trigger_krw_sell로
                                 매도 간격 독립 설정, 미설정/0이면 매수와 동일 /
                                 auto_rebase_enabled로 자동 시작가 트레일링 온오프).
                                 가동 중이면 max_rounds/order_amount/trigger/
                                 auto_rebase_enabled는 즉시 반영, start_price는
                                 차수 0일 때만 즉시 반영(차수>0이면 DB 예약, 메시지로
                                 구분 안내)
POST /api/settings            → 시스템 설정 저장 (ip_whitelist는 IP 또는 CIDR
                                 형식 검증, 잘못된 형식이면 400 / hourly_interval_hours·
                                 time_sync_interval_hours는 1~23 범위 검증 후 스케줄러에
                                 즉시 반영, 재시작 불필요 / hourly_enabled로 정각 현황
                                 알림 자체를 켜고 끌 수 있음, 스케줄러 pause_job/
                                 resume_job으로 즉시 반영 / auto_rebase_interval_minutes는
                                 5~240 범위 검증 후 즉시 반영, 재시작 불필요)
POST /api/settings/telegram/test → 텔레그램 테스트 메시지
POST /api/assets/manual-price → 수동 평균매수가 저장

DELETE /api/trades/orders     → 주문 내역 삭제 (ids 지정 시 선택삭제, start/end
                                 기간 지정 시 {_reauth_password} 재인증 필요)
```

## 웹 접근 보안 (`web/auth.py`)
- **비밀번호 로그인**: `.env`의 `WEB_PASSWORD` 설정 시에만 활성화(fail-open —
  미설정 시 인증 자체가 비활성되어 자기잠금 방지). `hmac.compare_digest`로
  타이밍 공격 방어
- **IP 화이트리스트**: 단일 IP 또는 CIDR(`10.73.56.0/24`) 표기 지원. 목록이
  비어있으면 전체 허용, `127.0.0.1`은 항상 허용
- **CSRF 토큰**: 로그인 시 세션에 발급/회전, 모든 페이지에 `<meta
  name="csrf-token">`으로 주입. 인증된 세션의 상태변경 API 요청은
  `X-CSRF-Token` 헤더 검증 필수(REST API 섹션 참고)
- **세션 무효화**: 비밀번호 변경 시 세션에 저장된 지문(pw_fp)이 불일치하게
  되어 다른 브라우저의 기존 세션도 다음 요청에서 자동 로그아웃됨
- **민감 작업 재인증(step-up)**: 전체 시작/중지, 주문 기간 전체 삭제는 요청에
  현재 비밀번호(`_reauth_password`)가 필요. 기존 확인모달에 비밀번호 입력칸을
  조건부로 노출해서 처리(새 모달 없음)
- **브루트포스 방어**: IP당 15분 내 5회 실패 시 10분 잠금, `login_attempts`
  테이블에 영속화(앱 재시작해도 잠금 유지), 잠금 발동 시 텔레그램 1회성 알림
- 이 모든 계층은 `web/auth.py`/`web/routes/api.py`에만 있고 `strategy/*.py`와는
  무관함

## main.py 실행 구조
```
main.py
  → get_config() — settings.yaml + .env 로드
  → db_configure() + create_tables() — DB 초기화/마이그레이션
  → check_integrity() — DB 무결성 검사 (실패 시 텔레그램 경고)
  → backup_database() — 시작 시 백업 1회
  → sync_windows_time() — NTP 동기화 1회
  → build_components()
      BithumbClient, TelegramNotifier, BotManager
      NotificationScheduler (APScheduler 시작)
      create_app() — Flask 앱
      TelegramCommandHandler (scheduler 주입)
  → sched.start(), cmd_handler.start()
  → app.run() — 메인 스레드 블록
```

## 개발 주의사항

### ★ 최우선: 자동매매 로직 변경 최소화
> 현재 자동매매가 안정적으로 실행 중임. 업그레이드 시 **`strategy/magic_split.py`,
> `strategy/trigger.py`, `strategy/circuit_breaker.py`** 의 매매 로직 핵심 코드는
> 꼭 필요한 경우가 아니면 변경하지 말 것.
> 웹/알림/DB/스케줄러 레이어 개선을 우선적으로 진행할 것.

### 기본 원칙
- main.py 실행 시 봇이 자동으로 시작되면 안 됨 — 반드시 웹 또는 텔레그램으로 수동 시작
- 시작/중지 버튼은 반드시 확인 팝업(confirm modal) 후 실행
- 각 파일은 단일 책임 원칙 준수
- API 키는 .env에서만 로드, 코드 하드코딩 금지
- 모든 예외는 try/except + 로그 기록
- 주문 실행 전 반드시 잔고 확인
- BTC/ETH/XRP/USDT 봇은 완전히 독립적으로 동작
- Windows 호환성 유지 (클라우드 PC 환경)
- 봇 재시작 시 DB에서 이전 상태(기준가/차수) 자동 복구
- 코드 커밋 후 반드시 git push까지 완료 (클라우드 PC에서 pull로 반영)

### 알려진 이슈
- `pytest tests/`를 실행하면 이번 세션 이전부터 있던 사전 실패가 항상 52~53개
  나온다(test_stage1/2/3/5/8/9/11.py, 코드 드리프트로 인한 낡은 assertion
  추정, 원인 분석 미착수). `test_stage5.py`는 실행마다 5개 또는 6개로 오가는
  flaky 상태 — 새 실패가 이 범위 안이면 회귀가 아니라 기존 이슈일 가능성이 큼.
