import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.gpt_scout import GPTScout, ScoutRankConfig


class GPTScoutTests(unittest.TestCase):
    def test_low_price_candidate_is_prioritized(self):
        with tempfile.TemporaryDirectory() as td:
            scout = GPTScout(snapshot_path=str(Path(td) / "gpt_candidates.json"))
            ranked = scout._rank_candidates(
                [
                    {
                        "symbol": "005930",
                        "price_krw": 78000,
                        "momentum_score": 82,
                        "pre_breakout_score": 84,
                        "overheat_score": 65,
                    },
                    {
                        "symbol": "003490",
                        "price_krw": 14500,
                        "momentum_score": 75,
                        "pre_breakout_score": 80,
                        "overheat_score": 28,
                    },
                ],
                ScoutRankConfig(max_candidates=5, prefer_price_krw=40000, price_cap_krw=120000, overheat_score_limit=75),
            )
            self.assertEqual(ranked[0]["symbol"], "003490")

    def test_refresh_uses_fallback_when_no_api_key(self):
        with tempfile.TemporaryDirectory() as td:
            scout = GPTScout(
                snapshot_path=str(Path(td) / "gpt_candidates.json"),
                guard_state_path=str(Path(td) / "openai_guard_state.json"),
            )
            payload = scout.refresh_daily_candidates(
                cfg={"max_candidates": 3, "api_key": ""},
                fallback_symbols=["005930", "000660", "035420"],
            )
            self.assertTrue(payload["source"].startswith("FALLBACK"))
            self.assertTrue(len(payload["symbols"]) >= 1)

    def test_guard_blocks_without_paid_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            scout = GPTScout(
                snapshot_path=str(Path(td) / "gpt_candidates.json"),
                guard_state_path=str(Path(td) / "openai_guard_state.json"),
            )
            cfg = {
                "max_candidates": 3,
                "api_key": "dummy-key",
                "quota_guard": {
                    "enabled": True,
                    "require_paid_opt_in": True,
                    "paid_opt_in_env": "OPENAI_PAID_ALLOWED_TEST",
                },
            }
            with patch.dict(os.environ, {"OPENAI_PAID_ALLOWED_TEST": "false"}, clear=False):
                payload = scout.refresh_daily_candidates(cfg=cfg, fallback_symbols=["005930", "000660", "035420"])
            self.assertEqual(payload["source"], "FALLBACK:OPENAI_PAID_OPT_IN_REQUIRED")
            guard = payload.get("openai_guard", {})
            self.assertTrue(bool(guard.get("require_paid_opt_in")))

    def test_affordable_price_cap_filters_expensive_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            scout = GPTScout(
                snapshot_path=str(Path(td) / "gpt_candidates.json"),
                guard_state_path=str(Path(td) / "openai_guard_state.json"),
            )
            mocked_candidates = [
                {
                    "symbol": "005930",
                    "name": "A",
                    "price_krw": 120000,
                    "momentum_score": 80,
                    "pre_breakout_score": 80,
                    "overheat_score": 30,
                },
                {
                    "symbol": "003490",
                    "name": "B",
                    "price_krw": 42000,
                    "momentum_score": 72,
                    "pre_breakout_score": 76,
                    "overheat_score": 28,
                },
            ]
            cfg = {"max_candidates": 5, "api_key": "dummy", "affordable_price_cap_krw": 50000}
            with patch.object(scout, "_request_openai_candidates", return_value=(mocked_candidates, "OPENAI_OK")):
                payload = scout.refresh_daily_candidates(cfg=cfg, fallback_symbols=["005930", "000660", "035420"])
            self.assertEqual(payload["source"], "OPENAI_OK")
            self.assertEqual(payload["symbols"], ["003490"])
            self.assertEqual(payload["rank_config"]["affordable_price_cap_krw"], 50000.0)


if __name__ == "__main__":
    unittest.main()
