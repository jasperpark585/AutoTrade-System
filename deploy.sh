#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/AutoTrade-System"
ENGINE_SVC="autotrade-engine"
UI_SVC="autotrade-ui"
VENV_PIP="${APP_DIR}/.venv/bin/pip"
REQ_FILE="${APP_DIR}/requirements.txt"

cd "$APP_DIR"

echo "== 1) Pre-check: git status =="
git status -sb || true
echo

echo "== 2) Safety: stash local changes that often block pull (strategy.yaml) =="
# strategy.yaml 수정 때문에 pull이 막히는 경우가 잦아서 전략 파일만 안전하게 stash
if git status --porcelain | grep -q '^ M strategy.yaml'; then
  git stash push -m "auto-stash strategy.yaml before deploy $(date -Iseconds)" -- strategy.yaml
  echo "stashed: strategy.yaml"
else
  echo "no local change in strategy.yaml"
fi
echo

echo "== 3) Update code: fetch + reset --hard origin/main =="
git fetch origin
git checkout main
git reset --hard origin/main
echo

echo "== 4) Install dependencies (if venv exists) =="
if [ -x "$VENV_PIP" ]; then
  if [ -f "$REQ_FILE" ]; then
    "$VENV_PIP" install -r "$REQ_FILE"
    echo "pip install done"
  else
    echo "requirements.txt not found -> skip pip install"
  fi
else
  echo ".venv pip not found -> skip pip install"
  echo "hint: create venv at ${APP_DIR}/.venv"
fi
echo

echo "== 5) Restart services =="
sudo systemctl restart "$ENGINE_SVC"
sudo systemctl restart "$UI_SVC"
echo

echo "== 6) Quick health check =="
echo "--- git log -1 ---"
git log -1 --oneline
echo
echo "--- systemctl status (top 25 lines) ---"
sudo systemctl status "$ENGINE_SVC" --no-pager -l | head -25
echo
sudo systemctl status "$UI_SVC" --no-pager -l | head -25
echo
echo "--- listening ports (8501/8000) ---"
sudo ss -lntp | egrep ':(8501|8000)\b' || true
echo

echo "== DONE =="
echo "UI: http://3.107.235.180:8501"
