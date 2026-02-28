#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/AutoTrade-System"
ENGINE_SVC="autotrade-engine"
UI_SVC="autotrade-ui"

cd "$APP_DIR"

echo "== Git status =="
git status -sb || true
echo

echo "== Update code (fast-forward only) =="
git checkout main
git pull --ff-only origin main
echo

echo "== Install dependencies =="
if [ -x "${APP_DIR}/.venv/bin/pip" ] && [ -f "${APP_DIR}/requirements.txt" ]; then
  "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
else
  echo "skip pip install (.venv or requirements.txt missing)"
fi
echo

echo "== Validate build =="
"${APP_DIR}/.venv/bin/python" -m compileall app tests
"${APP_DIR}/.venv/bin/python" -m unittest discover -s tests -v
echo

echo "== Restart services =="
sudo systemctl restart "$ENGINE_SVC" "$UI_SVC"
sudo systemctl status "$ENGINE_SVC" "$UI_SVC" --no-pager
echo

echo "== Health checks =="
curl -s http://127.0.0.1:8000/health || true
echo
curl -s http://127.0.0.1:8000/status || true
echo
