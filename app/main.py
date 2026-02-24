from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.core.config import ConfigManager
from app.core.database import Database
from app.core.engine import AutoTradingEngine
from app.services.kakao import KakaoNotifier
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    engine: AutoTradingEngine | None = None

    def do_GET(self):  # noqa: N802
        if self.path not in {"/health", "/status"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = {"ok": True, "engine": self.engine.heartbeat() if self.engine else {}}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):  # noqa: N802
        if self.path not in {"/refresh/portfolio", "/portfolio/refresh"}:
            self.send_response(404)
            self.end_headers()
            return
        if not self.engine:
            self.send_response(503)
            self.end_headers()
            return
        result = self.engine.refresh_portfolio_snapshot(force=True, trigger="manual")
        body = json.dumps({"ok": bool(result.get("ok")), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)



def run() -> None:
    setup_logging()
    cfg_mgr = ConfigManager()
    db = Database()

    notifier = KakaoNotifier(token=os.getenv("KAKAO_TOKEN"))
    engine = AutoTradingEngine(cfg_mgr, db, notifier)
    # default is OFF; keep persisted operator selection across restarts
    if db.get_engine_state("auto_trading_enabled") is None:
        engine.set_auto_trading_enabled(False)

    HealthHandler.engine = engine
    server = HTTPServer(("0.0.0.0", 8000), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server running on :8000/health and :8000/status")

    while True:
        cfg = cfg_mgr.load()
        engine.tick()
        time.sleep(max(5, int(cfg.get("scan_interval_seconds", 60))))


if __name__ == "__main__":
    run()
