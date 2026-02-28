from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from app.ui.time_utils import format_retry_time_kst

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

ENGINE_API_URL = os.getenv("ENGINE_API_URL", "http://127.0.0.1:8000").rstrip("/")
SNAPSHOT_FALLBACK_PATH = Path("data/portfolio_snapshot.json")

st.set_page_config(page_title="AutoTrade Monitor", layout="wide")
st.title("AutoTrade Operations Monitor")


def _api_get(path: str) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests package is required")
    response = requests.get(f"{ENGINE_API_URL}{path}", timeout=6)
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        raise RuntimeError(f"GET {path} failed: HTTP_{response.status_code} payload={payload}")
    return payload if isinstance(payload, dict) else {}


def _api_post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests package is required")
    response = requests.post(f"{ENGINE_API_URL}{path}", json=payload or {}, timeout=10)
    data = response.json() if response.content else {}
    if response.status_code != 200:
        raise RuntimeError(f"POST {path} failed: HTTP_{response.status_code} payload={data}")
    return data if isinstance(data, dict) else {}


def _safe_call(fn, label: str, default: Any = None) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.exception("ui_call_failed label=%s", label)
        st.warning(f"{label} failed: {exc}")
        return default


def _is_snapshot_valid(snap: dict[str, Any] | None) -> bool:
    if not isinstance(snap, dict) or not snap:
        return False
    account = snap.get("account")
    positions = snap.get("positions")
    return bool(account) or bool(positions)


def _remember_snapshot(snap: dict[str, Any], source: str) -> None:
    updated_at = str(snap.get("ts") or "-")
    st.session_state["portfolio_snapshot"] = snap
    st.session_state["portfolio_snapshot_updated_at"] = updated_at
    st.session_state["portfolio_snapshot_source"] = source


def _load_snapshot_file() -> dict[str, Any] | None:
    if not SNAPSHOT_FALLBACK_PATH.exists():
        return None
    try:
        import json

        loaded = json.loads(SNAPSHOT_FALLBACK_PATH.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def _get_heartbeat() -> dict[str, Any]:
    payload = _api_get("/status")
    engine = payload.get("engine", {}) if isinstance(payload.get("engine"), dict) else {}
    return engine


def _show_status() -> dict[str, Any]:
    hb = _safe_call(_get_heartbeat, "status", default={}) or {}
    if not hb:
        st.error("Engine status is unavailable.")
        return {}

    current_enabled = bool(hb.get("enabled", False))
    if "auto_enabled_toggle" not in st.session_state:
        st.session_state["auto_enabled_toggle"] = current_enabled

    toggled = st.toggle("Auto Trading Enabled", key="auto_enabled_toggle")
    if toggled != current_enabled:
        result = _safe_call(lambda: _api_post("/engine/enable", {"enabled": toggled}), "engine enable", default={})
        if isinstance(result, dict) and result.get("ok"):
            st.success(f"Auto trading set to {'ON' if toggled else 'OFF'}")
        else:
            st.session_state["auto_enabled_toggle"] = current_enabled

    st.json(
        {
            "enabled": hb.get("enabled"),
            "fatal_error": hb.get("fatal_error"),
            "blocker": hb.get("blocker"),
            "next_retry_at": hb.get("next_retry_at"),
            "mode": hb.get("mode"),
            "dry_run": hb.get("dry_run"),
            "live_order_enabled": hb.get("live_order_enabled"),
            "daily_trades": hb.get("daily_trades"),
            "open_positions": hb.get("open_positions"),
            "candidates_count": hb.get("candidates_count"),
            "recent_blockers": hb.get("recent_blockers"),
        }
    )

    retry_at = hb.get("next_retry_at")
    if retry_at:
        kst, utc = format_retry_time_kst(str(retry_at))
        st.caption(f"Next retry (KST): {kst} (UTC: {utc})")

    if hb.get("mode") == "LIVE" and not hb.get("live_order_enabled"):
        st.warning("LIVE mode is configured but real orders are blocked. Set LIVE=true and DRY_RUN=false explicitly.")
    return hb


def _show_portfolio(hb: dict[str, Any]) -> None:
    refresh_disabled = hb.get("blocker") == "KIS_TOKEN_COOLDOWN" and bool(hb.get("next_retry_at"))
    if st.button("Refresh Portfolio", disabled=refresh_disabled):
        payload = _safe_call(lambda: _api_post("/refresh/portfolio"), "portfolio refresh", default={}) or {}
        result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
        snap = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else None
        if payload.get("ok") and _is_snapshot_valid(snap):
            _remember_snapshot(snap, "engine_api")
            st.session_state["portfolio_last_error"] = None
            st.success(f"Portfolio refreshed. source={result.get('source')}")
        else:
            st.session_state["portfolio_last_error"] = result
            st.warning(f"Refresh failed: {result.get('reason', 'unknown')}")

    show_snap = st.session_state.get("portfolio_snapshot")
    if not _is_snapshot_valid(show_snap):
        file_snap = _load_snapshot_file()
        if _is_snapshot_valid(file_snap):
            show_snap = file_snap
            _remember_snapshot(show_snap, "snapshot_file")

    if not _is_snapshot_valid(show_snap):
        st.error("No portfolio snapshot is available yet.")
        return

    if st.session_state.get("portfolio_last_error"):
        last_err = st.session_state["portfolio_last_error"]
        st.warning(f"Showing last snapshot due to refresh error: {last_err.get('reason', 'unknown')}")
        if last_err.get("next_retry_at"):
            kst, utc = format_retry_time_kst(str(last_err.get("next_retry_at")))
            st.caption(f"Next retry (KST): {kst} (UTC: {utc})")

    account = show_snap.get("account", {}) if isinstance(show_snap.get("account"), dict) else {}
    orderable_cash = float(hb.get("orderable_cash", show_snap.get("orderable_cash", 0)) or 0)
    d2_cash = float(hb.get("d2_cash", show_snap.get("d2_cash", account.get("d2_cash", 0))) or 0)
    total_eval = float(account.get("total_eval", 0) or 0)
    total_pnl = float(account.get("raw_summary", {}).get("evlu_pfls_smtl_amt", 0) or 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Orderable Cash", f"{orderable_cash:,.0f} KRW")
    c2.metric("D+2 Cash", f"{d2_cash:,.0f} KRW")
    c3.metric("Total Eval", f"{total_eval:,.0f} KRW")
    c4.metric("Total PnL", f"{total_pnl:,.0f} KRW")

    st.caption(f"Snapshot updated at: {st.session_state.get('portfolio_snapshot_updated_at', '-')}")
    st.markdown("#### Positions")
    st.dataframe(pd.DataFrame(show_snap.get("positions", [])), use_container_width=True)
    st.markdown("#### Orders")
    st.dataframe(pd.DataFrame(show_snap.get("orders", [])), use_container_width=True)


def _show_ops() -> None:
    st.markdown("#### Delete Test Records")
    agreed = st.checkbox("I understand this operation.")
    only_dry = st.checkbox("Dry-run records only", value=True)
    vacuum = st.checkbox("Run VACUUM", value=False)

    if st.button("Delete", disabled=not agreed):
        payload = _safe_call(
            lambda: _api_post("/report/clear", {"only_dry": only_dry, "vacuum": vacuum}),
            "clear report data",
            default={},
        )
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        if not result:
            st.error("Delete failed: no response body")
            return

        deleted = result.get("deleted", {})
        skipped = result.get("skipped", [])
        msg = result.get("message", "")
        st.success(f"Delete finished. message={msg}")
        st.json({"deleted": deleted, "skipped": skipped, "only_dry": result.get("only_dry"), "vacuum": result.get("vacuum")})


for key, default in {
    "portfolio_snapshot": None,
    "portfolio_snapshot_updated_at": None,
    "portfolio_last_error": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

tab_status, tab_portfolio, tab_ops = st.tabs(["Status", "Portfolio", "Ops"])

with tab_status:
    heartbeat = _show_status()

with tab_portfolio:
    hb_for_portfolio = _safe_call(_get_heartbeat, "status", default={}) or {}
    _show_portfolio(hb_for_portfolio)

with tab_ops:
    _show_ops()
