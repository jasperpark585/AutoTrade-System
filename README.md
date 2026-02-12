# 국내주식 완전자동 매매 시스템 (운영형 최종본)

## 1) 프로젝트 폴더 구조

```text
AutoTrade-System/
├── app/
│   ├── core/
│   │   ├── config.py               # strategy.yaml 로드/저장(핫리로드용)
│   │   ├── database.py             # SQLite 스키마/CRUD(trades/signals/engine_state)
│   │   ├── engine.py               # 완전자동 스캔/진입/관리/청산 루프
│   │   ├── market_hours.py         # 장 운영(정규장/휴장일) 판별
│   │   ├── reporting.py            # 일/월/분기/연 집계 + MDD 추정 + 기여도
│   │   ├── secrets.py              # 시크릿 암호화 저장(Fernet)
│   │   └── strategy.py             # 단계별 점수화 전략 엔진
│   ├── services/
│   │   ├── kakao.py                # 카카오 알림 전송
│   │   └── kis_client.py           # KIS 연동 레이어(재시도/백오프)
│   ├── ui/
│   │   └── streamlit_app.py        # 설정/모니터링/시크릿/리포트 UI
│   ├── utils/
│   │   └── logging.py              # 로깅 + 로테이션 핸들러
│   ├── deploy/
│   │   ├── autotrade-engine.service # systemd (엔진)
│   │   ├── autotrade-ui.service     # systemd (UI)
│   │   └── logrotate-autotrade      # 로그 로테이션
│   ├── __init__.py
│   └── main.py                     # 엔진 메인 + /health 엔드포인트
├── scripts/
│   └── bootstrap_ec2.sh            # EC2 원클릭 부트스트랩
├── data/                           # DB/암호화 시크릿 저장 위치
├── logs/                           # 운영 로그
├── requirements.txt
├── strategy.yaml                   # 단계별 돌파전략/리스크/가중치 디폴트
└── README.md
```

## 2) 핵심 구현 포인트

- **완전자동화 루프**: Universe 스캔 → 후보 평가(단계별 점수) → 자동진입 → 포지션 자동관리/청산.
- **장 마감 주문 차단 + 감시 유지**: 장 상태에 따라 주문만 차단.
- **리스크 안전장치**: 일 최대거래수, 일 최대손실, 최대보유종목, 연속손실 쿨다운.
- **시크릿 보안 저장**: UI 비밀번호 입력 + 암호화 저장 + 재시작 후 자동 로드.
- **리포트**: 일/월/분기/연 집계, 승률/손익비/MDD/평균보유시간/종목기여도, CSV 다운로드.
- **운영성**: systemd 상시기동, 재부팅 자동재시작, 로그로테이션, `/health` 헬스체크.

## 3) 설치/실행/배포 매뉴얼 (AWS EC2)

### 3-1. 서버 준비

```bash
sudo mkdir -p /opt/AutoTrade-System
sudo chown -R $USER:$USER /opt/AutoTrade-System
cd /opt/AutoTrade-System
# 이 저장소 코드 배치
```

### 3-2. 환경변수(.env)

```bash
cat > .env <<'ENV'
AUTOTRADE_MASTER_PASSPHRASE=change-this-very-strong-passphrase
TZ=Asia/Seoul
ENV
```

### 3-3. 패키지/서비스 설치

```bash
bash scripts/bootstrap_ec2.sh
```

### 3-4. 서비스 제어

```bash
sudo systemctl status autotrade-engine
sudo systemctl status autotrade-ui
sudo systemctl restart autotrade-engine
sudo journalctl -u autotrade-engine -f
curl http://127.0.0.1:8000/health
```

## 4) UI 사용 매뉴얼

1. `http://<EC2-IP>:8501` 접속.
2. **시크릿 탭**에서 APPKEY/APPSECRET/계좌/카카오 토큰을 비밀번호 형식으로 입력 후 저장.
3. **전략 설정 탭**에서 단계별 파라미터를 조정하고 저장(핫리로드).
4. 모드는 `DRY-RUN`으로 검증 후 `LIVE`로 변경.
5. **운영 상태 탭**에서 점수/근거, 포지션, 장상태 확인.
6. **리포트 탭**에서 일/월/분기/연 성과 및 CSV 다운로드.

## 5) 운영 체크리스트

- [x] systemd 24시간 가동
- [x] 시크릿 암호화 저장 및 재시작 유지
- [x] 단계별 돌파구간 파라미터 UI 조정 + 디폴트 제공
- [x] 자동 스캔/진입/청산 루프
- [x] 기간별 성과 리포트 + CSV
- [x] 장 마감 주문 차단(감시는 유지)
- [x] 카카오 알림 연동 (체결/오류)

## 6) LIVE 전환 시 필수 작업

- `app/services/kis_client.py`의 `place_order`, `fetch_universe_quotes`를 실 KIS API 인증/서명/호가 API로 교체.
- 거래소별 세션/시간외 로직 세분화.
- 실서버에서 방화벽/IP화이트리스트, TLS, 모니터링(CloudWatch) 연동.
