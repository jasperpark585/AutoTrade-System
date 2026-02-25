# 국내주식 완전자동 매매 시스템 (운영형 최종본)

## 1) 프로젝트 폴더 구조

```text
AutoTrade-System/
├── app/
│   ├── core/
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── engine.py
│   │   ├── market_hours.py
│   │   ├── reporting.py
│   │   ├── secrets.py
│   │   └── strategy.py
│   ├── services/
│   │   ├── kakao.py
│   │   └── kis_client.py          # LIVE KIS 인증/해시키/주문 처리
│   ├── ui/
│   │   └── streamlit_app.py       # 환경변수 상태/전략/리포트 UI
│   ├── utils/
│   │   └── logging.py
│   ├── deploy/
│   │   ├── autotrade-engine.service
│   │   ├── autotrade-ui.service
│   │   └── logrotate-autotrade
│   ├── __init__.py
│   └── main.py
├── scripts/
│   └── bootstrap_ec2.sh
├── tests/
│   └── test_kis_client.py
├── data/
├── logs/
├── requirements.txt
├── strategy.yaml
└── README.md
```

## 2) 핵심 구현 포인트

- DRY-RUN / LIVE 주문 분기 명확화.
- LIVE 주문은 KIS REST: `tokenP` → `hashkey` → `order-cash` 호출.
- 실패 시 `HTTP status`, `rt_cd`, `msg1` 로깅.
- 정규장 외 주문 자동 차단.
- 리스크 제한(`max_orders_per_day`, `max_daily_loss_krw`, `max_daily_loss_pct`) 강제 적용.
- KIS/Kakao 시크릿은 UI 저장이 아니라 **.env/시스템 환경변수만 사용**.

## 3) 설치/실행/배포 매뉴얼 (AWS EC2)

### 3-1. .env 설정

```bash
cat > /opt/AutoTrade-System/.env <<'ENV'
TZ=Asia/Seoul

# KIS LIVE
KIS_BASE_URL=https://openapi.koreainvestment.com:9443
KIS_APPKEY=...
KIS_APPSECRET=...
KIS_ACCOUNT_NO=12345678-01

# Optional
KAKAO_TOKEN=...
AUTOTRADE_EQUITY_BASE_KRW=30000000
KIS_SYMBOLS=005930,000660,035420
KIS_MOCK_ORDER=false
ENV
```

### 3-2. 설치 및 서비스 등록

```bash
bash scripts/bootstrap_ec2.sh
```

### 3-3. 운영 명령어

```bash
sudo systemctl status autotrade-engine
sudo systemctl status autotrade-ui
sudo journalctl -u autotrade-engine -f
curl http://127.0.0.1:8000/health
```

## 4) UI 사용 매뉴얼

1. `http://<EC2-IP>:8501` 접속.
2. `환경변수` 탭에서 마스킹된 환경변수 로드 상태 확인.
3. `전략 설정` 탭에서 단계별 파라미터/리스크 제한 수정 후 저장.
4. `DRY-RUN`으로 검증 후 `LIVE` 전환.
5. `운영 상태` 탭에서 시그널 점수, 포지션 확인.
6. `리포트` 탭에서 일/월/분기/연 성과 조회 + CSV 다운로드.

## 5) 테스트

```bash
python -m unittest tests/test_kis_client.py -v
python -m compileall app
```

## 6) 수동 진행/자동매매 진단 UI

- `수동 진행/자동매매 진단` 탭에서 **1클릭 수동 진단**을 실행하면 아래를 한 화면에서 확인합니다.
  - 시장 단계(정규장 여부)
  - 리스크 단계(일 주문수/일 손실한도/쿨다운)
  - 환경변수 단계(LIVE 필수 키 누락 여부)
  - 종목별 단계 통과/실패(`universe/pre_breakout/trigger/confirmation`) + `blocker` 원인
- 같은 탭에서 수동 주문 테스트(BUY/SELL, 수량, 가격) 가능
- LIVE 테스트는 `KIS_MOCK_ORDER=true`로 먼저 검증 후 실제 주문 전환 권장



## 7) LIVE 지표 산출 방식(실데이터 기반)

- `fetch_universe_quotes()` LIVE 모드에서는 random 값을 사용하지 않습니다.
- 산출식:
  - `price`: 현재가 API(inquire-price)
  - `spread_pct`: 호가 API(best bid/ask) 기반 `(ask-bid)/mid*100`
  - `volume_ratio`: 당일 누적거래량 / 최근 N일 평균 거래량 (`KIS_VOLUME_AVG_DAYS`)
  - `volatility_pct`: `(당일고가-당일저가)/시가*100`
  - `execution_strength`: KIS 응답 체결강도 필드(`tday_rltv`) 사용
  - `trend_slope`: 최근 N개 분봉 종가 기울기 (`KIS_TREND_WINDOW`)
- API 실패/데이터 부족 시 해당 종목은 SKIP(보수적) 처리합니다.

## 8) 실패 사유 확인 방법

- UI 수동주문/수동진단 오류는 `RetryError`를 언랩하여 최종 원인(`KISError`)을 표시합니다.
- 표시 항목: 예외 타입, 메시지, `status_code`, `rt_cd`, `msg1`, raw 응답 일부.
- 엔진 fatal 중지 시에도 동일 요약이 로그/알림/heartbeat `fatal_error`에 남습니다.


## 9) allowlist 기반 우량주 운용

- `strategy.yaml > stages.universe` 설정:
  - `use_allowlist: true`
  - `allowlist_symbols: ["005930", "000660", ...]`
- `use_allowlist=true`일 때 allowlist 종목만 자동매수 후보에 포함됩니다.
- 코드에 임의 우량주 하드코딩은 없고, 설정 파일에서만 제어합니다.

## 10) 현금 부족 스킵 로직

- 자동매수는 PASS 후보를 점수 내림차순으로 순차 시도합니다.
- 주문 전 `available_cash`(주문가능현금) 조회 후 1주 기준 예상비용 계산:
  - `estimated_cost = price * qty + estimated_fees`
- 아래 조건이면 해당 후보를 즉시 스킵하고 다음 후보를 시도합니다.
  - `estimated_cost > available_cash`
  - `estimated_cost > max_buy_amount_per_trade_krw`
- 로그에 `blocker=INSUFFICIENT_CASH`와 상세 정보(symbol/price/qty/estimated_cost/available_cash)가 기록됩니다.
- 토큰/서버 장애 등 API_ERROR 계열은 후보를 바꿔도 무의미할 수 있어 해당 tick을 즉시 중단합니다.


## 11) 수동 점검 체크리스트

### 서버 명령어

```bash
python -m compileall app tests
python -m unittest discover -s tests -v
sudo systemctl restart autotrade-engine autotrade-ui
sudo systemctl status autotrade-engine autotrade-ui
sudo journalctl -u autotrade-engine -f
curl http://127.0.0.1:8000/health
```

### UI 확인 항목

- `운영 상태` 탭에서 계좌 요약(주문가능현금/예수금/총자산) 카드가 표시되는지
- `운영 상태`/`수동 진행` 탭에서 보유 종목 테이블이 표시되는지
- `자동매수 후보 TOP N`에서 affordable=false 후보가 기본 숨김인지, 토글로 확인 가능한지
- 수동 BUY 주문 시 사전검증에서 `INSUFFICIENT_CASH`/`MAX_BUY_EXCEEDED`가 구조화 메시지로 보이는지
- LIVE에서 잔고 조회 실패 시 `status/rt_cd/msg1/raw`가 UI에 표시되는지


## 12) 포트폴리오 탭 / 우량주 후보 탭 사용법

- `포트폴리오` 탭
  - 계좌 요약 카드: 주문가능금액, D+2 예수금, 총평가금액, 총손익/수익률
  - 보유종목 테이블: symbol/qty/avg_price/eval_price/pnl/pnl_pct
  - 주문/체결 최근 N건 표시
  - 자동갱신 ON/OFF(15~60초) + 수동 갱신 버튼
  - 오류 시 `status/rt_cd/msg1/next_retry_at/raw` 구조화 표시

- `수동 진행/자동매매진단` 탭 > 우량주 후보
  - 자동 주기 갱신(기본 30분) 또는 `지금 우량주 검색` 버튼
  - 호출 제한 초과 시 blocker 표시
  - 후보 테이블: symbol/score/price/estimated_cost/affordable/skip_reason
  - `use_news_universe` 활성화 시 엔진이 후보 리스트를 universe로 사용

## 13) 뉴스 후보 데이터 저장 파일

- `data/candidates.json`: 우량주 후보 공유 파일(UI/엔진 공용)
- `data/news_state.json`: 호출 횟수/비용 cap/다음 갱신 시간 상태
- `data/portfolio_snapshot.json`: 포트폴리오 단기 캐시
- 파일 쓰기는 임시파일 후 rename(atomic write) 방식으로 처리

## 14) 토큰 쿨다운 발생 시 동작

- KIS 토큰 발급이 `EGW00133`, `403`, `temporarily unavailable`, cooldown 응답일 때 엔진은 **치명오류로 중지하지 않습니다**.
- 엔진은 blocker를 `KIS_TOKEN_COOLDOWN`으로 기록하고 `next_retry_at` 전까지 tick에서 주문/시세/계좌 호출을 스킵합니다.
- `/status` 및 UI 운영상태에서 `enabled`, `blocker`, `next_retry_at`, `current_profile`, `orderable_cash`, `candidates_count`, `recent_blockers(최근 5개)`를 확인할 수 있습니다.

## 15) 소액전용 프로필 자동선택

- `orderable_cash < 300,000` 이면 `small_cash_profile` 자동 적용.
- 적용 시 강제 제한:
  - 최대 보유종목 수 `1~2`
  - 1회 최대매수금 `orderable_cash`의 40% (최소 20,000 / 최대 150,000)
  - 저가 필터(`현재가 <= 80,000`, 1주 매수 가능)
- 수량 산정은 `orderable_cash`와 `max_buy_amount_per_trade`를 동시에 만족하도록 계산됩니다.

## 16) 서버 적용 절차 (systemd)

```bash
cd /opt/AutoTrade-System
source .venv/bin/activate
python -m compileall app tests
python -m unittest discover -s tests -v
sudo systemctl daemon-reload
sudo systemctl restart autotrade-engine autotrade-ui
sudo systemctl status autotrade-engine autotrade-ui --no-pager
sudo journalctl -u autotrade-engine -n 200 --no-pager
sudo journalctl -u autotrade-engine -f
curl -s http://127.0.0.1:8000/status | jq .
```


## 17) UI 안정성 스모크 테스트(쿨다운/권한)

```bash
# 1) 기본 확인
python -m compileall app tests
python -m unittest discover -s tests -v

# 2) 엔진/상태 확인
curl -s http://127.0.0.1:8000/status | head -200

# 3) readonly 시뮬레이션(운영 전 테스트 환경에서만)
chmod -R a-w data logs || true
# UI에서 토글/전략저장이 비활성화되고 경고만 표시되는지 확인
chmod -R u+w data logs || true
```

- KIS_TOKEN_COOLDOWN 상황에서도 UI는 종료되지 않고 경고 배너와 `next_retry_at`만 표시합니다.
- DB/파일이 readonly이면 UI는 토글/저장/수동갱신을 자동 비활성화합니다.


### 재시작 체크(운영 반영 빠른 절차)

```bash
sudo systemctl daemon-reload
sudo systemctl restart autotrade-engine
sudo systemctl restart autotrade-ui
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/status
```
