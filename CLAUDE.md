# CryptoSplit — 프로젝트 컨텍스트

## 프로젝트 개요
- 빗썸 거래소에서 ETH/KRW, USDT/KRW 페어를 대상으로
  매직스플릿(분할매수/분할매도) 전략을 자동 실행하는 Python 봇
- 웹 대시보드(Flask)로 모바일/PC에서 설정 및 모니터링
- 텔레그램으로 매매 알림 수신

## 실행 흐름 (중요)
```
main.py 실행
  → Flask 웹서버만 시작 (포트 5000)
  → 봇은 중지 상태로 대기

사용자가 브라우저에서 접속
  → 설정 페이지에서 전략 파라미터 입력 후 저장
  → 대시보드에서 [시작] 버튼 클릭
  → 확인 팝업 ("ETH 봇을 시작하시겠습니까?") → 확인
  → 봇 Thread 시작, 매매 가동

사용자가 [중지] 버튼 클릭
  → 확인 팝업 ("ETH 봇을 중지하시겠습니까?") → 확인
  → 봇 Thread 중지, 미체결 주문 취소
```

## 핵심 매매 방식
1초마다 현재가 조회 → 트리거 조건 판단 → 주문 실행
(미리 주문 올려두는 방식 X, 트리거 감지 후 즉시 주문 방식 O)

### 매수 조건
현재가 ≤ 기준가 × (1 - 트리거%) 이면 매수 주문 실행

### 매도 조건
현재가 ≥ 기준가 × (1 + 트리거% + 0.08%) 이면 매도 주문 실행
(수수료 0.04% × 2회 반영, 반드시 수익이 나는 가격에만 매도)

### 체결 후 처리
- 매수 체결: 차수 +1, 기준가 = 매수 체결가
- 매도 체결: 차수 -1, 기준가 = 매도 체결가
- 최대 차수 도달 시: 매수 중단, 매도만 유지, 텔레그램 알림
- 미체결 주문 300초 초과 시: 자동 취소 후 재주문

## 사용자 설정 가능 항목 (웹 설정 페이지에서 변경)
- 트리거 간격 % (기본값: 0.5%)
- 최대 차수 (기본값: 50차)
- 1회 주문금액 (기본값: 50,000원)
- 미체결 주문 자동취소 시간 (기본값: 300초)

## 기술 스택
- 언어: Python
- 거래소 API: python-bithumb
- 웹: Flask + Bootstrap 5 (다크모드, 반응형)
- DB: SQLite
- 알림: 텔레그램 Bot API
- 실행환경: Windows (클라우드 PC, Windows 8)
- 개발환경: 개인 PC → GitHub 커밋 → 클라우드 PC pull → 실행

## 폴더 구조
```
crypto-split-bot/
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── CLAUDE.md
├── main.py
├── config/
│   ├── __init__.py
│   ├── settings.yaml
│   └── loader.py
├── exchange/
│   ├── __init__.py
│   ├── base.py
│   ├── bithumb_client.py
│   └── retry.py
├── strategy/
│   ├── __init__.py
│   ├── magic_split.py
│   └── trigger.py
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
│   ├── connection.py
│   ├── models.py
│   ├── order_repo.py
│   ├── state_repo.py
│   └── pnl_repo.py
├── notification/
│   ├── __init__.py
│   ├── telegram.py
│   └── message_builder.py
├── web/
│   ├── __init__.py
│   ├── app.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py
│   │   ├── settings.py
│   │   └── api.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   └── settings.html
│   └── static/
│       ├── css/style.css
│       └── js/
│           ├── dashboard.js
│           └── settings.js
└── utils/
    ├── __init__.py
    ├── logger.py
    ├── time_utils.py
    └── math_utils.py
```

## settings.yaml 기본값
```yaml
eth:
  ticker: "KRW-ETH"
  enabled: true
  total_capital_krw: 2500000
  order_amount_krw: 50000
  max_splits: 50
  trigger_pct: 0.5

usdt:
  ticker: "KRW-USDT"
  enabled: true
  total_capital_krw: 2500000
  order_amount_krw: 50000
  max_splits: 50
  trigger_pct: 0.5

strategy:
  fee_rate: 0.0004
  cancel_timeout_sec: 300
  loop_interval_sec: 1

notification:
  enabled: true
  daily_report_hour: 23

logging:
  level: "INFO"
  file: "logs/bot.log"
```

## DB 테이블
- orders: id, uuid, ticker, side, split_no, price, volume,
          amount_krw, state(wait/done/cancel), created_at, updated_at
- bot_state: key, ticker, value, updated_at
  (저장항목: base_price, is_running, current_splits, last_trade_time)
- daily_pnl: date, ticker, realized_pnl, trade_count, fee_total, net_pnl

## 텔레그램 알림 항목
- 매수 체결: 코인/가격/수량/금액/차수/시각
- 매도 체결: 코인/가격/수량/수익금/수익률/차수/시각
- 최대 차수 도달: 코인/현재차수/매수중단 안내
- 미체결 자동취소: 코인/취소된주문/재주문 안내
- API 오류: 오류내용/재시도횟수

- 매 정각 가동 현황 리포트:
  🟢 CryptoSplit 가동중 [00:00]
  ├ ETH: 실행중 | 현재가 3,245,000원 | 현재차수 3차
  ├ ETH: 오늘수익 +12,400원 | 거래 24회
  ├ USDT: 실행중 | 현재가 1,385원 | 현재차수 1차
  └ USDT: 오늘수익 +3,200원 | 거래 8회

- 일일 결산 리포트 (매일 00시):
  📊 CryptoSplit 일일 결산 [2025-01-15]
  ─────────────────
  [ETH]
  실현수익: +38,200원
  거래횟수: 87회
  수수료합계: -4,350원
  순수익: +33,850원
  ─────────────────
  [USDT]
  실현수익: +12,400원
  거래횟수: 31회
  수수료합계: -1,240원
  순수익: +11,160원
  ─────────────────
  전체 순수익: +45,010원
  KRW잔고: 512,000원
  ETH보유: 0.142 ETH
  USDT보유: 1,850 USDT

## 웹 대시보드 상세
접속: http://[클라우드PC_IP]:5000

### 대시보드 페이지 (/)
- ETH / USDT 탭 분리
- 봇 상태 표시 (중지 / 실행중 / 최대차수도달)
- 현재가 및 등락률 (5초마다 자동 갱신)
- 현재 차수 / 최대 차수
- 오늘 수익 / 거래 횟수
- KRW 잔고 / 코인 보유량
- [시작] 버튼:
    클릭 시 확인 팝업 ("ETH 자동매매를 시작하시겠습니까?")
    확인 → 봇 시작
- [중지] 버튼:
    클릭 시 확인 팝업 ("ETH 자동매매를 중지하시겠습니까?
                        미체결 주문이 자동으로 취소됩니다.")
    확인 → 봇 중지 + 미체결 주문 전량 취소
- 최근 거래 내역 (최근 20건, 차수 표시)

### 설정 페이지 (/settings)
- ETH / USDT 각각 독립 설정
- 입력 항목:
    트리거 간격 (%), 최대 차수, 1회 주문금액 (원),
    미체결 취소 시간 (초), 텔레그램 알림 on/off
- 자동 계산 표시:
    총 필요자금 = 최대차수 × 1회주문금액
    최대 하락 커버 = 트리거% × 최대차수
- [저장] 버튼: settings.yaml 즉시 반영

### REST API (/api/...)
- GET  /api/status          → 전체 봇 상태 JSON
- GET  /api/status/{ticker} → 특정 코인 상태 JSON
- GET  /api/trades          → 최근 거래 내역 JSON
- GET  /api/pnl             → 오늘/누적 손익 JSON
- POST /api/bot/start       → 봇 시작 (ticker 지정)
- POST /api/bot/stop        → 봇 중지 + 미체결 취소
- POST /api/settings        → 설정 저장 및 즉시 반영

## main.py 실행 구조
```
main.py 실행
  → BotManager 초기화 (ETH/USDT 봇 인스턴스 생성, 중지 상태)
  → Flask 웹서버 시작 (포트 5000)
  → 웹에서 시작 요청 시 해당 봇 Thread 시작
  → 웹에서 중지 요청 시 해당 봇 Thread 중지
```

## 개발 주의사항
- main.py 실행 시 봇이 자동으로 시작되면 안 됨
  반드시 웹 대시보드에서 사용자가 수동으로 시작
- 시작/중지 버튼은 반드시 확인 팝업(confirm modal) 후 실행
- 각 파일은 단일 책임 원칙 준수
- API 키는 .env에서만 로드, 코드 하드코딩 금지
- 모든 예외는 try/except + 로그 기록
- 주문 실행 전 반드시 잔고 확인
- ETH/USDT 봇은 완전히 독립적으로 동작
- Windows 8 호환성 유지
- 봇 재시작 시 DB에서 이전 상태(기준가/차수) 자동 복구