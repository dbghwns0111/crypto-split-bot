# CryptoSplit 운영 단축 명령어 — ~/.bash_aliases 에서 source 한다.
#   echo '[ -f ~/crypto-split-bot/scripts/cs_aliases.sh ] && . ~/crypto-split-bot/scripts/cs_aliases.sh' >> ~/.bash_aliases
# 목록 보기: cs
#
# 실행 방식 자동 판단: systemd 서비스(cryptosplit.service)가 enable 돼 있으면 systemd,
# 아니면 Docker(docker-compose.yml). 2026-11-15부터 Docker가 기본이다.

CS_DIR="$HOME/crypto-split-bot"
CS_SVC="cryptosplit"

_cs_mode() {
    if systemctl is-enabled "$CS_SVC" >/dev/null 2>&1; then echo systemd; else echo docker; fi
}
_cs_dc() { docker compose --project-directory "$CS_DIR" -f "$CS_DIR/docker-compose.yml" "$@"; }

# 자동 재가동 옵션 처리: -n / --no-resume 이면 이번 기동에서 자동 재가동을 건너뛰게 표식을 남긴다.
# (서버가 뜰 때 auto_resume.py 가 data/.no_auto_resume 를 확인하고 1회 소비한다)
# 반환: 0=정상, 1=알 수 없는 옵션
_cs_resume_opt() {
    rm -f "$CS_DIR/data/.no_auto_resume"
    case "${1:-}" in
        "") _CS_RESUME_MSG="※ 사용자가 켜두었던 종목은 서버가 뜨면 자동 재가동됩니다 (텔레그램으로 결과 알림)" ;;
        -n|--no-resume)
            touch "$CS_DIR/data/.no_auto_resume"
            _CS_RESUME_MSG="※ 자동 재가동 안 함 → 웹 [전체 가동] 또는 텔레그램 /resumeall 로 직접 시작" ;;
        *) echo "알 수 없는 옵션: $1  (사용법: ${FUNCNAME[1]} [-n|--no-resume])" >&2; return 1 ;;
    esac
}

# ── 서비스 제어 ─────────────────────────────────────────────
cs-restart() {   # cs-restart [-n]  — -n: 이번엔 자동 재가동 안 함
    _cs_resume_opt "$@" || return 1
    if [ "$(_cs_mode)" = systemd ]; then
        sudo systemctl restart "$CS_SVC" && systemctl --no-pager --lines=0 status "$CS_SVC" | head -3
    else
        _cs_dc restart && _cs_dc ps
    fi
    echo "$_CS_RESUME_MSG"
}
cs-start() {     # cs-start [-n]
    _cs_resume_opt "$@" || return 1
    if [ "$(_cs_mode)" = systemd ]; then sudo systemctl start "$CS_SVC"; else _cs_dc up -d; fi
    echo "$_CS_RESUME_MSG"
}
cs-stop() {   # 봇 중지 + 미체결 취소 후 종료(최대 60초)
    if [ "$(_cs_mode)" = systemd ]; then sudo systemctl stop "$CS_SVC"; else _cs_dc stop; fi
}
cs-status() {
    if [ "$(_cs_mode)" = systemd ]; then systemctl --no-pager status "$CS_SVC"; else _cs_dc ps; fi
}
cs-rebuild() {   # cs-rebuild [-n]  — Docker 전용: requirements.txt 변경 시 이미지 재빌드 후 재기동
    _cs_resume_opt "$@" || return 1
    _cs_dc up -d --build && _cs_dc ps
    echo "$_CS_RESUME_MSG"
}
alias cs-up='cs-rebuild'

# ── 로그 ────────────────────────────────────────────────────
cs-log() {   # 서비스 전체 로그 (실시간)
    if [ "$(_cs_mode)" = systemd ]; then journalctl -u "$CS_SVC" -f -n 50; else _cs_dc logs -f --tail 50; fi
}
alias cs-trade='tail -n 50 -f "$CS_DIR/logs/strategy.magic_split.log"'   # 매매 로그
alias cs-err='grep -h "ERROR" "$CS_DIR"/logs/*.log | sort | tail -n 30'  # 최근 오류 30줄

# ── 이동 / 코드 ─────────────────────────────────────────────
alias cs-cd='cd "$CS_DIR"'
alias cs-pull='git -C "$CS_DIR" pull'

# ── 네트워크 / 시스템 ───────────────────────────────────────
alias cs-ts='tailscale status'
alias cs-fw='sudo ufw status verbose'
alias cs-web='curl -s -o /dev/null -w "웹 응답: %{http_code}\n" -m 5 http://127.0.0.1:5000/login'
alias cs-disk='df -h / && du -sh "$CS_DIR/data" "$CS_DIR/logs"'

cs() {
    cat <<EOF
CryptoSplit 단축 명령어 (현재 실행 방식: $(_cs_mode))
  cs-restart  재시작 (코드 반영 ②단계) — 켜두었던 종목 자동 재가동
  cs-restart -n   재시작하되 자동 재가동 안 함 (cs-start, cs-rebuild 도 -n 지원)
  cs-start    시작
  cs-stop     중지 (봇 중지 + 미체결 취소)
  cs-status   상태
  cs-rebuild  이미지 재빌드 후 재기동 (requirements.txt 변경 시, Docker)
  cs-log      전체 로그 실시간 (Ctrl+C 종료)
  cs-trade    매매 로그 실시간
  cs-err      최근 오류 30줄
  cs-cd       프로젝트 폴더로 이동
  cs-pull     git pull
  cs-ts       Tailscale 연결 상태
  cs-fw       방화벽(ufw) 상태
  cs-web      웹 대시보드 응답 확인 (200이면 정상)
  cs-disk     디스크 / DB / 로그 용량
EOF
}
