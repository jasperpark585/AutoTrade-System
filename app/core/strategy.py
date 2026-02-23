from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.kis_client import Quote


@dataclass
class ScoreResult:
    passed: bool
    total_score: float
    stage_scores: dict[str, float]
    reason: str
    stage_checks: dict[str, dict[str, Any]]


class StageStrategy:
    def __init__(self, config: dict):
        self.config = config
        self.stages = config["stages"]
        self.weights = config["scoring_weights"]

    def evaluate(self, q: Quote) -> ScoreResult:
        stage_scores: dict[str, float] = {}
        stage_checks: dict[str, dict[str, Any]] = {}

        u = self.stages["universe"]
        universe_pass = q.spread_pct <= u["max_spread_pct"]
        stage_scores["universe"] = self.weights["universe"] if universe_pass else 0
        stage_checks["universe"] = {
            "passed": universe_pass,
            "score": stage_scores["universe"],
            "max_score": self.weights["universe"],
            "reason": f"spread {q.spread_pct:.2f}% <= {u['max_spread_pct']}%" if universe_pass else f"spread 초과: {q.spread_pct:.2f}%",
        }

        pb = self.stages["pre_breakout"]
        pb_pass = q.volume_ratio >= pb["volume_spike_ratio_min"] and q.volatility_pct >= pb["intraday_volatility_pct_min"]
        stage_scores["pre_breakout"] = self.weights["pre_breakout"] if pb_pass else 0
        stage_checks["pre_breakout"] = {
            "passed": pb_pass,
            "score": stage_scores["pre_breakout"],
            "max_score": self.weights["pre_breakout"],
            "reason": (
                f"volume_ratio {q.volume_ratio:.2f}, volatility {q.volatility_pct:.2f}%"
                if pb_pass
                else (
                    f"volume_ratio 부족({q.volume_ratio:.2f})" if q.volume_ratio < pb["volume_spike_ratio_min"] else "volatility 부족"
                )
            ),
        }

        t = self.stages["trigger"]
        breakout = q.volatility_pct >= t["breakout_zone_1_pct"]
        if q.volatility_pct >= t["breakout_zone_3_pct"]:
            trigger_score = self.weights["trigger"]
            trigger_reason = "돌파구간3(강)"
        elif q.volatility_pct >= t["breakout_zone_2_pct"]:
            trigger_score = self.weights["trigger"] * 0.75
            trigger_reason = "돌파구간2(확정)"
        elif breakout:
            trigger_score = self.weights["trigger"] * 0.4
            trigger_reason = "돌파구간1(약)"
        else:
            trigger_score = 0
            trigger_reason = "돌파 미충족"
        stage_scores["trigger"] = trigger_score
        stage_checks["trigger"] = {
            "passed": trigger_score > 0,
            "score": trigger_score,
            "max_score": self.weights["trigger"],
            "reason": trigger_reason,
        }

        c = self.stages["confirmation"]
        conf_pass = (
            q.execution_strength >= c["execution_strength_min"]
            and q.spread_pct <= c["spread_pct_max"]
            and q.trend_slope >= c["trend_slope_min"]
        )
        stage_scores["confirmation"] = self.weights["confirmation"] if conf_pass else 0
        stage_checks["confirmation"] = {
            "passed": conf_pass,
            "score": stage_scores["confirmation"],
            "max_score": self.weights["confirmation"],
            "reason": (
                f"체결강도 {q.execution_strength:.1f}, spread {q.spread_pct:.2f}, trend {q.trend_slope:.2f}"
                if conf_pass
                else "체결강도/스프레드/추세 조건 미충족"
            ),
        }

        total = sum(stage_scores.values())
        passed = total >= 65 and pb_pass and conf_pass
        reason = "통과" if passed else "단계 점수 미달 또는 확인조건 실패"
        return ScoreResult(passed, total, stage_scores, reason, stage_checks)
