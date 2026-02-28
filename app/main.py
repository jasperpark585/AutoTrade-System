from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.core.config import ConfigManager
from app.core.database import Database
from app.core.engine import AutoTradingEngine
from app.services.kakao import KakaoNotifier
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    engine: AutoTradingEngine | None = None

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = max(0, int(raw_length))
        except ValueError:
            return {}
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        if not self.engine:
            self._write_json(503, {"ok": False, "reason": "ENGINE_UNAVAILABLE"})
            return

        if self.path in {"/health", "/status"}:
            payload = {"ok": True, "engine": self.engine.heartbeat()}
            self._write_json(200, payload)
            return

        if self.path == "/config":
            self._write_json(200, {"ok": True, "config": self.engine.get_config_summary()})
            return

        if self.path.startswith("/trades"):
            limit = 200
            if "?" in self.path:
                try:
                    query = self.path.split("?", 1)[1]
                    for part in query.split("&"):
                        if part.startswith("limit="):
                            limit = int(part.split("=", 1)[1])
                            break
                except Exception:
                    limit = 200
            rows = self.engine.get_recent_trades(limit=limit)
            self._write_json(200, {"ok": True, "rows": rows})
            return

        if self.path == "/candidates":
            payload = self.engine.get_watchlist_payload()
            self._write_json(200, {"ok": True, "result": payload})
            return

        if self.path.startswith("/chart"):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            symbol = str((params.get("symbol") or [""])[0]).strip()
            try:
                count = int((params.get("count") or ["120"])[0])
            except Exception:
                count = 120
            if not symbol:
                self._write_json(400, {"ok": False, "reason": "symbol is required"})
                return
            result = self.engine.get_symbol_chart(symbol=symbol, count=count)
            self._write_json(200, {"ok": True, "result": result})
            return

        self._write_json(404, {"ok": False, "reason": "NOT_FOUND"})

    def do_POST(self):  # noqa: N802
        if not self.engine:
            self._write_json(503, {"ok": False, "reason": "ENGINE_UNAVAILABLE"})
            return

        if self.path in {"/refresh/portfolio", "/portfolio/refresh"}:
            result = self.engine.refresh_portfolio_snapshot(force=True, trigger="manual")
            self._write_json(200, {"ok": bool(result.get("ok")), "result": result})
            return

        if self.path == "/report/clear":
            payload = self._read_json_body()
            only_dry = bool(payload.get("only_dry", True))
            vacuum = bool(payload.get("vacuum", False))
            result = self.engine.clear_report_data(only_dry=only_dry, vacuum=vacuum)
            self._write_json(200, {"ok": True, "result": result})
            return

        if self.path == "/engine/enable":
            payload = self._read_json_body()
            enabled = bool(payload.get("enabled", False))
            self.engine.set_auto_trading_enabled(enabled)
            self._write_json(200, {"ok": True, "enabled": enabled})
            return

        if self.path == "/config/mode":
            payload = self._read_json_body()
            try:
                saved = self.engine.set_mode(str(payload.get("mode", "")))
            except Exception as exc:
                self._write_json(400, {"ok": False, "reason": str(exc)})
                return
            self._write_json(200, {"ok": True, "config": saved})
            return

        if self.path == "/candidates/refresh":
            payload = self._read_json_body()
            force = bool(payload.get("force", True))
            result = self.engine.refresh_daily_candidates(force=force, trigger="manual")
            self._write_json(200, {"ok": bool(result.get("ok", True)), "result": result})
            return

        self._write_json(404, {"ok": False, "reason": "NOT_FOUND"})


def run() -> None:
    setup_logging()
    cfg_mgr = ConfigManager()
    db = Database()
    notifier = KakaoNotifier(token=os.getenv("KAKAO_TOKEN"))
    engine = AutoTradingEngine(cfg_mgr, db, notifier)

    if db.get_engine_state("auto_trading_enabled") is None:
        engine.set_auto_trading_enabled(False)

    HealthHandler.engine = engine
    server = HTTPServer(("0.0.0.0", 8000), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Engine API server on :8000 (/health, /status)")

    while True:
        cfg = cfg_mgr.load()
        engine.tick()
        time.sleep(max(5, int(cfg.get("scan_interval_seconds", 60))))


if __name__ == "__main__":
    run()
