#!/usr/bin/env bash
# CryptoSplit — systemd 서비스 설치 (Ubuntu)
#
#   sudo ./install_service.sh
#
# 1) 프로젝트 폴더에 .venv 가상환경 생성 + requirements 설치 (실행 사용자 소유)
# 2) /etc/systemd/system/cryptosplit.service 생성
# 3) 부팅 시 자동 시작 + 비정상 종료 시 60초 후 재시작 등록, 즉시 시작
#
# 서비스가 시작돼도 자동매매는 "중지" 상태로 대기한다(자동 시작 금지 원칙).
# 매매 가동은 웹 홈 [전체 가동] 또는 텔레그램 /resumeall.
set -euo pipefail

SERVICE=cryptosplit
UNIT=/etc/systemd/system/${SERVICE}.service
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ $EUID -ne 0 ]]; then
    echo "[오류] 관리자 권한이 필요합니다: sudo ./install_service.sh" >&2
    exit 1
fi

# 서비스를 실행할 일반 사용자 (root 로 돌리지 않는다)
RUN_USER="${SUDO_USER:-}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
    RUN_USER="$(stat -c '%U' "$PROJECT_DIR")"
fi
RUN_GROUP="$(id -gn "$RUN_USER")"

echo "===================================="
echo "  CryptoSplit - 서비스 설치"
echo "===================================="
echo "  프로젝트: $PROJECT_DIR"
echo "  실행계정: $RUN_USER"
echo

as_user() { sudo -u "$RUN_USER" -H "$@"; }

# ── 1. 가상환경 ───────────────────────────────────────────
echo "[1/3] 파이썬 가상환경 준비..."
if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    if ! as_user "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"; then
        PYVER="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
        echo "[오류] venv 생성 실패. 먼저 설치하세요: sudo apt install python${PYVER}-venv" >&2
        rm -rf "$PROJECT_DIR/.venv"
        exit 1
    fi
fi
as_user "$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip -q
as_user "$PROJECT_DIR/.venv/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt" -q
as_user mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data"
echo "      완료"

# ── 2. systemd 유닛 ───────────────────────────────────────
echo "[2/3] systemd 유닛 생성: $UNIT"
cat > "$UNIT" <<UNIT_EOF
[Unit]
Description=CryptoSplit 자동매매 봇 (웹 대시보드 + 텔레그램)
Wants=network-online.target
After=network-online.target time-sync.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_GROUP
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/python main.py
Environment=PYTHONUNBUFFERED=1
# 정상 종료 시 main.py 가 봇 중지 + 미체결 주문 취소를 수행할 시간
KillSignal=SIGTERM
TimeoutStopSec=60
# 비정상 종료 시 60초 후 재시작 (간격 내 무한 재시도)
Restart=on-failure
RestartSec=60
StartLimitIntervalSec=0

[Install]
WantedBy=multi-user.target
UNIT_EOF
echo "      완료"

# ── 3. 등록 + 시작 ────────────────────────────────────────
echo "[3/3] 서비스 등록 및 시작..."
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 3
systemctl --no-pager --lines=0 status "$SERVICE" || true

echo
echo "===================================="
echo "  설치 완료"
echo "  - 부팅 시 자동 시작, 비정상 종료 시 60초 후 재시작"
echo "  - 상태:   systemctl status $SERVICE"
echo "  - 재시작: sudo systemctl restart $SERVICE"
echo "  - 로그:   journalctl -u $SERVICE -f  /  $PROJECT_DIR/logs/"
echo "  - 대시보드: http://127.0.0.1:5000"
echo "  ※ 자동매매는 중지 상태 — 웹 [전체 가동] 또는 텔레그램 /resumeall"
echo "===================================="
