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
                f"스프레드 통과 ({q.spread_pct:.2f}% <= {universe_cfg['max_spread_pct']}%)"
                if universe_pass
                else f"스프레드 초과 ({q.spread_pct:.2f}%)"
            ),
        }

        pre_cfg = self.stages["pre_breakout"]
        pre_pass = q.volume_ratio >= pre_cfg["volume_spike_ratio_min"] and q.volatility_pct >= pre_cfg["intraday_volatility_pct_min"]
        stage_scores["pre_breakout"] = self.weights["pre_breakout"] if pre_pass else 0.0
        if pre_pass:
            pre_reason = f"거래량 비율 {q.volume_ratio:.2f}, 변동성 {q.volatility_pct:.2f}%"
        elif q.volume_ratio < pre_cfg["volume_spike_ratio_min"]:
            pre_reason = f"거래량 비율 부족 ({q.volume_ratio:.2f})"
        else:
            pre_reason = f"변동성 부족 ({q.volatility_pct:.2f}%)"
        stage_checks["pre_breakout"] = {
            "passed": pre_pass,
            "score": stage_scores["pre_breakout"],
            "max_score": self.weights["pre_breakout"],
            "reason": pre_reason,
        }

        trigger_cfg = self.stages["trigger"]
        if q.volatility_pct >= trigger_cfg["breakout_zone_3_pct"]:
            trigger_score = self.weights["trigger"]
            trigger_reason = "돌파 구간 3"
        elif q.volatility_pct >= trigger_cfg["breakout_zone_2_pct"]:
            trigger_score = self.weights["trigger"] * 0.75
            trigger_reason = "돌파 구간 2"
        elif q.volatility_pct >= trigger_cfg["breakout_zone_1_pct"]:
            trigger_score = self.weights["trigger"] * 0.4
            trigger_reason = "돌파 구간 1"
        else:
            trigger_score = 0.0
            trigger_reason = "돌파 기준 미충족"
        stage_scores["trigger"] = float(trigger_score)
        stage_checks["trigger"] = {
            "passed": trigger_score > 0,
            "score": stage_scores["trigger"],
            "max_score": self.weights["trigger"],
            "reason": trigger_reason,
        }

        confirm_cfg = self.stages["confirmation"]
        confirm_pass = (
            q.execution_strength >= confirm_cfg["execution_strength_min"]
            and q.spread_pct <= confirm_cfg["spread_pct_max"]
            and q.trend_slope >= confirm_cfg["trend_slope_min"]
        )
        stage_scores["confirmation"] = self.weights["confirmation"] if confirm_pass else 0.0
        stage_checks["confirmation"] = {
            "passed": confirm_pass,
            "score": stage_scores["confirmation"],
            "max_score": self.weights["confirmation"],
            "reason": (
                f"체결강도 {q.execution_strength:.1f}, 스프레드 {q.spread_pct:.2f}, 추세 {q.trend_slope:.3f}"
                if confirm_pass
                else "체결강도/스프레드/추세 조건 미충족"
            ),
        }

        total = float(sum(stage_scores.values()))
        passed = bool(total >= 65.0 and pre_pass and confirm_pass)
        reason = "전략 통과" if passed else "점수 또는 확인 조건 미충족"
        return ScoreResult(passed=passed, total_score=total, stage_scores=stage_scores, reason=reason, stage_checks=stage_checks)
