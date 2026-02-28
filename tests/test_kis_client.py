import os
import unittest
from unittest.mock import patch

from app.services.kis_client import KISClient


class KISClientTests(unittest.TestCase):
    def setUp(self):
        os.environ["KIS_APPKEY"] = "appkey"
        os.environ["KIS_APPSECRET"] = "appsecret"
        os.environ["KIS_ACCOUNT_NO"] = "12345678-01"
        os.environ["LIVE"] = "false"
        os.environ["DRY_RUN"] = "false"
        os.environ["KIS_MOCK_ORDER"] = "false"

    def test_place_order_dry_run(self):
        client = KISClient(dry_run=True)
        result = client.place_order("005930", 1, "BUY", 70000)
        self.assertEqual(result["status"], "SIMULATED")

    @patch("app.services.kis_client.get_market_status")
    def test_live_order_requires_explicit_live_flag(self, mock_market):
        mock_market.return_value = type("S", (), {"can_place_order": True, "reason": "OPEN"})()
        client = KISClient(dry_run=False)
        result = client.place_order("005930", 1, "BUY", 70000)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason"], "LIVE_FLAG_REQUIRED")

    @patch("app.services.kis_client.get_market_status")
    def test_live_mock_success_when_live_flag_enabled(self, mock_market):
        mock_market.return_value = type("S", (), {"can_place_order": True, "reason": "OPEN"})()
        os.environ["LIVE"] = "true"
        os.environ["KIS_MOCK_ORDER"] = "true"
        client = KISClient(dry_run=False)
        result = client.place_order("005930", 2, "BUY", 70000)
        self.assertEqual(result["status"], "FILLED")


if __name__ == "__main__":
    unittest.main()
