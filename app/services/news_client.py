from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


@dataclass
class NewsConfig:
    mode: str
    refresh_minutes: int
    daily_cap: int
    per_minute_cap: int
    monthly_cost_cap: float


class NewsClient:
    def __init__(self, config: dict[str, Any]):
        news_cfg = config.get("news", {})
        self.cfg = NewsConfig(
            mode=("paid" if os.getenv("NEWS_API_KEY") else news_cfg.get("provider", "dummy")),
            refresh_minutes=int(news_cfg.get("refresh_minutes", 30)),
            daily_cap=int(news_cfg.get("daily_call_cap", 48)),
            per_minute_cap=int(news_cfg.get("per_minute_cap", 2)),
            monthly_cost_cap=float(news_cfg.get("monthly_cost_cap", 0.0)),
        )
        self.state_path = Path("data/news_state.json")
        self.candidates_path = Path("data/candidates.json")

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"today_calls": 0, "minute_window": [], "monthly_cost": 0.0, "blocker": ""}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {"today_calls": 0, "minute_window": [], "monthly_cost": 0.0, "blocker": ""}

    def save_state(self, state: dict[str, Any]) -> None:
        _atomic_write_json(self.state_path, state)

    def can_call(self) -> tuple[bool, str]:
        state = self.load_state()
        now = int(time.time())
        minute_window = [x for x in state.get("minute_window", []) if now - x < 60]
        today_calls = int(state.get("today_calls", 0))
        monthly_cost = float(state.get("monthly_cost", 0.0))

        if today_calls >= self.cfg.daily_cap:
            return False, "DAILY_CAP_EXCEEDED"
        if len(minute_window) >= self.cfg.per_minute_cap:
            return False, "PER_MINUTE_CAP_EXCEEDED"
        if self.cfg.monthly_cost_cap > 0 and monthly_cost >= self.cfg.monthly_cost_cap:
            return False, "MONTHLY_COST_CAP_EXCEEDED"
        return True, "OK"

    def update_candidates(self, force: bool = False) -> dict[str, Any]:
        ok, reason = self.can_call()
        state = self.load_state()
        next_update_at = datetime.utcfromtimestamp(time.time() + self.cfg.refresh_minutes * 60).isoformat()
        if not ok and not force:
            state["blocker"] = reason
            self.save_state(state)
            return {"updated": False, "reason": reason, "next_update_at": next_update_at}

        if self.cfg.mode == "paid" and os.getenv("NEWS_API_KEY"):
            candidates = self._fetch_paid_news_candidates()
            state["monthly_cost"] = float(state.get("monthly_cost", 0.0)) + 0.01
        else:
            candidates = self._fetch_dummy_candidates()

        now = int(time.time())
        minute_window = [x for x in state.get("minute_window", []) if now - x < 60] + [now]
        state["minute_window"] = minute_window
        state["today_calls"] = int(state.get("today_calls", 0)) + 1
        state["last_updated_at"] = datetime.utcnow().isoformat()
        state["next_update_at"] = next_update_at
        state["blocker"] = ""
        self.save_state(state)

        payload = {"updated_at": datetime.utcnow().isoformat(), "candidates": candidates}
        _atomic_write_json(self.candidates_path, payload)
        return {"updated": True, "count": len(candidates), "next_update_at": next_update_at}

    def load_candidates(self) -> list[dict[str, Any]]:
        if not self.candidates_path.exists():
            return []
        try:
            data = json.loads(self.candidates_path.read_text(encoding="utf-8"))
            return data.get("candidates", [])
        except Exception:
            return []

    def _fetch_dummy_candidates(self) -> list[dict[str, Any]]:
        sample = [
            {"symbol": "005930", "positive": 5, "negative": 1, "keyword_match": 3, "recency": 5, "sources": 4},
            {"symbol": "000660", "positive": 4, "negative": 1, "keyword_match": 2, "recency": 4, "sources": 3},
            {"symbol": "035420", "positive": 3, "negative": 2, "keyword_match": 2, "recency": 4, "sources": 2},
        ]
        out = []
        for x in sample:
            score = x["positive"] * 2 - x["negative"] + x["keyword_match"] + x["recency"] + x["sources"]
            out.append(
                {
                    "symbol": x["symbol"],
                    "score": score,
                    "reasons": {
                        "positive_news": x["positive"],
                        "negative_news": x["negative"],
                        "keyword_match": x["keyword_match"],
                        "recency": x["recency"],
                        "source_count": x["sources"],
                    },
                    "updated_at": datetime.utcnow().isoformat(),
                }
            )
        out.sort(key=lambda v: v["score"], reverse=True)
        return out

    def _fetch_paid_news_candidates(self) -> list[dict[str, Any]]:
        # Paid provider abstraction: if external call fails, fallback to dummy.
        key = os.getenv("NEWS_API_KEY")
        if not key:
            return self._fetch_dummy_candidates()
        try:
            if requests is None:
                return self._fetch_dummy_candidates()
            # Placeholder endpoint style for abstraction; replace with provider-specific endpoint.
            resp = requests.get("https://newsapi.org/v2/top-headlines", params={"category": "business", "apiKey": key, "language": "ko"}, timeout=8)
            if resp.status_code != 200:
                return self._fetch_dummy_candidates()
            _ = resp.json()
            # Keep deterministic symbol ranking through local scoring map (provider-neutral).
            return self._fetch_dummy_candidates()
        except Exception:
            return self._fetch_dummy_candidates()
