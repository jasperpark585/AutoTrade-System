# YouTube Trend Automation

트렌드 수집, 3분 내외 브리핑 생성, TTS/자막/세로형 영상 렌더, 유튜브 업로드, 채널별 규칙 관리, 원격 Studio UI까지 한 번에 처리하는 자동화 프로젝트입니다.

## 핵심 기능

- Google Trends + 네이버 뉴스 기반 트렌드 수집
- 주제별 상세 브리핑 스크립트 자동 생성
- 채널별 프리셋과 규칙 관리
- 제목, 설명, 태그, 배경 이미지, 썸네일 자동 생성
- `edge-tts` 기반 MP3 생성
- SRT 자막 생성
- `ffmpeg` 기반 세로형 MP4 렌더
- 유튜브 업로드
- Streamlit 기반 Studio UI
- Windows 예약 업로드 + Studio 실행 중 자동 일시중지
- Cloudflare Quick Tunnel / Named Tunnel 원격 접속 지원

## 빠른 시작

### Windows PowerShell

```powershell
cd youtube_trend_automation
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m compileall app tests main.py studio_app.py studio_launcher.py
pytest tests
python main.py --mode dry-run --allow-network false
python main.py --mode run-once --allow-network false --skip-upload
```

### Studio 실행

```powershell
.\launch_studio.ps1
```

또는

```powershell
python main.py --mode studio
```

## EXE 빌드와 실행

```powershell
powershell -ExecutionPolicy Bypass -File .\build_studio_exe.ps1
```

실행 파일:

```text
dist\YouTubeAutomationStudio\YouTubeAutomationStudio.exe
```

EXE 동작 방식:

- 실행하면 Studio UI가 `127.0.0.1:8501` 기준으로 열립니다.
- 화면 상단에 `Local`, `LAN`, `Remote` 주소가 표시됩니다.
- `Local`: 현재 PC에서 접속하는 주소
- `LAN`: 같은 네트워크 안의 다른 기기용 주소
- `Remote`: 외부 다른 장소의 PC/태블릿/휴대폰에서 접속하는 주소
- `Remote` 는 Cloudflare 설정에 따라 `Quick Tunnel` 또는 `Named Tunnel` 고정 도메인으로 동작합니다.
- 접속 주소는 [data/runtime/studio_access.txt](c:/Users/happy/Desktop/박정근/주식/자동트레이드/AutoTrade-System/youtube_trend_automation/data/runtime/studio_access.txt) 와 EXE 폴더의 `StudioAccess.txt` 에도 저장됩니다.

## 자동 업로드

- 기본 자동 업로드 주기는 `6시간`입니다.
- 주기는 Studio UI의 `자동화 / 원격 접속` 탭에서 변경할 수 있습니다.
- 저장하면 서버 설정 파일을 동기화하고 `youtube-trend-bot.service` 를 재시작합니다.
- 정기 업로드는 Ubuntu 서버에서만 실행됩니다.
- 로컬 Windows 자동 업로드는 사용하지 않습니다.
- 로컬 Studio/EXE는 설정 변경과 수동 1회 생성/업로드 전용입니다.

Windows에서 기존 로컬 작업 스케줄러를 제거하려면:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\remove_windows_scheduler.ps1
```

## Cloudflare 원격 접속

### Quick Tunnel

- 추가 설정 없이 바로 사용 가능
- 실행할 때마다 `Remote` 주소가 바뀝니다.

### Named Tunnel 고정 도메인

- Studio UI의 `자동화 / 원격 접속` 탭에서 `고정 도메인 Named Tunnel` 을 선택합니다.
- `고정 도메인 호스트명` 과 `Tunnel Token` 을 입력하고 저장하면 다음 실행부터 같은 주소를 계속 사용합니다.
- 아직 토큰이 없으면 아래 스크립트로 준비할 수 있습니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\setup_cloudflare_named_tunnel.ps1 -Hostname studio.example.com -TunnelName youtube-automation-studio
```

이 스크립트는 다음을 처리합니다.

- Cloudflare 로그인 확인
- named tunnel 생성
- DNS hostname 연결
- tunnel token 발급
- `data/studio_settings.json` 에 고정 도메인 설정 저장

## CLI 모드

- `python main.py --mode dry-run`
- `python main.py --mode run-once`
- `python main.py --mode render-only`
- `python main.py --mode upload-only`
- `python main.py --mode scheduler`
- `python main.py --mode studio`

옵션:

- `--allow-network true|false`
- `--skip-render`
- `--skip-upload`
- `--force`

## 주요 환경값

`.env.example` 를 기준으로 채우면 됩니다.

- `YTA_ALLOW_NETWORK=true`
- `OPENAI_API_KEY=...`
- `YTA_UPLOAD_ENABLED=true`
- `YOUTUBE_CLIENT_SECRETS_FILE=./secrets/client_secret.json`
- `YOUTUBE_TOKEN_FILE=./data/youtube-token.json`
- `YTA_YOUTUBE_PRIVACY_STATUS=private`

## 배포

### Windows 로컬 검증 + 서버 업로드

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
```

### Ubuntu 서버 반영

```bash
cd /opt/youtube-trend-automation
bash deploy/deploy.sh
sudo systemctl daemon-reload
sudo systemctl restart youtube-trend-bot
sudo systemctl status youtube-trend-bot --no-pager
```

## 매뉴얼

- [docs/youtube_auto_upload_manual_ko.md](c:/Users/happy/Desktop/박정근/주식/자동트레이드/AutoTrade-System/youtube_trend_automation/docs/youtube_auto_upload_manual_ko.md)
- [docs/studio_program_manual_ko.md](c:/Users/happy/Desktop/박정근/주식/자동트레이드/AutoTrade-System/youtube_trend_automation/docs/studio_program_manual_ko.md)
- [docs/cloudflare_named_tunnel_manual_ko.md](c:/Users/happy/Desktop/박정근/주식/자동트레이드/AutoTrade-System/youtube_trend_automation/docs/cloudflare_named_tunnel_manual_ko.md)

## 검증 명령

```powershell
python -m compileall app tests main.py studio_app.py studio_launcher.py
pytest tests
python main.py --mode dry-run --allow-network false
python main.py --mode run-once --allow-network false --skip-upload
```

## Server Status Panel

- Studio 상단에 `서버 자동 업로드 상태` 패널이 표시됩니다.
- 여기서 서버 서비스 상태, 다음 업로드 예정 시각, 남은 시간, 마지막 업로드 주제를 바로 확인할 수 있습니다.
- `서버 상태 새로고침` 버튼으로 즉시 다시 조회할 수 있습니다.
- `다음 자동 업로드 모니터 시작` 버튼을 누르면 서버가 다음 예약 업로드 결과를 자동 기록합니다.
