#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git curl

cd /opt/AutoTrade-System
mkdir -p data logs
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f /opt/AutoTrade-System/.env ]; then
  cat > /opt/AutoTrade-System/.env <<'ENV'
TZ=Asia/Seoul
LOG_LEVEL=INFO
LIVE=false
DRY_RUN=true
KIS_MOCK_ORDER=false
OPENAI_PAID_ALLOWED=false
ENV
  chmod 600 /opt/AutoTrade-System/.env || true
fi

sudo cp app/deploy/autotrade-engine.service /etc/systemd/system/
sudo cp app/deploy/autotrade-ui.service /etc/systemd/system/
sudo cp app/deploy/logrotate-autotrade /etc/logrotate.d/autotrade
sudo systemctl daemon-reload
sudo systemctl enable autotrade-engine autotrade-ui
sudo systemctl restart autotrade-engine autotrade-ui
