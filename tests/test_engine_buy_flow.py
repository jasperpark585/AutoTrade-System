import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.core.database import Database
from app.core.engine import AutoTradingEngine
from app.services.kakao import KakaoNotifier
from app.services.kis_client import KISCooldownError, Quote


class DummyConfigManager:
    def load(self):
        return {
            "mode": "DRY-RUN",
            "scan_interval_seconds": 60,
            "risk_limits": {
                "max_daily_trades": 8,
                "max_orders_per_day": 8,
                "max_positions": 4,
                "max_buy_amount_per_trade_krw": 1500000,
            },
            "scoring_weights": {"universe": 20, "pre_breakout": 25, "trigger": 30, "confirmation": 25},
            "stages": {
                "universe": {"max_spread_pct": 1.2},
                "pre_breakout": {"volume_spike_ratio_min": 2.2, "intraday_volatility_pct_min": 1.8},
                "trigger": {"breakout_zone_1_pct": 0.6, "breakout_zone_2_pct": 1.2, "breakout_zone_3_pct": 2.0},
                "confirmation": {"execution_strength_min": 105, "spread_pct_max": 0.9, "trend_slope_min": 0.2},
                "exit": {"stop_loss_pct": 1.8, "take_profit_pct": 4.2},
            },
        }

    def runtime_flags(self, config):
        return {
            "mode": "DRY-RUN",
            "explicit_live": False,
            "env_dry_run": True,
            "dry_run": True,
            "mock_order": False,
            "live_order_enabled": False,
        }


class EngineBuyFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "autotrade.db")
        self.engine = AutoTradingEngine(DummyConfigManager(), self.db, KakaoNotifier(None))

    def tearDown(self):
        self._tmp.cleanup()

    @patch("app.core.engine.get_market_status")
    def test_cooldown_sets_transient_blocker_not_fatal(self, mock_market):
        mock_market.return_value = type("S", (), {"can_place_order": True, "reason": "OPEN", "is_open": True})()
        self.engine.set_auto_trading_enabled(True)
        self.engine.kis.fetch_account_summary = Mock(return_value={"available_cash": 1000000})
        self.engine.kis.fetch_universe_quotes = Mock(
            side_effect=KISCooldownError(
                "cooldown",
                detail={"reason": "KIS_TOKEN_COOLDOWN", "next_retry_at": "2099-01-01T00:00:00", "http_status": 403},
            )
        )

        self.engine.tick()

        hb = self.engine.heartbeat()
        self.assertTrue(hb["enabled"])
        self.assertEqual(hb["blocker"], "KIS_TOKEN_COOLDOWN")
        self.assertEqual(hb["next_retry_at"], "2099-01-01T00:00:00")
        self.assertFalse(bool(hb["fatal_error"]))

    def test_skip_insufficient_cash_then_try_next(self):
        self.engine.kis.fetch_account_summary = Mock(return_value={"available_cash": 200000})
        candidates = [
            {"symbol": "A", "pass_fail": "PASS", "price": 300000, "reason": "ok", "total_score": 90, "stage_scores": {}, "stage_checks": {}, "strategy_pass": True},
            {"symbol": "B", "pass_fail": "PASS", "price": 100000, "reason": "ok", "total_score": 80, "stage_scores": {}, "stage_checks": {}, "strategy_pass": True},
        ]
        called = []

        def fake_try(symbol, price, reason, qty=None):
            called.append(symbol)

        self.engine._try_entry = fake_try  # type: ignore[method-assign]
        self.engine._attempt_buy_candidates(candidates)
        self.assertEqual(called, ["B"])

    def test_build_candidates(self):
        quotes = [Quote("005930", 70000, 3.0, 2.3, 120.0, 0.2, 0.4)]
        rows = self.engine.build_candidates(quotes)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "005930")


if __name__ == "__main__":
    unittest.main()
