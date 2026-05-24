# YouTube 자동업로드 완성 매뉴얼

이 문서는 현재 프로젝트 `youtube_trend_automation` 에서 실제 유튜브 자동업로드를 완성하기 위한 처음부터 끝까지 매뉴얼입니다.

이 문서대로 진행하면 다음 상태까지 도달할 수 있습니다.

1. Google Cloud 프로젝트 생성
2. YouTube Data API 활성화
3. OAuth 동의 화면 설정
4. OAuth Desktop Client 생성
5. 로컬에서 첫 로그인 승인
6. 토큰 발급 및 저장
7. 로컬에서 실제 자동업로드 테스트
8. Ubuntu 서버에 토큰 복사
9. systemd 기반 자동 실행
10. 실패 시 점검 방법 이해

## 1. 먼저 꼭 알아야 하는 핵심 사실

이 프로젝트의 유튜브 업로드는 `OAuth 2.0 + youtube.upload scope` 방식으로 동작합니다.

현재 코드 기준으로 중요한 사실은 아래와 같습니다.

1. 서비스 계정은 사용할 수 없습니다. YouTube Data API는 서비스 계정을 YouTube 채널에 연결할 수 없다고 공식 문서에 명시합니다.
2. 첫 인증은 브라우저 로그인과 승인 과정이 필요합니다.
3. 현재 코드의 인증 방식은 로컬 브라우저를 띄우는 Desktop App 방식입니다.
4. 따라서 첫 OAuth 승인은 Windows 로컬 PC에서 먼저 하는 것이 가장 안전합니다.
5. 서버는 그 이후 발급된 토큰 파일을 받아서 자동 갱신하며 사용하는 구조가 가장 현실적입니다.
6. 비검증 API 프로젝트로 업로드한 영상은 private 상태로 제한될 수 있습니다. 현재 프로젝트 기본값도 `private` 이므로 이 정책과 잘 맞습니다.
7. 2025년 12월 4일 기준으로 YouTube Data API의 `videos.insert` 업로드 비용은 약 1600에서 약 100 quota units로 변경됐다고 YouTube 공식 revision history에 적혀 있습니다.
8. 기본 일일 할당량은 공식 quota calculator 기준 10,000 units/day 입니다.

## 2. 내가 선택해야 하는 운영 방식

실제로는 아래 2가지 중 하나를 선택하면 됩니다.

### 방식 A. 가장 빨리 성공하는 방법

- 용도: 본인 계정으로만 테스트 또는 소규모 개인 사용
- Google Cloud OAuth 앱 상태: `External + Testing`
- 장점: 설정이 가장 빠름
- 단점: 공식 문서상 테스트 사용자 승인은 동의 후 7일이 지나면 만료됩니다
- 추천 상황: 처음 업로드 성공 여부를 확인할 때

### 방식 B. 장기 자동운영용 방법

- 용도: EC2에서 계속 자동업로드를 돌릴 목적
- Google Cloud OAuth 앱 상태: `External + In production`
- 장점: 테스트 모드 7일 만료 문제를 피하기 쉬움
- 단점: 상황에 따라 OAuth 검증, 앱 정보, 홈페이지, 개인정보처리방침, 데모 영상 제출이 필요할 수 있음
- 추천 상황: 서버에서 장기 무중단 운영하려는 경우

처음에는 방식 A로 실제 업로드 성공까지 먼저 확인하고, 이후 장기 운영이 필요할 때 방식 B로 넘어가는 것을 권장합니다.

## 3. 사전 준비물

아래가 준비되어 있어야 합니다.

1. Google 계정 1개
2. 유튜브 채널 1개
3. 이 프로젝트 로컬 실행 환경
4. Python 가상환경
5. `ffmpeg`
6. `.env` 파일
7. 업로드할 수 있는 Google 계정으로 로그인 가능한 브라우저

확인 명령:

```powershell
cd C:\Users\happy\Desktop\박정근\주식\자동트레이드\AutoTrade-System\youtube_trend_automation
.venv\Scripts\Activate.ps1
python --version
pytest tests
python main.py --mode run-once
```

## 4. YouTube 채널 준비

1. 브라우저에서 YouTube에 로그인합니다.
2. 오른쪽 상단 프로필 메뉴에서 채널이 실제로 생성되어 있는지 확인합니다.
3. 업로드에 사용할 Google 계정과 채널이 정확히 연결되어 있는지 확인합니다.
4. 여러 채널을 운영 중이면 업로드 대상 채널로 전환된 상태인지 확인합니다.
5. Shorts 위주라면 일반적으로 바로 테스트 가능하지만, 긴 영상 업로드나 추가 기능을 쓰려면 채널 인증이 필요한 경우가 있으니 YouTube Studio 상태도 확인합니다.

## 5. Google Cloud 프로젝트 만들기

1. 브라우저에서 Google Cloud Console에 접속합니다.
2. 상단 프로젝트 선택 메뉴에서 `새 프로젝트`를 누릅니다.
3. 프로젝트 이름 예시:
   `youtube-trend-automation-dev`
4. 만들기를 누릅니다.
5. 프로젝트가 선택된 상태인지 다시 확인합니다.

권장 방식:

1. 개발/테스트용 프로젝트 1개
2. 장기 운영용 프로젝트 1개

Google 공식 도움말도 개발/테스트 프로젝트와 운영/배포 프로젝트를 분리하는 것을 권장합니다.

## 6. YouTube Data API v3 활성화

1. Google Cloud Console에서 현재 프로젝트를 선택합니다.
2. 왼쪽 메뉴 또는 검색창에서 `APIs & Services` 로 이동합니다.
3. `Library` 를 엽니다.
4. 검색창에 `YouTube Data API v3` 를 입력합니다.
5. `Enable` 을 누릅니다.

여기까지 끝나야 업로드 API를 쓸 수 있습니다.

## 7. OAuth 동의 화면 설정

1. Google Cloud Console에서 `Google Auth Platform` 또는 `OAuth consent screen` 화면으로 이동합니다.
2. 앱 유형을 선택합니다.
3. 일반 개인 계정이면 보통 `External` 을 선택합니다.
4. 회사 Google Workspace 조직 내부 전용이면 `Internal` 도 가능하지만, 일반인은 대부분 `External` 입니다.
5. 아래 정보를 입력합니다.

필수 입력 예시:

- App name: `YouTube Trend Automation`
- User support email: 본인 이메일
- Developer contact email: 본인 이메일

추가로 넣을 수 있는 항목:

- App logo
- App homepage
- Privacy policy URL
- Terms of service URL

중요:

1. 지금 당장 테스트만 할 거면 최소 정보만 넣고 진행해도 됩니다.
2. 장기 운영용으로 `In production` 으로 바꾸거나 검증을 진행할 때는 홈페이지와 개인정보처리방침 URL이 사실상 필요해질 수 있습니다.

## 8. 앱 상태를 어떻게 둘지 결정하기

### 빠른 테스트용 설정

1. Publishing status 를 `Testing` 으로 둡니다.
2. Test users 에 본인 Google 계정을 추가합니다.
3. 저장합니다.

이 방식은 가장 빨리 성공하지만, 공식 도움말 기준으로 테스트 사용자의 승인(authorizations)은 동의 후 7일 뒤 만료될 수 있습니다.

### 장기 운영용 설정

1. 앱을 `In production` 으로 전환합니다.
2. 필요 시 verification 절차를 진행합니다.
3. 검증에 대비해 홈페이지, 개인정보처리방침, 앱 설명, 스코프 사용 목적, 데모 영상을 준비합니다.

Google 공식 문서상 개인 용도 앱은 검증이 항상 필수는 아니지만, `unverified app` 경고나 사용자 제한 정책이 걸릴 수 있습니다. 장기 무인 운영이면 운영 프로젝트를 따로 두고 정식 절차를 준비하는 것이 안전합니다.

## 9. OAuth 스코프 이해하기

현재 프로젝트 코드는 아래 스코프를 사용합니다.

```text
https://www.googleapis.com/auth/youtube.upload
```

이 스코프는 유튜브 업로드 권한입니다. 현재 프로젝트는 이 범위를 기준으로 동작합니다.

스코프를 더 넓게 늘리지 않는 것이 좋습니다. 공식 문서도 필요한 최소 범위만 요청하라고 안내합니다.

## 10. OAuth Client ID 만들기

1. Google Cloud Console에서 `APIs & Services` > `Credentials` 로 이동합니다.
2. `Create Credentials` 를 누릅니다.
3. `OAuth client ID` 를 선택합니다.
4. Application type 은 `Desktop app` 을 선택합니다.
5. 이름 예시:
   `youtube-trend-automation-desktop`
6. 생성 후 JSON 다운로드를 누릅니다.

중요:

1. 현재 프로젝트 코드는 Desktop App OAuth 흐름에 맞춰 작성되어 있습니다.
2. 현재 코드가 `InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0)` 을 사용하므로, 브라우저가 열리는 로컬 승인 방식이 가장 잘 맞습니다.

## 11. JSON 파일을 프로젝트에 배치하기

다운로드한 OAuth JSON 파일을 프로젝트 안에 둡니다.

권장 위치 예시:

```text
youtube_trend_automation/
  secrets/
    client_secret.json
```

지금 프로젝트 루트에 바로 둬도 동작은 하지만, 장기적으로는 `secrets/` 폴더를 따로 두는 것이 정리하기 쉽습니다.

파일 이름은 꼭 `client_secret.json` 일 필요는 없습니다. `.env` 에 실제 경로만 넣으면 됩니다.

## 12. .env 파일 수정하기

`.env` 에 아래 항목이 맞는지 확인합니다.

```env
YTA_ALLOW_NETWORK=true
YTA_UPLOAD_ENABLED=true
YTA_YOUTUBE_PRIVACY_STATUS=private
YOUTUBE_CLIENT_SECRETS_FILE=./secrets/client_secret.json
YOUTUBE_TOKEN_FILE=./data/youtube-token.json
```

설명:

1. `YTA_ALLOW_NETWORK=true`
   외부 네트워크 호출 허용
2. `YTA_UPLOAD_ENABLED=true`
   YouTube 업로드 단계 활성화
3. `YTA_YOUTUBE_PRIVACY_STATUS=private`
   기본 업로드 상태를 private 로 유지
4. `YOUTUBE_CLIENT_SECRETS_FILE`
   방금 받은 OAuth JSON 경로
5. `YOUTUBE_TOKEN_FILE`
   첫 로그인 후 저장될 토큰 파일 경로

권장:

1. 처음에는 반드시 `private`
2. public 자동공개가 꼭 필요하지 않다면 계속 `private`
3. 비검증 API 프로젝트는 공식 문서상 public 업로드가 제한될 수 있으므로 특히 `private` 유지가 안전

## 13. 첫 인증은 로컬 Windows에서 진행하기

이 단계가 가장 중요합니다.

현재 코드 구조상 첫 OAuth 인증은 브라우저가 열리는 환경에서 진행하는 것이 가장 간단합니다.

PowerShell:

```powershell
cd C:\Users\happy\Desktop\박정근\주식\자동트레이드\AutoTrade-System\youtube_trend_automation
.venv\Scripts\Activate.ps1
python main.py --mode run-once
```

진행 흐름:

1. 프로젝트가 트렌드 수집
2. 대본/설명/태그 생성
3. TTS 생성
4. MP4 렌더링
5. 업로드 단계에서 브라우저 로그인 창 열림
6. 업로드에 사용할 Google 계정 선택
7. 권한 승인
8. 성공 시 토큰 파일 생성

정상 완료되면 아래 파일이 생깁니다.

```text
data/youtube-token.json
```

그리고 `outputs/metadata/*.json` 에 업로드 결과가 기록됩니다.

## 14. 브라우저 승인 창에서 주의할 점

1. 반드시 업로드할 YouTube 채널이 연결된 Google 계정으로 로그인합니다.
2. 다른 구글 계정이 여러 개 로그인된 상태면 실수하기 쉬우니 가능하면 브라우저 프로필을 따로 쓰는 것이 좋습니다.
3. 앱이 검증되지 않았다면 경고 화면이 나올 수 있습니다.
4. 개인 테스트 용도라면 본인이 만든 앱을 본인 계정으로 승인하는 것은 일반적으로 가능한 흐름입니다.
5. `Testing` 상태라면 반드시 `Test users` 에 등록한 계정으로 로그인해야 합니다.

## 15. 첫 업로드 성공 확인 방법

다음 3곳을 확인합니다.

1. PowerShell 출력에서 `upload.status` 가 `created` 인지 확인
2. `upload.path` 에 YouTube 영상 URL이 들어왔는지 확인
3. YouTube Studio 에 접속해서 실제 private 영상이 올라왔는지 확인

추가 확인 파일:

- `outputs/audio/`
- `outputs/subtitles/`
- `outputs/videos/`
- `outputs/metadata/`

## 16. 업로드만 따로 테스트하는 방법

이미 비디오가 만들어져 있다면 업로드만 다시 시험할 수 있습니다.

```powershell
python main.py --mode upload-only
```

이 모드는 최신 메타데이터를 기준으로 업로드만 다시 시도합니다.

## 17. 서버 운영 전 꼭 해야 하는 권장 절차

서버에서 처음부터 OAuth 승인까지 하려고 하지 말고, 아래 순서로 진행하세요.

1. Windows 로컬에서 첫 OAuth 승인 완료
2. `youtube-token.json` 생성 확인
3. 로컬에서 실제 업로드 1회 성공 확인
4. 그 다음에만 서버로 이동

이 순서를 지키면 EC2에서 브라우저 문제로 막히는 일을 크게 줄일 수 있습니다.

## 18. Ubuntu 서버에 올릴 파일

서버에 최소한 아래가 있어야 합니다.

1. 프로젝트 코드 전체
2. `.env`
3. OAuth client secret JSON
4. `youtube-token.json`

권장 서버 파일 위치 예시:

```text
/opt/youtube-trend-automation/
  .env
  main.py
  app/
  data/
    youtube-token.json
  secrets/
    client_secret.json
```

## 19. 서버 .env 예시

```env
YTA_ALLOW_NETWORK=true
YTA_UPLOAD_ENABLED=true
YTA_YOUTUBE_PRIVACY_STATUS=private
YOUTUBE_CLIENT_SECRETS_FILE=/opt/youtube-trend-automation/secrets/client_secret.json
YOUTUBE_TOKEN_FILE=/opt/youtube-trend-automation/data/youtube-token.json
SSH_DEPLOY_USER=ubuntu
SSH_DEPLOY_PATH=/opt/youtube-trend-automation
```

## 20. Ubuntu 서버 반영 순서

1. 서버에 접속합니다.
2. 프로젝트 폴더를 준비합니다.
3. `.env`, client secret, token 파일을 업로드합니다.
4. 아래 명령을 실행합니다.

```bash
cd /opt/youtube-trend-automation
bash deploy/deploy.sh
sudo cp deploy/systemd/youtube-trend-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable youtube-trend-bot
sudo systemctl restart youtube-trend-bot
sudo systemctl status youtube-trend-bot --no-pager
```

## 21. 서버에서 토큰을 새로 만들지 않는 이유

현재 코드의 첫 인증 방식은 로컬 웹서버를 띄우는 Desktop OAuth 흐름입니다.

따라서 아래 환경에서는 처음 인증이 번거롭습니다.

1. GUI 없는 EC2
2. 브라우저 없는 Ubuntu
3. SSH 콘솔만 있는 환경

그래서 가장 쉬운 실전 방식은 다음입니다.

1. 로컬 Windows에서 OAuth 승인
2. `youtube-token.json` 생성
3. 그 파일을 서버에 복사
4. 서버는 그 토큰을 사용해 자동 갱신하며 업로드

## 22. systemd 자동실행 확인

서비스 상태 확인:

```bash
sudo systemctl status youtube-trend-bot --no-pager
```

로그 확인:

```bash
journalctl -u youtube-trend-bot -n 200 --no-pager
tail -n 200 /opt/youtube-trend-automation/logs/systemd.log
```

수동 1회 실행:

```bash
cd /opt/youtube-trend-automation
source .venv/bin/activate
python main.py --mode run-once
```

## 23. 장기 운영 시 꼭 이해해야 하는 정책

### 테스트 모드의 한계

공식 문서 기준:

1. `Testing` 상태 앱은 테스트 사용자만 승인 가능
2. 최대 100 test users 제한
3. 테스트 사용자 승인은 7일 뒤 만료될 수 있음

따라서 EC2에서 장기 자동업로드를 계속 하려면 `Testing` 상태를 장기간 유지하는 것은 불안정합니다.

### 비검증 프로젝트의 private 제한

공식 YouTube Data API 문서 기준:

1. 2020년 7월 28일 이후 생성된 비검증 API 프로젝트가 업로드한 영상은 private 로 제한될 수 있음
2. public 자동공개를 원하면 audit 또는 검증 절차가 필요할 수 있음

따라서 현재 프로젝트는 `private` 업로드를 기본으로 쓰는 것이 맞습니다.

## 24. 진짜 장기 운영을 위한 권장 구조

가장 현실적인 구조는 아래입니다.

1. 개발용 Google Cloud 프로젝트
2. 운영용 Google Cloud 프로젝트
3. 개발용은 Testing 상태로 빠르게 테스트
4. 운영용은 In production 전환
5. 필요 시 verification 준비
6. 업로드는 계속 private
7. 공개 시점은 YouTube Studio에서 사람이 검토 후 공개하거나, audit 후 자동공개 전략 검토

## 25. Verification 이 필요해질 때 준비할 것

Google 공식 도움말 기준으로 운영 프로젝트를 검증하려면 보통 아래를 준비합니다.

1. App name
2. Support email
3. Developer contact email
4. Homepage URL
5. Privacy policy URL
6. 필요 시 Terms of Service URL
7. 스코프 사용 목적 설명
8. 실제 OAuth 흐름과 데이터 사용을 보여주는 데모 영상

이때 홈페이지와 개인정보처리방침은 공개 접근 가능한 정적 페이지면 충분한 경우가 많습니다.

## 26. 업로드 실패 시 가장 흔한 원인과 해결 방법

### 1. `Missing YouTube client secrets`

원인:

- `.env` 의 `YOUTUBE_CLIENT_SECRETS_FILE` 경로가 틀림

해결:

1. 실제 파일이 존재하는지 확인
2. 상대경로 대신 절대경로 사용
3. Windows와 Ubuntu에서 경로를 따로 맞춤

### 2. 브라우저 승인이 안 뜸

원인:

- 서버에서 처음 인증하려고 함
- 브라우저 차단
- 로컬 포트 충돌

해결:

1. 먼저 Windows 로컬에서 인증
2. 그 후 토큰 파일만 서버로 복사

### 3. `access blocked` 또는 테스트 사용자 오류

원인:

- 앱이 Testing 상태인데 본인 계정이 Test users 에 없음

해결:

1. OAuth consent screen 에서 Test users 확인
2. 승인에 사용할 구글 계정이 목록에 있는지 확인

### 4. 7일 후 다시 인증하라고 함

원인:

- 앱이 Testing 상태

해결:

1. 임시로 다시 로그인 승인
2. 장기 운영이면 운영용 프로젝트를 만들고 In production 전환 고려

### 5. 업로드는 됐는데 public 이 안 됨

원인:

- 비검증 API 프로젝트의 private 제한

해결:

1. 기본 운영을 private 로 유지
2. public 자동공개가 꼭 필요하면 audit/verification 준비

### 6. 서버에서 갑자기 갱신 실패

원인:

- 토큰 만료 또는 revoked
- Google 계정 비밀번호/보안 설정 변경

해결:

1. 로컬에서 다시 승인
2. 새 `youtube-token.json` 을 서버에 덮어쓰기
3. 서비스 재시작

### 7. `quotaExceeded`

원인:

- 일일 할당량 초과

해결:

1. Cloud Console의 Quotas 페이지 확인
2. 업로드 횟수 줄이기
3. 필요 시 quota extension 검토

## 27. 일반인이 가장 안전하게 끝내는 실제 권장 순서

이 순서대로 하면 됩니다.

1. YouTube 채널 확인
2. Google Cloud 프로젝트 생성
3. YouTube Data API v3 활성화
4. OAuth consent screen 설정
5. `External + Testing` 으로 시작
6. Test users 에 본인 계정 추가
7. Desktop App OAuth client 생성
8. JSON 다운로드
9. 프로젝트에 JSON 배치
10. `.env` 에 경로 입력
11. `YTA_ALLOW_NETWORK=true`
12. `YTA_UPLOAD_ENABLED=true`
13. `YTA_YOUTUBE_PRIVACY_STATUS=private`
14. 로컬에서 `python main.py --mode run-once`
15. 브라우저 승인
16. YouTube Studio 에 private 업로드 확인
17. `data/youtube-token.json` 생성 확인
18. 이 토큰 파일을 서버로 복사
19. Ubuntu 서버에 프로젝트 반영
20. `deploy.sh` 실행
21. systemd 등록
22. 로그 확인
23. 1일 이상 지켜보며 자동업로드 확인
24. 장기 운영 필요 시 운영용 Google Cloud 프로젝트 별도 생성
25. 필요 시 In production 및 verification 준비

## 28. 이 프로젝트 기준 실사용 명령 모음

로컬 전체 실행:

```powershell
python main.py --mode run-once
```

업로드만 재시도:

```powershell
python main.py --mode upload-only
```

스케줄러 실행:

```powershell
python main.py --mode scheduler
```

Windows 배포 검증:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1 -SkipUpload
```

Ubuntu 배포:

```bash
bash deploy/deploy.sh
```

## 29. 내가 지금 당장 해야 하는 최소 작업 체크리스트

아래가 모두 체크되면 실제 자동업로드 준비가 끝난 것입니다.

- Google Cloud 프로젝트 생성 완료
- YouTube Data API v3 활성화 완료
- OAuth consent screen 설정 완료
- Test users 에 본인 계정 추가 완료
- Desktop App OAuth client JSON 다운로드 완료
- `.env` 의 `YOUTUBE_CLIENT_SECRETS_FILE` 설정 완료
- `.env` 의 `YOUTUBE_TOKEN_FILE` 설정 완료
- `.env` 의 `YTA_UPLOAD_ENABLED=true` 설정 완료
- 로컬에서 브라우저 승인 완료
- `data/youtube-token.json` 생성 완료
- 로컬 실제 업로드 1회 성공 완료
- 서버에 token 파일 복사 완료
- systemd 실행 완료

## 30. 공식 문서 참고 링크

아래 링크는 2026-03-25 기준 확인한 공식 문서입니다.

- Upload a Video: https://developers.google.com/youtube/v3/guides/uploading_a_video
- OAuth 2.0 for Mobile & Desktop Apps: https://developers.google.com/youtube/v3/guides/auth/installed-apps
- YouTube OAuth Authorization overview: https://developers.google.com/youtube/v3/guides/authentication
- Videos resource and private restriction note: https://developers.google.com/youtube/v3/docs/videos
- Quota Calculator: https://developers.google.com/youtube/v3/determine_quota_cost
- Revision History: https://developers.google.com/youtube/v3/revision_history
- When verification is not needed: https://support.google.com/cloud/answer/13464323
- Manage App Audience / Publishing status: https://support.google.com/cloud/answer/15549945
- Submit app for verification: https://support.google.com/cloud/answer/13461325
