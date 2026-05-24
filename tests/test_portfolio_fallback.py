import unittest

from app.ui.portfolio_fallback import choose_portfolio_snapshot


class PortfolioFallbackTests(unittest.TestCase):
    def test_live_refresh_snapshot_wins_over_other_sources(self):
        chosen = choose_portfolio_snapshot(
            live_result={"ok": True, "result": {"snapshot": {"ts": "live"}}},
            cached_snapshot={"ts": "cached"},
            file_snapshot={"ts": "file"},
            session_snapshot={"ts": "session"},
        )

        self.assertEqual(chosen.snapshot, {"ts": "live"})
        self.assertEqual(chosen.source, "live_refresh")
        self.assertIsNone(chosen.warning)

    def test_falls_back_in_documented_order_with_warning(self):
        chosen = choose_portfolio_snapshot(
            live_result={"ok": False, "result": {"reason": "KIS_TOKEN_COOLDOWN"}},
            cached_snapshot=None,
            file_snapshot={"ts": "file"},
            session_snapshot={"ts": "session"},
        )

        self.assertEqual(chosen.snapshot, {"ts": "file"})
        self.assertEqual(chosen.source, "file_snapshot")
        self.assertIn("KIS_TOKEN_COOLDOWN", chosen.warning or "")


if __name__ == "__main__":
    unittest.main()
