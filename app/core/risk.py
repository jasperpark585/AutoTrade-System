from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


class RiskGuard:
    def __init__(self, config: dict[str, Any]):
        self.update(config)

    def update(self, config: dict[str, Any]) -> None:
        self.config = config
        self.risk_limits = config.get("risk_limits", {})

    def check_daily_order_limit(self, daily_trades: int) -> RiskDecision:
        max_orders = int(self.risk_limits.get("max_orders_per_day", self.risk_limits.get("max_daily_trades", 0)) or 0)
        if max_orders > 0 and daily_trades >= max_orders:
            return RiskDecision(False, f"MAX_ORDERS_REACHED ({daily_trades}/{max_orders})")
        return RiskDecision(True, "OK")

    def check_position_limit(self, open_positions: int) -> RiskDecision:
        max_positions = int(self.risk_limits.get("max_positions", 0) or 0)
        if max_positions > 0 and open_positions >= max_positions:
            return RiskDecision(False, f"MAX_POSITIONS_REACHED ({open_positions}/{max_positions})")
        return RiskDecision(True, "OK")
