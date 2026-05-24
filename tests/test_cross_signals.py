import unittest

from app.core.cross_signals import MIDLONG_DEAD_CROSS, MIDLONG_GOLDEN_CROSS, enrich_bars_with_cross_signals


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

    def test_detects_midlong_cross_against_senkou_span2(self):
        bars = []
        closes = [40] * 52 + [65, 70, 75, 80, 85, 90, 95, 100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160]
        for idx, close in enumerate(closes):
            bars.append({"ts": f"2026-05-{idx + 1:02d}", "high": max(close, 60), "low": min(close, 40), "close": close})

        enriched, signals = enrich_bars_with_cross_signals(bars)

        self.assertIn("ma20", enriched[-1])
        self.assertIn("senkou_span2", enriched[-1])
        self.assertTrue(any(row["signal"] == MIDLONG_GOLDEN_CROSS for row in signals))
        first = next(row for row in signals if row["signal"] == MIDLONG_GOLDEN_CROSS)
        self.assertIn("support_low", first)
        self.assertIn("resistance_high", first)

    def test_detects_midlong_dead_cross_against_senkou_span2(self):
        bars = []
        closes = [70] * 52 + [35, 32, 30, 28, 25, 22, 20, 18, 16, 14, 12, 10, 9, 8, 7, 6]
        for idx, close in enumerate(closes):
            bars.append({"ts": f"2026-06-{idx + 1:02d}", "high": max(close, 70), "low": min(close, 40), "close": close})

        _, signals = enrich_bars_with_cross_signals(bars)

        self.assertTrue(any(row["signal"] == MIDLONG_DEAD_CROSS for row in signals))


if __name__ == "__main__":
    unittest.main()
