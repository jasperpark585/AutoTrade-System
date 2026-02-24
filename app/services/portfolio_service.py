from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class PortfolioService:
    def __init__(self, snapshot_path: str = "data/portfolio_snapshot.json", ttl_sec: int = 45, min_refresh_gap_sec: int = 5):
        self.path = Path(snapshot_path)
        self.ttl_sec = ttl_sec
        self.min_refresh_gap_sec = min_refresh_gap_sec

    def get_snapshot(self, force_refresh: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        now = time.time()
        if not self.path.exists():
            return None, {"throttled": False, "reason": "NO_CACHE"}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ts = float(data.get("cache_ts", 0))
            age = now - ts
            if force_refresh and age < self.min_refresh_gap_sec:
                return data.get("payload"), {"throttled": True, "reason": "REFRESH_THROTTLED", "age_sec": round(age, 2)}
            if age <= self.ttl_sec:
                return data.get("payload"), {"throttled": False, "reason": "CACHE_HIT", "age_sec": round(age, 2)}
        except Exception:
            return None, {"throttled": False, "reason": "CACHE_CORRUPTED"}
        return None, {"throttled": False, "reason": "CACHE_EXPIRED"}

    def get_cached(self) -> dict[str, Any] | None:
        snap, _ = self.get_snapshot(force_refresh=False)
        return snap

    def set_cached(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, {"cache_ts": time.time(), "payload": payload, "updated_at": datetime.utcnow().isoformat()})
