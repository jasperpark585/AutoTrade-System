import unittest

from app.agents.orchestrator import build_agent_team, route_operational_task


class AgentOrchestratorTests(unittest.TestCase):
    def test_agent_team_has_required_specialists(self):
        team = build_agent_team()
        names = {agent["name"] for agent in team["specialists"]}
        self.assertIn("strategy_agent", names)
        self.assertIn("risk_agent", names)
        self.assertIn("ui_agent", names)
        self.assertIn("ops_agent", names)
        self.assertEqual(team["order_policy"], "agents_may_not_place_orders")

    def test_risk_keywords_route_to_risk_agent(self):
        route = route_operational_task("주문 전 리스크와 손실 한도를 점검해줘")
        self.assertEqual(route["target_agent"], "risk_agent")


if __name__ == "__main__":
    unittest.main()
