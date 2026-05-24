#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_NAME="youtube-trend-bot"
STUDIO_SERVICE_NAME="youtube-trend-studio"
DEPLOY_ID="${DEPLOY_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export LANG="C.UTF-8"
export LC_ALL="C.UTF-8"
export PYTHONUTF8="1"
export PYTHONIOENCODING="utf-8"

cd "$PROJECT_ROOT"

if [ -d .git ]; then
  git pull --ff-only || true
fi

if [ ! -d "$VENV_PATH" ]; then
  "$PYTHON_BIN" -m venv "$VENV_PATH"
fi

sudo apt-get clean
sudo rm -rf /var/lib/apt/lists/*
sudo dpkg --configure -a || true
sudo apt-get update
sudo apt-get install -y --no-install-recommends curl ffmpeg
sudo apt-get clean

mkdir -p "$PROJECT_ROOT/vendor/cloudflared"
if [ ! -x "$PROJECT_ROOT/vendor/cloudflared/cloudflared" ]; then
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
    -o "$PROJECT_ROOT/vendor/cloudflared/cloudflared"
  chmod +x "$PROJECT_ROOT/vendor/cloudflared/cloudflared"
fi

source "$VENV_PATH/bin/activate"
python -m pip install --upgrade pip --no-cache-dir
python -m pip install --no-cache-dir -r requirements.txt
python -m pip cache purge || true
python -m compileall app tests main.py studio_app.py studio_launcher.py
pytest tests
python main.py --mode dry-run --allow-network false
python main.py --mode run-once --allow-network false --skip-upload --force
python main.py --mode mark-server-update --deploy-id "$DEPLOY_ID"
rm -rf "$PROJECT_ROOT/outputs/audio"/* "$PROJECT_ROOT/outputs/backgrounds"/* "$PROJECT_ROOT/outputs/subtitles"/* "$PROJECT_ROOT/outputs/thumbnails"/* "$PROJECT_ROOT/outputs/videos"/*

mkdir -p "$PROJECT_ROOT/logs"
sudo cp "$PROJECT_ROOT/deploy/systemd/youtube-trend-bot.service" "/etc/systemd/system/youtube-trend-bot.service"
sudo cp "$PROJECT_ROOT/deploy/systemd/youtube-trend-studio.service" "/etc/systemd/system/youtube-trend-studio.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl enable "$STUDIO_SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl restart "$STUDIO_SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager
sudo systemctl status "$STUDIO_SERVICE_NAME" --no-pager
