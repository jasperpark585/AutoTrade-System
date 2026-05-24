from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.cross_signals import DEAD_CROSS, GOLDEN_CROSS, MIDLONG_DEAD_CROSS, MIDLONG_GOLDEN_CROSS
from app.services.kis_client import Quote


@dataclass
class ScoreResult:
    passed: bool
    total_score: float
    stage_scores: dict[str, float]
    reason: str
    stage_checks: dict[str, dict[str, Any]]


class StageStrategy:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.stages = config["stages"]
        self.weights = config["scoring_weights"]

    def evaluate(self, q: Quote) -> ScoreResult:
        stage_scores: dict[str, float] = {}
        stage_checks: dict[str, dict[str, Any]] = {}

        universe_cfg = self.stages["universe"]
        universe_pass = q.spread_pct <= universe_cfg["max_spread_pct"]
        stage_scores["universe"] = self.weights["universe"] if universe_pass else 0.0
        stage_checks["universe"] = {
            "passed": universe_pass,
            "score": stage_scores["universe"],
            "max_score": self.weights["universe"],
            "reason": (
                f"spread ok ({q.spread_pct:.2f}% <= {universe_cfg['max_spread_pct']}%)"
                if universe_pass
                else f"spread too wide ({q.spread_pct:.2f}%)"
            ),
        }

        pre_cfg = self.stages["pre_breakout"]
        pre_pass = q.volume_ratio >= pre_cfg["volume_spike_ratio_min"] and q.volatility_pct >= pre_cfg["intraday_volatility_pct_min"]
        stage_scores["pre_breakout"] = self.weights["pre_breakout"] if pre_pass else 0.0
        if pre_pass:
            pre_reason = f"volume ratio {q.volume_ratio:.2f}, volatility {q.volatility_pct:.2f}%"
        elif q.volume_ratio < pre_cfg["volume_spike_ratio_min"]:
            pre_reason = f"volume ratio too low ({q.volume_ratio:.2f})"
        else:
            pre_reason = f"volatility too low ({q.volatility_pct:.2f}%)"
        stage_checks["pre_breakout"] = {
            "passed": pre_pass,
            "score": stage_scores["pre_breakout"],
            "max_score": self.weights["pre_breakout"],
            "reason": pre_reason,
        }

        trigger_cfg = self.stages["trigger"]
        if q.volatility_pct >= trigger_cfg["breakout_zone_3_pct"]:
            trigger_score = self.weights["trigger"]
            trigger_reason = "breakout zone 3"
        elif q.volatility_pct >= trigger_cfg["breakout_zone_2_pct"]:
            trigger_score = self.weights["trigger"] * 0.75
            trigger_reason = "breakout zone 2"
        elif q.volatility_pct >= trigger_cfg["breakout_zone_1_pct"]:
            trigger_score = self.weights["trigger"] * 0.4
            trigger_reason = "breakout zone 1"
        else:
            trigger_score = 0.0
            trigger_reason = "breakout threshold not met"
        stage_scores["trigger"] = float(trigger_score)
        stage_checks["trigger"] = {
            "passed": trigger_score > 0,
            "score": stage_scores["trigger"],
            "max_score": self.weights["trigger"],
            "reason": trigger_reason,
        }

        confirm_cfg = self.stages["confirmation"]
        cross_signal = str(getattr(q, "cross_signal", "") or "")
        bearish_crosses = {DEAD_CROSS, MIDLONG_DEAD_CROSS}
        dead_cross_blocked = bool(confirm_cfg.get("block_dead_cross", True)) and cross_signal in bearish_crosses
        confirm_pass = (
            q.execution_strength >= confirm_cfg["execution_strength_min"]
            and q.spread_pct <= confirm_cfg["spread_pct_max"]
            and q.trend_slope >= confirm_cfg["trend_slope_min"]
            and not dead_cross_blocked
        )
        stage_scores["confirmation"] = self.weights["confirmation"] if confirm_pass else 0.0
        stage_checks["confirmation"] = {
            "passed": confirm_pass,
            "score": stage_scores["confirmation"],
            "max_score": self.weights["confirmation"],
            "reason": (
                f"execution {q.execution_strength:.1f}, spread {q.spread_pct:.2f}, trend {q.trend_slope:.3f}, cross {cross_signal or 'NONE'}"
                if confirm_pass
                else f"confirmation failed; cross {cross_signal or 'NONE'}"
            ),
        }

        cross_bonus = float(confirm_cfg.get("golden_cross_bonus", 5) or 0)
        midlong_bonus = float(confirm_cfg.get("midlong_golden_cross_bonus", 8) or 0)
        stage_scores["cross"] = midlong_bonus if cross_signal == MIDLONG_GOLDEN_CROSS else cross_bonus if cross_signal == GOLDEN_CROSS else 0.0
        stage_checks["cross"] = {
            "passed": cross_signal in {GOLDEN_CROSS, MIDLONG_GOLDEN_CROSS},
            "score": stage_scores["cross"],
            "max_score": max(cross_bonus, midlong_bonus),
            "reason": cross_signal or "no moving-average cross",
        }

        total = float(sum(stage_scores.values()))
        passed = bool(total >= 65.0 and pre_pass and confirm_pass)
        if dead_cross_blocked:
            reason = f"blocked by {cross_signal}"
        elif cross_signal in {GOLDEN_CROSS, MIDLONG_GOLDEN_CROSS} and passed:
            reason = f"strategy passed with {GOLDEN_CROSS}"
            if cross_signal == MIDLONG_GOLDEN_CROSS:
                reason = f"strategy passed with {MIDLONG_GOLDEN_CROSS}"
        else:
            reason = "strategy passed" if passed else "score or confirmation condition not met"
        return ScoreResult(passed=passed, total_score=total, stage_scores=stage_scores, reason=reason, stage_checks=stage_checks)
