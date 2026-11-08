#!/usr/bin/env bash
# CryptoSplit — systemd 서비스 제거:  sudo ./uninstall_service.sh
# (.venv, data/, logs/ 는 그대로 둔다)
set -euo pipefail
SERVICE=cryptosplit
UNIT=/etc/systemd/system/${SERVICE}.service

if [[ $EUID -ne 0 ]]; then
    echo "[오류] 관리자 권한이 필요합니다: sudo ./uninstall_service.sh" >&2
    exit 1
fi

echo "[1/2] 서비스 중지 (봇 중지 + 미체결 주문 취소 후 종료)..."
systemctl disable --now "$SERVICE" 2>/dev/null || true

echo "[2/2] 유닛 파일 제거..."
rm -f "$UNIT"
systemctl daemon-reload
systemctl reset-failed "$SERVICE" 2>/dev/null || true
echo "완료 — 서비스가 제거되었습니다."
