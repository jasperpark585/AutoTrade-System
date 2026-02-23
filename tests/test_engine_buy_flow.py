import unittest
from unittest.mock import Mock

from app.core.database import Database
from app.core.engine import AutoTradingEngine
from app.services.kakao import KakaoNotifier
from app.services.kis_client import KISError, Quote


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
                "universe": {"max_spread_pct": 1.2, "use_allowlist": False, "allowlist_symbols": []},
                "pre_breakout": {"volume_spike_ratio_min": 2.2, "intraday_volatility_pct_min": 1.8},
                "trigger": {"breakout_zone_1_pct": 0.6, "breakout_zone_2_pct": 1.2, "breakout_zone_3_pct": 2.0},
                "confirmation": {"execution_strength_min": 105, "spread_pct_max": 0.9, "trend_slope_min": 0.2},
                "exit": {"stop_loss_pct": 1.8, "take_profit_pct": 4.2},
            },
        }


class EngineBuyFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = AutoTradingEngine(DummyConfigManager(), Database(), KakaoNotifier(None))

    def test_skip_insufficient_cash_then_try_next(self):
        self.engine.kis.get_account_summary = Mock(return_value={"available_cash": 200000})

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

    def test_api_error_stops_loop(self):
        self.engine.kis.get_account_summary = Mock(return_value={"available_cash": 10_000_000})
        candidates = [
            {"symbol": "A", "pass_fail": "PASS", "price": 100000, "reason": "ok", "total_score": 90, "stage_scores": {}, "stage_checks": {}, "strategy_pass": True},
            {"symbol": "B", "pass_fail": "PASS", "price": 100000, "reason": "ok", "total_score": 80, "stage_scores": {}, "stage_checks": {}, "strategy_pass": True},
        ]

        def fail_try(symbol, price, reason, qty=None):
            raise KISError("api fail", detail={"http_status": 503, "rt_cd": "9", "msg1": "server error"})

        self.engine._try_entry = fail_try  # type: ignore[method-assign]
        with self.assertRaises(KISError):
            self.engine._attempt_buy_candidates(candidates)


if __name__ == "__main__":
    unittest.main()
