import tempfile
import unittest
from pathlib import Path

from app.core.database import Database


class DatabaseReportClearTests(unittest.TestCase):
    def test_clear_report_data_preserves_open_positions(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(Path(td) / "autotrade.db")
            t1 = db.open_trade("005930", 1, 70000, "open")
            db.close_trade(t1, 71000, 0, "close")
            db.open_trade("000660", 1, 120000, "open")
            db.insert_signal("005930", 1.0, "{}", "PASS", "reason")

            out = db.clear_report_data(only_dry=True, vacuum=False)

            self.assertIn("trades", out["deleted"])
            self.assertIn("signals", out["deleted"])
            self.assertGreaterEqual(out["deleted"]["trades"], 1)
            self.assertGreaterEqual(out["deleted"]["signals"], 1)

            open_trades = db.fetch_df("SELECT * FROM trades WHERE status='OPEN'")
            closed_trades = db.fetch_df("SELECT * FROM trades WHERE status='CLOSED'")
            self.assertEqual(len(open_trades), 1)
            self.assertEqual(len(closed_trades), 0)


if __name__ == "__main__":
    unittest.main()
