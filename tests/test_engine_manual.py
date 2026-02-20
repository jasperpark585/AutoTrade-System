import os
import unittest
from unittest.mock import patch

from app.core.database import Database
from app.core.engine import AutoTradingEngine
from app.services.kakao import KakaoNotifier
from app.services.kis_client import Quote


class DummyConfigManager:
    def load(self):
        return {
            "mode": "DRY-RUN",
            "scan_interval_seconds": 60,
            "risk_limits": {
                "max_daily_trades": 8,
                "max_orders_per_day": 8,
                "max_daily_loss_krw": 600000,
                "max_daily_loss_pct": 2.5,
                "equity_base_krw": 30000000,
                "max_positions": 4,
                "max_buy_amount_per_trade_krw": 1500000,
                "cooldown_after_consecutive_losses": 3,
                "cooldown_minutes": 20,
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


class EngineManualTests(unittest.TestCase):
    def setUp(self):
        os.environ["KIS_APPKEY"] = "appkey"
        os.environ["KIS_APPSECRET"] = "appsecret"
        os.environ["KIS_ACCOUNT_NO"] = "12345678-01"
        self.db = Database()
        self.engine = AutoTradingEngine(DummyConfigManager(), self.db, KakaoNotifier(None))

    @patch("app.core.engine.get_market_status")
    @patch("app.services.kis_client.KISClient.fetch_universe_quotes")
    def test_manual_diagnosis_returns_blocker_when_market_closed(self, mock_quotes, mock_market):
        mock_market.return_value = type("S", (), {"can_place_order": False, "reason": "장마감", "is_open": False})()
        mock_quotes.return_value = [
            Quote("005930", 70000, 3.0, 2.2, 120, 0.5, 0.4),
        ]

        result = self.engine.run_manual_diagnosis()
        self.assertIn("rows", result)
        self.assertEqual(len(result["rows"]), 1)
        self.assertFalse(result["rows"][0]["can_auto_order_now"])
        self.assertIn("시장", result["rows"][0]["blocker"])

    def test_risk_check_limit_by_order_count(self):
        self.engine.runtime.daily_trades = int(self.engine.config["risk_limits"]["max_orders_per_day"])
        ok, reason = self.engine.risk_check_detail()
        self.assertFalse(ok)
        self.assertIn("주문횟수", reason)


if __name__ == "__main__":
    unittest.main()
