# AutoTrade-System

Production-focused stock auto trading system for KR market operation:

- Engine loop (`python -m app.main`)
- KIS broker integration (token, quotes, account, orders)
- Risk guardrails (market status, order/position limits, cooldown blocker)
- Daily pre-market AI scouting (OpenAI) + watchlist auto registration
- Low-price pre-breakout bias for candidate ranking
- Position auto-management (stop, take-profit, trailing, weak-trend/time exit)
- Hourly Kakao status updates during market hours
- Persistent state/log storage (SQLite + rotating logs)
- Alerting (Kakao notifier)
- Minimal operations UI (Streamlit monitor)

## Project Layout

```text
app/
  main.py                     # engine entrypoint + HTTP API
  core/
    config.py                # YAML/env config + runtime mode flags
    database.py              # sqlite storage
    engine.py                # trading loop
    market_hours.py          # KR market open/close check
    risk.py                  # risk guardrails
    strategy.py              # candidate scoring
  services/
    kis_client.py            # KIS integration
    kakao.py                 # notifier
    portfolio_service.py     # snapshot cache
  ui/
    streamlit_app.py         # minimal operations UI
    time_utils.py            # UTC/KST formatting
  utils/
    logging.py               # rotating file logging
    errors.py
  deploy/
    autotrade-engine.service
    autotrade-ui.service
    logrotate-autotrade
scripts/
  bootstrap_ec2.sh
strategy.yaml
requirements.txt
```

## Safety Rules

- Real account orders are blocked unless all are true:
  - `mode: LIVE` in `strategy.yaml` (or `MODE=LIVE`)
  - `LIVE=true` in environment
  - `DRY_RUN=false` in environment
  - `KIS_MOCK_ORDER=false`
- If market is closed, token cooldown is active, or required env vars are missing, order calls are blocked.
- KIS token cooldown (`KIS_TOKEN_COOLDOWN`) is treated as transient blocker, not fatal stop.

## Environment

Create `/opt/AutoTrade-System/.env`:

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

# order safety flags
LIVE=false
DRY_RUN=true
KIS_MOCK_ORDER=false
```

OpenAI 충전/한도 설정은 [docs/openai_billing_guide_ko.md](docs/openai_billing_guide_ko.md)를 참고하세요.

## Local Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Run UI (optional, separate terminal):

```bash
. .venv/bin/activate
streamlit run app/ui/streamlit_app.py --server.port 8501 --server.address 0.0.0.0
```

## Server (systemd)

```bash
sudo cp app/deploy/autotrade-engine.service /etc/systemd/system/
sudo cp app/deploy/autotrade-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable autotrade-engine autotrade-ui
sudo systemctl restart autotrade-engine autotrade-ui
```

Restart snippet:

```bash
sudo systemctl restart autotrade-engine
sudo systemctl restart autotrade-ui
sudo systemctl status autotrade-engine autotrade-ui --no-pager
```

## Engine HTTP API

- `GET /health`
- `GET /status`
- `GET /candidates`
- `GET /chart?symbol=005930&count=180`
- `POST /refresh/portfolio`
- `POST /candidates/refresh` body: `{"force": true}`
- `POST /report/clear` body: `{"only_dry": true, "vacuum": false}`
- `POST /engine/enable` body: `{"enabled": true}`

## DRY-RUN / LIVE Switch

1. Dry run mode (safe default):
   - `mode: DRY-RUN`
   - `LIVE=false`
2. Live mode:
   - `mode: LIVE`
   - `LIVE=true`
   - `DRY_RUN=false`
   - `KIS_MOCK_ORDER=false`

## Minimal Test

```bash
python -m compileall app tests
python -m unittest discover -s tests -v
python -m app.main
curl -s http://127.0.0.1:8000/status
streamlit run app/ui/streamlit_app.py --server.port 8501
```

## Strategy knobs (strategy.yaml)

- `gpt_scout`:
  - `allow_external_call=true` enables OpenAI call only for scheduled refresh
  - `premarket_refresh_time_kst` sets daily pre-market refresh time
  - `prefer_price_krw`, `price_cap_krw` bias low-price/pre-breakout names
  - `quota_guard` enforces paid safety limits:
    - `require_paid_opt_in=true` + `paid_opt_in_env=OPENAI_PAID_ALLOWED`
    - `max_requests_per_day`, `max_requests_per_month`
    - `max_monthly_cost_usd`, `reserve_ratio`
    - `max_estimated_cost_per_call_usd`
    - `cooldown_minutes_on_http_429`
- `position_management`:
  - `trailing_stop_pct`, `max_holding_hours`, `weak_trend_exit_slope`
- `hourly_alert`:
  - `enabled`, `minute`, `grace_minutes`
