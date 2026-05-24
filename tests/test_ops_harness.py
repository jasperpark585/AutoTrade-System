import unittest

from app.agents.ops_harness import build_ops_context, evaluate_ops_context


class OpsHarnessTests(unittest.TestCase):
    def test_live_mode_without_order_enablement_requires_operator_attention(self):
        context = build_ops_context(
            status={
                "mode": "LIVE",
                "live_order_enabled": False,
                "live_block_reasons": ["DRY_RUN=false required"],
                "blocker": "",
                "fatal_error": None,
            },
            config={"mode": "LIVE"},
            candidates={"symbols": ["005930"]},
        )

        report = evaluate_ops_context(context)

        self.assertEqual(report["severity"], "warning")
        self.assertTrue(any("LIVE mode" in item for item in report["findings"]))

    def test_fatal_error_is_critical(self):
        context = build_ops_context(
            status={"fatal_error": "boom", "mode": "DRY-RUN", "live_order_enabled": False},
            config={"mode": "DRY-RUN"},
            candidates={},
        )

        report = evaluate_ops_context(context)

        self.assertEqual(report["severity"], "critical")
        self.assertTrue(any("fatal_error" in item for item in report["findings"]))


if __name__ == "__main__":
    unittest.main()
