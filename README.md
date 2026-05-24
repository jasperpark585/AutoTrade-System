# AutoTrade-System

국내 주식 자동매매 운영 프로젝트입니다. 기본값은 `DRY-RUN`이며, 실계좌 주문은 여러 안전 플래그가 모두 맞을 때만 열립니다.

## 핵심 구성

```text
app/
  main.py                     # 엔진 루프 + HTTP API
  core/
    config.py                 # strategy.yaml/env 로딩 + LIVE 안전 플래그
    database.py               # SQLite 저장소 + 리포트 정리
    engine.py                 # 자동매매 엔진
    market_hours.py           # 국내 장 운영 시간
    risk.py                   # 리스크 한도
    strategy.py               # 후보 점수화
  services/
    gpt_scout.py              # 장전 AI 후보 선정 + 과금 가드
    kis_client.py             # KIS 토큰/시세/계좌/주문
    kakao.py                  # 알림
    portfolio_service.py      # 포트폴리오 스냅샷 캐시
  ui/
    streamlit_app.py          # 운영 UI
    portfolio_fallback.py     # 포트폴리오 표시 우선순위 계약
  agents/
    orchestrator.py           # 전문 에이전트 팀 라우팅/안전 정책
    ops_harness.py            # 읽기 전용 운영 점검 하네스
tests/
strategy.yaml
requirements.txt
```

## 운영 원칙

- GitHub `origin/main`을 기준 버전으로 둡니다.
- 로컬 작업 전 `git fetch --prune origin` 후 현재 브랜치가 `origin/main`과 같은지 확인합니다.
- 로컬 변경은 테스트 통과 후 커밋하고 GitHub에 push합니다.
- 충돌이나 실험 작업이 있으면 먼저 `git stash push -u -m "<reason>"`로 보관한 뒤 정리합니다.

## 안전 규칙

실계좌 주문은 아래 조건이 모두 참일 때만 허용됩니다.

- `strategy.yaml` 또는 `MODE`가 `LIVE`
- 환경변수 `LIVE=true`
- 환경변수 `DRY_RUN=false`
- 환경변수 `KIS_MOCK_ORDER=false`

KIS 토큰 쿨다운은 치명 오류가 아니라 일시 blocker로 처리합니다. 엔진은 `KIS_TOKEN_COOLDOWN`과 `next_retry_at`을 상태에 남기고, 가능한 경우 마지막 정상 현금 정보를 캐시에서 복원합니다.

## 환경변수

예시:

```bash
TZ=Asia/Seoul
LOG_LEVEL=INFO

KIS_BASE_URL=https://openapi.koreainvestment.com:9443
KIS_APPKEY=...
KIS_APPSECRET=...
KIS_ACCOUNT_NO=12345678-01

KAKAO_TOKEN=...
OPENAI_API_KEY=...
OPENAI_PAID_ALLOWED=false

LIVE=false
DRY_RUN=true
KIS_MOCK_ORDER=false
```

OpenAI 유료 호출은 `OPENAI_PAID_ALLOWED=true`일 때만 허용되도록 `strategy.yaml`의 `gpt_scout.quota_guard`가 방어합니다.

## 실행

Windows 자동 설치:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

Ubuntu/EC2 자동 설치:

```bash
bash scripts/bootstrap_ec2.sh
```

수동 실행:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

UI:

```bash
streamlit run app/ui/streamlit_app.py --server.port 8501 --server.address 0.0.0.0
```

## HTTP API

- `GET /health`
- `GET /status`
- `GET /config`
- `GET /candidates`
- `GET /trades?limit=200`
- `GET /chart?symbol=005930&count=180`
- `POST /refresh/portfolio`
- `POST /candidates/refresh` body: `{"force": true}`
- `POST /report/clear` body: `{"only_dry": true, "vacuum": false}`
- `POST /engine/enable` body: `{"enabled": true}`
- `POST /config/mode` body: `{"mode": "DRY-RUN"}`

## UI 노출 정책

- 운영상태: 헬스, blocker, LIVE 준비 상태, 후보 종목, OpenAI 가드 상태를 표시합니다.
- 계좌연결: 개인별 KIS AppKey, AppSecret, 계좌번호를 로컬 암호화 저장소에 등록합니다.
- 포트폴리오: 실계좌 상세, 보유 종목, 주문/체결을 표시합니다.
- 매매차트: 가격과 매수/매도 이벤트를 표시합니다.
- 운영도구: 후보 강제 갱신, 리포트성 기록 삭제를 제공합니다.

계좌 키는 중앙 서버로 전송하지 않는 개인 설치형 구조입니다. 저장값은 `data/autotrade.db`에 암호화되어 들어가며, 복호화 키는 `data/credential.key`에 로컬 파일로 보관됩니다. 이 두 파일은 사용자 장비 밖으로 공유하지 마세요.

포트폴리오 표시 우선순위는 `app/ui/portfolio_fallback.py`에 고정되어 있습니다.

1. live refresh 결과
2. engine cached snapshot
3. file snapshot
4. session last snapshot

## Cross Strategy

`app/core/cross_signals.py` calculates short-term moving-average crosses and the mid/long-term leading-span rule from the reference material.

- `GOLDEN_CROSS`: the short moving average crosses above the long moving average. The strategy adds a confirmation bonus.
- `DEAD_CROSS`: the short moving average crosses below the long moving average. The strategy blocks new entries by default.
- `MIDLONG_GOLDEN_CROSS`: the 20-period moving average crosses above Ichimoku Senkou Span 2, calculated from the 52-period high/low midpoint. The strategy treats this as the primary mid/long-term bottoming signal.
- `MIDLONG_DEAD_CROSS`: the 20-period moving average crosses below Senkou Span 2. The strategy blocks new entries.
- The signal candle high/low is returned as resistance/support levels so the chart can draw future reference lines.
- KIS daily chart requests use original prices (`FID_ORG_ADJ_PRC=1`) so split-adjusted historical prices do not distort these support and resistance levels.
- The chart API returns `cross_signals`, `ma_short`, and `ma_long`; the UI renders the signals as diamond markers.

## 에이전트 하네스

`app/agents/ops_harness.py`는 읽기 전용 운영 점검 하네스입니다. 실계좌 주문, 설정 변경, 파일 변경을 하지 않습니다. OpenAI Agents SDK로 확장할 때도 이 하네스는 “상태 요약과 권고”까지만 담당하고, 주문 실행은 엔진의 기존 안전 플래그와 수동 승인 경계를 통과해야 합니다.

`app/agents/orchestrator.py`는 전략, 리스크, UI, 운영 전문 에이전트 팀의 라우팅과 안전 정책을 정의합니다. 공식 Agents SDK 흐름은 `Agent`, `Runner`, `handoffs`, `guardrails`, `function_tool` 기반으로 확장할 수 있습니다. 이 프로젝트에서는 실거래 자동화를 LLM에 직접 위임하지 않고, 운영 점검 컨텍스트를 구조화하는 방식으로만 반영합니다.

예시:

```powershell
$context = '{"status":{"mode":"DRY-RUN","live_order_enabled":false},"config":{},"candidates":{"symbols":["005930"]}}'
$context | python -m app.agents.ops_harness
```

## 테스트

```bash
python -m compileall app tests
python -m unittest discover -s tests -v
```

## 주요 설정

- `gpt_scout.allow_external_call`: OpenAI 후보 선정 호출 허용 여부
- `gpt_scout.quota_guard`: 호출 횟수, 월 예산, 429 쿨다운 방어
- `small_cash_profile`: 주문가능현금이 작을 때 자동 축소 운용
- `position_management`: trailing stop, 약추세/시간 기반 청산
- `hourly_alert`: 장중 시간별 Kakao 상태 알림
