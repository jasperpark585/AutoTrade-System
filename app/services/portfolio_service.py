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
    def __init__(self, snapshot_path: str = "data/portfolio_snapshot.json", ttl_sec: int = 20):
        self.path = Path(snapshot_path)
        self.ttl_sec = ttl_sec

    def get_cached(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ts = data.get("cache_ts", 0)
            if time.time() - ts <= self.ttl_sec:
                return data.get("payload")
        except Exception:
            return None
        return None

    def set_cached(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, {"cache_ts": time.time(), "payload": payload, "updated_at": datetime.utcnow().isoformat()})
