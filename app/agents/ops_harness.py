from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def build_ops_context(
    *,
    status: dict[str, Any],
    config: dict[str, Any],
    candidates: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": dict(status or {}),
        "config": dict(config or {}),
        "candidates": dict(candidates or {}),
    }


def evaluate_ops_context(context: dict[str, Any]) -> dict[str, Any]:
    status = context.get("status", {}) if isinstance(context.get("status"), dict) else {}
    candidates = context.get("candidates", {}) if isinstance(context.get("candidates"), dict) else {}
    findings: list[str] = []
    actions: list[str] = []
    severity = "ok"

    if status.get("fatal_error"):
        severity = "critical"
        findings.append(f"fatal_error is set: {status.get('fatal_error')}")
        actions.append("Check engine logs before re-enabling trading.")

    blocker = str(status.get("blocker") or "").strip()
    if blocker:
        severity = "critical" if severity == "critical" else "warning"
        findings.append(f"active blocker: {blocker}")
        retry_at = status.get("next_retry_at")
        actions.append(f"Wait until retry time or inspect broker/API state: {retry_at or 'not provided'}.")

    if str(status.get("mode") or "").upper() == "LIVE" and not bool(status.get("live_order_enabled")):
        severity = "critical" if severity == "critical" else "warning"
        reasons = status.get("live_block_reasons", [])
        detail = "; ".join(str(x) for x in reasons) if isinstance(reasons, list) else str(reasons)
        findings.append(f"LIVE mode is selected but live orders are blocked: {detail or 'no reason reported'}")
        actions.append("Confirm LIVE=true, DRY_RUN=false, and KIS_MOCK_ORDER=false only when ready for real orders.")

    symbols = candidates.get("symbols", []) if isinstance(candidates.get("symbols"), list) else []
    if not symbols:
        severity = "warning" if severity == "ok" else severity
        findings.append("candidate watchlist is empty")
        actions.append("Run /candidates/refresh or confirm gpt_scout fallback symbols.")

    if not findings:
        findings.append("no blocking operational issues detected")
        actions.append("Continue monitoring health, portfolio freshness, and risk limits.")

    return {
        "severity": severity,
        "findings": findings,
        "recommended_actions": actions,
        "order_policy": "read_only_no_order_placement",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline operational readiness harness.")
    parser.add_argument("context_json", nargs="?", help="JSON object with status/config/candidates keys")
    args = parser.parse_args()
    raw_context = args.context_json if args.context_json else sys.stdin.read()
    context = json.loads(raw_context)
    print(json.dumps(evaluate_ops_context(context), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
