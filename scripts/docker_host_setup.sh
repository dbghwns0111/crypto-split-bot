#!/usr/bin/env bash
# CryptoSplit Docker 호스트 1회 설정:  sudo ./scripts/docker_host_setup.sh
#
# 컨테이너가 재시작되면 자동매매 금지 원칙상 전 종목이 "중지" 상태로 올라와 사람이 다시
# 가동할 때까지 매매가 멈춘다(2026-11-14 06:15 systemd 시절 실제 40분 정지). Docker로
# 옮기면 같은 일이 "Docker 데몬 재시작"으로 일어날 수 있어 이를 막는다.
#
#  1) live-restore: dockerd 가 재시작(패키지 업데이트 등)돼도 실행 중인 컨테이너는 그대로 둔다.
#     reload(SIGHUP)로 적용되므로 지금 떠 있는 컨테이너(asset-dashboard 포함)는 재시작되지 않는다.
#  2) needrestart: 자동 보안 업데이트 후 docker/containerd 서비스를 마음대로 재시작하지 않게 한다.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "[오류] 관리자 권한이 필요합니다: sudo ./scripts/docker_host_setup.sh" >&2
    exit 1
fi

DAEMON_JSON=/etc/docker/daemon.json

echo "[1/2] Docker live-restore 설정..."
if [[ -f "$DAEMON_JSON" ]]; then
    if grep -q '"live-restore"' "$DAEMON_JSON"; then
        echo "      이미 설정돼 있음 — 건너뜀 ($DAEMON_JSON)"
    else
        echo "      [중단] $DAEMON_JSON 이 이미 있어 자동으로 고치지 않습니다."
        echo "      직접 \"live-restore\": true 항목을 추가한 뒤 'sudo systemctl reload docker' 하세요."
        exit 1
    fi
else
    printf '{\n  "live-restore": true\n}\n' > "$DAEMON_JSON"
    systemctl reload docker
    echo "      완료 (컨테이너 재시작 없이 적용)"
fi
docker info --format '      확인: LiveRestoreEnabled={{.LiveRestoreEnabled}}'

echo "[2/2] needrestart 자동 재시작 제외 (docker, containerd)..."
if [[ -d /etc/needrestart ]]; then
    mkdir -p /etc/needrestart/conf.d
    cat > /etc/needrestart/conf.d/cryptosplit-docker.conf <<'NR_EOF'
# CryptoSplit: 자동 업데이트 후 Docker 서비스 자동 재시작 금지 (scripts/docker_host_setup.sh 가 생성)
$nrconf{override_rc}{qr(^docker\.service$)} = 0;
$nrconf{override_rc}{qr(^containerd\.service$)} = 0;
NR_EOF
    echo "      완료"
else
    echo "      needrestart 없음 — 건너뜀"
fi

echo "끝. 갱신된 Docker는 다음에 직접 재시작할 때 반영됩니다."
