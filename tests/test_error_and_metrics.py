import unittest

from app.services.kis_client import KISError, calc_spread_pct, calc_trend_slope
from app.utils.errors import unwrap_exception


class ErrorAndMetricsTests(unittest.TestCase):
    def test_calc_spread_pct(self):
        val = calc_spread_pct(9990, 10010)
        self.assertIsNotNone(val)
        self.assertGreater(val, 0)

    def test_calc_trend_slope_positive(self):
        slope = calc_trend_slope([1, 2, 3, 4, 5])
        self.assertGreater(slope, 0)

    def test_unwrap_kis_error_detail(self):
        err = KISError("order failed", detail={"http_status": 400, "rt_cd": "1", "msg1": "주문가능금액 부족"})
        etype, msg, detail = unwrap_exception(err)
        self.assertEqual(etype, "KISError")
        self.assertIn("status=400", msg)
        self.assertEqual(detail["rt_cd"], "1")


if __name__ == "__main__":
    unittest.main()
