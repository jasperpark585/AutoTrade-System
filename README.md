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
