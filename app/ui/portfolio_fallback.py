from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PortfolioSnapshotChoice:
    snapshot: dict[str, Any] | None
    source: str
    warning: str | None = None


def _extract_live_snapshot(live_result: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(live_result, dict):
        return None, None
    result = live_result.get("result", {}) if isinstance(live_result.get("result"), dict) else {}
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else None
    if bool(live_result.get("ok")) and isinstance(snapshot, dict):
        return snapshot, None
    reason = str(result.get("reason") or live_result.get("reason") or "live refresh failed")
    return None, reason


def choose_portfolio_snapshot(
    *,
    live_result: dict[str, Any] | None = None,
    cached_snapshot: dict[str, Any] | None = None,
    file_snapshot: dict[str, Any] | None = None,
    session_snapshot: dict[str, Any] | None = None,
) -> PortfolioSnapshotChoice:
    """Choose the UI portfolio snapshot in the documented fallback order.

    Priority is live refresh, engine cached snapshot, file snapshot, then the
    last session snapshot. Any live refresh failure is carried as a warning so
    the UI can explain stale data without exposing sensitive account details.
    """

    live_snapshot, live_warning = _extract_live_snapshot(live_result)
    if isinstance(live_snapshot, dict):
        return PortfolioSnapshotChoice(live_snapshot, "live_refresh", None)
    if isinstance(cached_snapshot, dict):
        return PortfolioSnapshotChoice(cached_snapshot, "engine_cache", live_warning)
    if isinstance(file_snapshot, dict):
        return PortfolioSnapshotChoice(file_snapshot, "file_snapshot", live_warning)
    if isinstance(session_snapshot, dict):
        return PortfolioSnapshotChoice(session_snapshot, "session_snapshot", live_warning)
    return PortfolioSnapshotChoice(None, "none", live_warning)
