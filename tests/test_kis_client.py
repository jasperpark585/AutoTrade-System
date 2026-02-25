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


    def test_parse_money_commas_and_spaces(self):
        self.assertEqual(KISClient.parse_money("120,398"), 120398)
        self.assertEqual(KISClient.parse_money(" 120398 "), 120398)

    def test_account_summary_parses_orderable_from_output2(self):
        client = KISClient(dry_run=False)
        client._split_account_no = Mock(return_value=("12345678", "01"))  # type: ignore[method-assign]
        client._auth_headers = Mock(return_value={"authorization": "Bearer x"})  # type: ignore[method-assign]
        client._request_json = Mock(return_value={
            "output2": [
                {
                    "ord_psbl_cash": "120,398",
                    "nxdy_excc_amt": "121,280",
                    "dnca_tot_amt": "121,280",
                    "tot_evlu_amt": "10,000",
                    "tot_asst_amt": "131,280",
                }
            ]
        })  # type: ignore[method-assign]

        summary = client.get_account_summary()
        self.assertEqual(summary["orderable_cash"], 120398)
        self.assertEqual(summary["available_cash"], 120398)
        self.assertEqual(summary["d2_cash"], 121280)

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

        # token/hash/order for 1st try + token/hash/order for retries
        fake_requests.post.side_effect = [token_resp, hash_resp, order_resp] * 3

        with patch.object(kis_client, "requests", fake_requests):
            client = KISClient(dry_run=False)
            with self.assertRaises(Exception):
                client.place_order("005930", 3, "BUY", 70000)


if __name__ == "__main__":
    unittest.main()
