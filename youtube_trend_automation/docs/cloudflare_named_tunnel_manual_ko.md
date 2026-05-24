# Cloudflare 고정 도메인 Tunnel 안내

## 1. `my-key.pem` 과 `cert.pem` 은 다릅니다

- `my-key.pem`
  - 보통 AWS EC2 SSH 접속용 개인키
  - 예: `ssh -i my-key.pem ubuntu@server-ip`
  - 서버 로그인용

- `cert.pem`
  - Cloudflare Tunnel 계정 인증서
  - `cloudflared tunnel login` 을 실행할 때 생성
  - Cloudflare 계정에 연결된 tunnel 생성/삭제/route 설정용

즉, 두 파일은 용도가 완전히 다릅니다.

## 2. Cloudflare 고정 호스트명은 무엇인가

고정 호스트명은 이런 문자열입니다.

- `studio.example.com`
- `youtube-admin.mydomain.kr`

이 호스트명은 Cloudflare에 등록된 본인 도메인 안의 서브도메인이어야 합니다.

## 3. 내가 자동으로 끝까지 만들 수 있는 범위

자동으로 가능한 부분:

- named tunnel 생성
- DNS route 연결
- tunnel token 발급
- Studio 설정 파일 저장

사람이 반드시 해야 하는 최소 단계:

- Cloudflare 계정 로그인
- 브라우저에서 사용할 도메인(zone) 선택

이 단계는 Cloudflare 계정 소유자 확인 절차라서 자동으로 대신할 수 없습니다.

## 4. `cert.pem` 생성 위치

Windows 기본 위치:

- `%USERPROFILE%\\.cloudflared\\cert.pem`

예:

- `C:\\Users\\happy\\.cloudflared\\cert.pem`

## 5. 가장 쉬운 진행 방법

PowerShell에서 아래 명령만 실행하면 됩니다.

```powershell
cd youtube_trend_automation
powershell -ExecutionPolicy Bypass -File .\deploy\setup_cloudflare_named_tunnel.ps1 -Hostname studio.example.com -TunnelName youtube-automation-studio
```

스크립트가 자동으로 하는 일:

1. `cert.pem` 이 있는지 확인
2. 없으면 `cloudflared tunnel login` 실행
3. 브라우저 로그인 유도
4. named tunnel 생성
5. DNS route 연결
6. tunnel token 발급
7. `data/studio_settings.json` 에 저장

## 6. 브라우저에서 직접 해야 하는 순서

스크립트를 실행하면 Cloudflare 로그인 창이 열릴 수 있습니다.

그 다음 순서:

1. Cloudflare 계정 로그인
2. 사용할 도메인(zone) 선택
3. 승인 완료

완료되면 `cert.pem` 이 생성됩니다.

## 7. 도메인이 아직 없다면

Cloudflare 고정 도메인 방식은 본인 도메인이 Cloudflare DNS에 있어야 합니다.

즉, 이런 상황이면 바로 가능:

- 이미 Cloudflare에 `example.com` 이 등록돼 있음

이런 상황이면 먼저 준비가 필요:

- 아직 개인 도메인이 없음
- 도메인은 있지만 Cloudflare에 연결되지 않음

이 경우에는 고정 도메인 대신 Quick Tunnel만 사용할 수 있습니다.

## 8. 준비가 끝난 뒤 Studio에서 보이는 값

준비가 끝나면 Studio의 `자동화 / 원격 접속` 탭에서:

- 원격 접속 방식: `고정 도메인 Named Tunnel`
- 고정 도메인 호스트명: 예 `studio.example.com`
- Tunnel 이름: `youtube-automation-studio`

으로 저장됩니다.

그 뒤 Studio를 다시 실행하면 `Remote` 주소가 매번 바뀌지 않고 고정됩니다.

## 9. 공식 문서

- Cloudflare locally-managed tunnel 생성: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/local-management/create-local-tunnel/
- Tunnel permissions / `cert.pem` 설명: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/configure-tunnels/local-management/tunnel-permissions/
- Default `cloudflared` directory: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/local-management/local-tunnel-terms/
- DNS route: https://developers.cloudflare.com/tunnel/routing/
