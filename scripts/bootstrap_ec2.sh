#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip

cd /opt/AutoTrade-System
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

sudo cp app/deploy/autotrade-engine.service /etc/systemd/system/
sudo cp app/deploy/autotrade-ui.service /etc/systemd/system/
sudo cp app/deploy/logrotate-autotrade /etc/logrotate.d/autotrade
sudo systemctl daemon-reload
sudo systemctl enable autotrade-engine autotrade-ui
sudo systemctl restart autotrade-engine autotrade-ui
