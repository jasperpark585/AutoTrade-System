import os
import unittest
from unittest.mock import Mock, patch

from app.services import kis_client
from app.services.kis_client import KISClient, KISError


class KISClientTests(unittest.TestCase):
    def setUp(self):
        os.environ["KIS_APPKEY"] = "appkey"
        os.environ["KIS_APPSECRET"] = "appsecret"
        os.environ["KIS_ACCOUNT_NO"] = "12345678-01"
        os.environ["KIS_MOCK_ORDER"] = "false"

    def test_place_order_dry_run(self):
        client = KISClient(dry_run=True)
        result = client.place_order("005930", 1, "BUY", 70000)
        self.assertEqual(result["status"], "SIMULATED")

    @patch("app.services.kis_client.get_market_status")
    def test_place_order_live_blocked_when_market_closed(self, mock_market):
        mock_market.return_value = type("S", (), {"can_place_order": False, "reason": "장마감"})()
        client = KISClient(dry_run=False)
        result = client.place_order("005930", 1, "BUY", 70000)
        self.assertEqual(result["status"], "BLOCKED")

    @patch("app.services.kis_client.get_market_status")
    def test_place_order_live_mock_success(self, mock_market):
        mock_market.return_value = type("S", (), {"can_place_order": True, "reason": "정규장"})()
        os.environ["KIS_MOCK_ORDER"] = "true"
        client = KISClient(dry_run=False)
        result = client.place_order("005930", 3, "BUY", 70000)
        self.assertEqual(result["status"], "FILLED")

    @patch("app.services.kis_client.get_market_status")
    def test_place_order_live_failure_raises(self, mock_market):
        mock_market.return_value = type("S", (), {"can_place_order": True, "reason": "정규장"})()

        token_resp = Mock(status_code=200)
        token_resp.json.return_value = {"access_token": "token", "expires_in": 3600}

        hash_resp = Mock(status_code=200)
        hash_resp.json.return_value = {"HASH": "hash"}

        order_resp = Mock(status_code=200)
        order_resp.json.return_value = {"rt_cd": "1", "msg1": "주문오류"}

        fake_requests = Mock()
        fake_requests.post.side_effect = [token_resp, hash_resp, order_resp]

        with patch.object(kis_client, "requests", fake_requests):
            client = KISClient(dry_run=False)
            with self.assertRaises(KISError):
                client.place_order("005930", 3, "BUY", 70000)


if __name__ == "__main__":
    unittest.main()
