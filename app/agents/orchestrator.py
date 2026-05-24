from __future__ import annotations

from typing import Any


def build_agent_team() -> dict[str, Any]:
    return {
        "name": "autotrade_orchestrator",
        "pattern": "code_first_routing_with_optional_agents_sdk_handoffs",
        "order_policy": "agents_may_not_place_orders",
        "guardrails": [
            "No agent can call a broker order endpoint directly.",
            "Order execution must stay inside deterministic engine risk checks.",
            "Credential values must never be included in prompts, logs, or agent outputs.",
        ],
        "specialists": [
            {
                "name": "strategy_agent",
                "role": "Candidate analysis, strategy review, and watchlist reasoning.",
                "allowed_tools": ["read_market_snapshot", "read_candidates", "read_strategy_config"],
            },
            {
                "name": "risk_agent",
                "role": "Risk limits, live readiness, cash exposure, cooldown, and blocker review.",
                "allowed_tools": ["read_status", "read_risk_config", "read_open_positions"],
            },
            {
                "name": "ui_agent",
                "role": "Operator workflow and UI improvement recommendations.",
                "allowed_tools": ["read_ui_state", "read_user_feedback"],
            },
            {
                "name": "ops_agent",
                "role": "Install, service health, logs, API errors, and recovery guidance.",
                "allowed_tools": ["read_health", "read_logs", "read_install_state"],
            },
        ],
    }


def route_operational_task(task: str) -> dict[str, str]:
    text = str(task or "").lower()
    if any(token in text for token in ("risk", "리스크", "손실", "주문", "한도", "cash", "blocker")):
        target = "risk_agent"
    elif any(token in text for token in ("ui", "화면", "디자인", "사용자", "ux")):
        target = "ui_agent"
    elif any(token in text for token in ("설치", "로그", "서버", "서비스", "오류", "복구", "health")):
        target = "ops_agent"
    else:
        target = "strategy_agent"
    return {"target_agent": target, "order_policy": "agents_may_not_place_orders"}
