import unittest

from app.core.cross_signals import enrich_bars_with_cross_signals


class CrossSignalTests(unittest.TestCase):
    def test_detects_golden_cross_and_dead_cross(self):
        closes = [10, 9, 8, 7, 6, 6, 7, 8, 9, 10, 9, 8, 7, 6]
        bars = [{"ts": f"2026-05-24T09:{idx:02d}:00", "close": close} for idx, close in enumerate(closes)]

        enriched, signals = enrich_bars_with_cross_signals(bars, short_window=3, long_window=5)

        signal_types = [row["signal"] for row in signals]
        self.assertIn("GOLDEN_CROSS", signal_types)
        self.assertIn("DEAD_CROSS", signal_types)
        self.assertIn("ma_short", enriched[-1])
        self.assertIn("ma_long", enriched[-1])

    def test_ignores_incomplete_data(self):
        enriched, signals = enrich_bars_with_cross_signals([{"ts": "x", "close": 10}], short_window=3, long_window=5)

        self.assertEqual(enriched[0]["ma_short"], None)
        self.assertEqual(enriched[0]["ma_long"], None)
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
