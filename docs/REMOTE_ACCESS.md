# 원격 접속 — Tailscale (홈서버 미니PC, 2026-11-08)

대시보드(`:5000`)는 **Tailscale 가상망 안에서만** 접속한다. 공유기 포트포워딩은 하지 않는다.

## 왜 Tailscale인가 (DuckDNS + 포트포워딩과 비교)
| | Tailscale | DuckDNS + 포트포워딩 |
|---|---|---|
| 인터넷 노출 | 없음 (포트 안 열림) | 공유기 포트가 전 세계에 열림 |
| HTTPS | WireGuard로 기기 간 암호화 | 리버스프록시(Caddy/Nginx)+인증서 별도 구성 필요 |
| IP 화이트리스트 | `100.64.0.0/10`로 계속 유효 | 폰 LTE IP가 바뀌어 사실상 전체 허용해야 함 |
| 공인 IP 변경 / 유선↔Wi-Fi 전환 | 영향 없음 | DDNS 갱신 필요, 내부 IP 바뀌면 포트포워딩 재설정 |
| 필요한 것 | 폰/PC에 앱 설치 | 도메인, 공유기 설정, 프록시, 인증서 갱신 |

DuckDNS는 n8n 웹훅처럼 **외부 서비스가 서버로 접속해 와야 할 때** 필요한 방식이다. 이 봇은
텔레그램도 long-polling(아웃바운드)이라 외부에서 들어오는 연결이 필요 없다. 그리고 대시보드가
거래소 API 키로 실제 매매하는 봇을 제어하므로 공인 인터넷에 열 이유가 없다.

## 미니PC 설정
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # 출력된 URL로 로그인
tailscale ip -4              # 100.x.y.z 확인

# 방화벽: SSH + Tailscale 인터페이스에서만 5000 허용
sudo ufw allow OpenSSH
sudo ufw allow in on tailscale0 to any port 5000 proto tcp
# (선택) 집 Wi-Fi에서 VPN 없이 접속하려면 + settings.yaml 화이트리스트에 192.168.219.0/24 추가
# sudo ufw allow from 192.168.219.0/24 to any port 5000 proto tcp
sudo ufw enable
```
- Tailscale 관리 콘솔(login.tailscale.com) → Machines → `home-server` → **Disable key expiry**
  (기본 180일마다 재인증이 필요한데, 만료되면 원격 접속이 끊긴다)
- 화이트리스트: `config/settings.yaml` → `web.auth.ip_whitelist: [100.64.0.0/10, 127.0.0.1]`

## 폰/PC
Tailscale 앱 설치 → 같은 계정으로 로그인 → `http://home-server:5000`
(MagicDNS가 안 되면 `http://100.x.y.z:5000`)

## 이미 적용된 보안 코드
- `web/auth.py`: 비밀번호 로그인, CSRF, IP 화이트리스트(CIDR), 브루트포스 방어
  (IP별 15분 내 5회 실패 → 10분 잠금), 민감 작업 재인증.
- 권장: 강한 `WEB_PASSWORD`/`WEB_SECRET_KEY`(.env), 빗썸 API 키는 거래 전용(출금 비활성) + 허용 IP 등록.
