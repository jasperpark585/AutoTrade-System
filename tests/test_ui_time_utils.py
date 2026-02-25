import unittest

from app.ui.time_utils import format_retry_time_kst


class UITimeUtilsTests(unittest.TestCase):
    def test_format_retry_time_kst_from_utc_iso(self):
        kst, utc = format_retry_time_kst("2026-01-01T00:00:00")
        self.assertIn("KST", kst)
        self.assertTrue(kst.startswith("2026-01-01 09:00:00"))
        self.assertTrue(utc.startswith("2026-01-01T00:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()
