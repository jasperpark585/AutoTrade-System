from __future__ import annotations

from dataclasses import dataclass

from app.services.kis_client import Quote


@dataclass
class ScoreResult:
    passed: bool
    total_score: float
    stage_scores: dict[str, float]
    reason: str


class StageStrategy:
    def __init__(self, config: dict):
        self.config = config
        self.stages = config["stages"]
        self.weights = config["scoring_weights"]

    def evaluate(self, q: Quote) -> ScoreResult:
        stages: dict[str, float] = {}

        u = self.stages["universe"]
        stages["universe"] = self.weights["universe"] if q.spread_pct <= u["max_spread_pct"] else 0

        pb = self.stages["pre_breakout"]
        pb_pass = q.volume_ratio >= pb["volume_spike_ratio_min"] and q.volatility_pct >= pb["intraday_volatility_pct_min"]
        stages["pre_breakout"] = self.weights["pre_breakout"] if pb_pass else 0

        t = self.stages["trigger"]
        breakout = q.volatility_pct >= t["breakout_zone_1_pct"]
        if q.volatility_pct >= t["breakout_zone_3_pct"]:
            stages["trigger"] = self.weights["trigger"]
        elif q.volatility_pct >= t["breakout_zone_2_pct"]:
            stages["trigger"] = self.weights["trigger"] * 0.75
        elif breakout:
            stages["trigger"] = self.weights["trigger"] * 0.4
        else:
            stages["trigger"] = 0

        c = self.stages["confirmation"]
        conf_pass = (
            q.execution_strength >= c["execution_strength_min"]
            and q.spread_pct <= c["spread_pct_max"]
            and q.trend_slope >= c["trend_slope_min"]
        )
        stages["confirmation"] = self.weights["confirmation"] if conf_pass else 0

        total = sum(stages.values())
        passed = total >= 65 and pb_pass and conf_pass
        reason = "통과" if passed else "단계 점수 미달 또는 확인조건 실패"
        return ScoreResult(passed, total, stages, reason)
