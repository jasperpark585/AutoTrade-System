import unittest

from app.core.strategy import StageStrategy
from app.services.kis_client import Quote


def _config():
    return {
        "scoring_weights": {"universe": 20, "pre_breakout": 25, "trigger": 30, "confirmation": 25},
        "stages": {
            "universe": {"max_spread_pct": 1.2},
            "pre_breakout": {"volume_spike_ratio_min": 2.2, "intraday_volatility_pct_min": 1.8},
            "trigger": {"breakout_zone_1_pct": 0.6, "breakout_zone_2_pct": 1.2, "breakout_zone_3_pct": 2.0},
            "confirmation": {
                "execution_strength_min": 105,
                "spread_pct_max": 0.9,
                "trend_slope_min": 0.2,
                "golden_cross_bonus": 5,
                "block_dead_cross": True,
            },
            "exit": {"stop_loss_pct": 1.8, "take_profit_pct": 4.2},
        },
    }


class StrategyCrossSignalTests(unittest.TestCase):
    def test_golden_cross_adds_confirmation_bonus(self):
        strategy = StageStrategy(_config())
        quote = Quote("005930", 70000, 3.0, 2.3, 120.0, 0.2, 0.4, cross_signal="GOLDEN_CROSS")

        result = strategy.evaluate(quote)

        self.assertTrue(result.passed)
        self.assertEqual(result.stage_scores["cross"], 5)
        self.assertIn("GOLDEN_CROSS", result.reason)

    def test_dead_cross_blocks_entry(self):
        strategy = StageStrategy(_config())
        quote = Quote("005930", 70000, 3.0, 2.3, 120.0, 0.2, 0.4, cross_signal="DEAD_CROSS")

        result = strategy.evaluate(quote)

        self.assertFalse(result.passed)
        self.assertFalse(result.stage_checks["confirmation"]["passed"])
        self.assertIn("DEAD_CROSS", result.reason)


if __name__ == "__main__":
    unittest.main()
