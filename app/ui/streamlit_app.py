from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import altair as alt
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

st.set_page_config(page_title="국내주식 자동매매 운영센터", layout="wide")


def _inject_style() -> None:
    st.markdown(
        """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
html, body, [class*="css"] {font-family: "Pretendard", "Noto Sans KR", sans-serif !important;}
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(1400px 700px at -10% -20%, rgba(15, 118, 110, 0.15), transparent 55%),
    radial-gradient(1100px 700px at 110% -10%, rgba(30, 64, 175, 0.15), transparent 50%),
    linear-gradient(180deg, #f8fafc 0%, #ecf2f9 100%);
}
.hero {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 45%, #0f766e 100%);
  color: #f8fafc; border-radius: 20px; padding: 20px 24px; margin-bottom: 14px;
  box-shadow: 0 18px 42px rgba(15,23,42,0.25);
}
.hero h1 {margin: 0; font-size: 2rem;}
.hero p {margin: 8px 0 0 0; opacity: 0.92;}
[data-testid="stMetric"] {
  background: rgba(255,255,255,0.82); border-radius: 12px; border: 1px solid rgba(15,23,42,0.08);
  box-shadow: 0 8px 18px rgba(15,23,42,0.06); padding: 8px 12px;
}
</style>
""",
        unsafe_allow_html=True,
    )


def _api_get(path: str) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 설치되어야 합니다.")
    resp = requests.get(f"{ENGINE_API_URL}{path}", timeout=10)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise RuntimeError(f"GET {path} 실패: HTTP {resp.status_code}")
    return data if isinstance(data, dict) else {}


def _api_post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 설치되어야 합니다.")
    resp = requests.post(f"{ENGINE_API_URL}{path}", json=payload or {}, timeout=12)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise RuntimeError(f"POST {path} 실패: HTTP {resp.status_code}")
    return data if isinstance(data, dict) else {}


def _safe_call(fn, label: str, default: Any = None) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.exception("ui_call_failed %s", label)
        st.warning(f"{label} 처리 중 오류: {exc}")
        return default


def _load_snapshot_file() -> dict[str, Any] | None:
    if not SNAPSHOT_FALLBACK_PATH.exists():
        return None
    try:
        import json

        payload = json.loads(SNAPSHOT_FALLBACK_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _remember_snapshot(snapshot: dict[str, Any], source: str) -> None:
    st.session_state["portfolio_snapshot"] = snapshot
    st.session_state["portfolio_snapshot_source"] = source
    st.session_state["portfolio_snapshot_updated_at"] = str(snapshot.get("ts") or "-")


def _get_heartbeat() -> dict[str, Any]:
    payload = _api_get("/status")
    engine = payload.get("engine", {})
    return engine if isinstance(engine, dict) else {}


def _get_config() -> dict[str, Any]:
    payload = _api_get("/config")
    config = payload.get("config", {})
    return config if isinstance(config, dict) else {}


def _get_trades(limit: int = 400) -> list[dict[str, Any]]:
    rows = _api_get(f"/trades?limit={int(limit)}").get("rows", [])
    return rows if isinstance(rows, list) else []


def _get_candidates() -> dict[str, Any]:
    payload = _api_get("/candidates")
    result = payload.get("result", {})
    return result if isinstance(result, dict) else {}


def _get_chart(symbol: str, count: int = 180) -> dict[str, Any]:
    path = f"/chart?symbol={symbol}&count={int(count)}"
    payload = _api_get(path)
    result = payload.get("result", {})
    return result if isinstance(result, dict) else {}


def _render_live_readiness(hb: dict[str, Any]) -> None:
    st.markdown("#### LIVE 주문 준비 상태")
    if bool(hb.get("live_order_enabled")):
        st.success("실계좌 주문 경로가 활성화되어 있습니다.")
        return
    st.warning("실계좌 주문이 차단되어 있습니다.")
    reasons = hb.get("live_block_reasons", [])
    if isinstance(reasons, list):
        for reason in reasons:
            st.markdown(f"- {reason}")
    st.caption("필수 조건: mode=LIVE, LIVE=true, DRY_RUN=false, KIS_MOCK_ORDER=false")


def _render_status_tab() -> dict[str, Any]:
    hb = _safe_call(_get_heartbeat, "엔진 상태 조회", default={}) or {}
    cfg = _safe_call(_get_config, "설정 조회", default={}) or {}
    cands = _safe_call(_get_candidates, "AI 후보 조회", default={}) or {}
    if not hb:
        st.error("엔진 상태를 불러오지 못했습니다.")
        return {}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("자동매매", "ON" if hb.get("enabled") else "OFF")
    c2.metric("현재 모드", str(hb.get("mode", "-")))
    c3.metric("실주문 경로", "활성" if hb.get("live_order_enabled") else "차단")
    c4.metric("감시 종목", f"{len(hb.get('watchlist_symbols') or [])}개")

    left, right = st.columns(2)
    with left:
        toggle = st.toggle("자동매매 ON/OFF", value=bool(hb.get("enabled")), key="toggle_enabled")
        if st.button("자동매매 적용"):
            res = _safe_call(lambda: _api_post("/engine/enable", {"enabled": bool(toggle)}), "자동매매 변경", default={})
            if isinstance(res, dict) and res.get("ok"):
                st.success("자동매매 설정을 반영했습니다.")
                st.rerun()
    with right:
        mode_current = str(cfg.get("mode", hb.get("mode", "DRY-RUN")))
        mode_sel = st.radio("운용 모드", ["DRY-RUN", "LIVE"], horizontal=True, index=0 if mode_current == "DRY-RUN" else 1)
        if st.button("모드 저장", type="primary"):
            res = _safe_call(lambda: _api_post("/config/mode", {"mode": mode_sel}), "모드 저장", default={})
            if isinstance(res, dict) and res.get("ok"):
                st.success("모드를 저장했습니다.")
                st.rerun()

    if hb.get("next_retry_at"):
        kst, utc = format_retry_time_kst(str(hb.get("next_retry_at")))
        st.info(f"KIS 재시도 시각 (KST): {kst} | UTC: {utc}")

    _render_live_readiness(hb)
    st.caption(f"마지막 시간보고 알림: {hb.get('last_hourly_alert_at') or '-'}")

    st.markdown("#### 오늘 AI 선정 종목")
    st.write(f"출처: `{cands.get('source', '-')}` / 기준일: `{cands.get('date_kst', '-')}`")
    st.dataframe(pd.DataFrame(cands.get("candidates", [])), use_container_width=True)
    return hb


def _render_portfolio_tab(hb: dict[str, Any]) -> None:
    if st.button("포트폴리오 새로고침", type="primary"):
        data = _safe_call(lambda: _api_post("/refresh/portfolio"), "포트폴리오 갱신", default={}) or {}
        result = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
        snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else None
        if bool(data.get("ok")) and isinstance(snapshot, dict):
            _remember_snapshot(snapshot, source="engine")
            st.session_state["portfolio_last_error"] = None
            st.success("포트폴리오를 갱신했습니다.")
        else:
            st.session_state["portfolio_last_error"] = result
            st.warning(f"갱신 실패: {result.get('reason', 'unknown')}")

    snapshot = st.session_state.get("portfolio_snapshot")
    if not isinstance(snapshot, dict):
        file_snap = _load_snapshot_file()
        if isinstance(file_snap, dict):
            snapshot = file_snap
            _remember_snapshot(file_snap, source="file")

    if not isinstance(snapshot, dict):
        st.error("표시할 포트폴리오 스냅샷이 없습니다.")
        return

    last_error = st.session_state.get("portfolio_last_error")
    if isinstance(last_error, dict):
        st.warning(f"최근 갱신 오류: {last_error.get('reason', '-')}")

    account = snapshot.get("account", {}) if isinstance(snapshot.get("account"), dict) else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("주문가능현금", f"{float(hb.get('orderable_cash', 0) or 0):,.0f}원")
    c2.metric("D+2 예수금", f"{float(hb.get('d2_cash', 0) or 0):,.0f}원")
    c3.metric("총평가금액", f"{float(account.get('total_eval', 0) or 0):,.0f}원")
    c4.metric("보유종목수", f"{len(snapshot.get('positions') or [])}개")

    st.caption(f"스냅샷 시각: {st.session_state.get('portfolio_snapshot_updated_at', '-')}")
    st.markdown("#### 보유 종목")
    st.dataframe(pd.DataFrame(snapshot.get("positions", [])), use_container_width=True)
    st.markdown("#### 주문/체결")
    st.dataframe(pd.DataFrame(snapshot.get("orders", [])), use_container_width=True)


def _render_chart_tab() -> None:
    trades = _safe_call(lambda: _get_trades(limit=500), "거래 조회", default=[]) or []
    cands = _safe_call(_get_candidates, "AI 후보 조회", default={}) or {}
    symbols = {str(row.get("symbol")) for row in trades if row.get("symbol")}
    symbols.update(str(s) for s in (cands.get("symbols") or []) if s)
    symbols = sorted({s for s in symbols if s and s != "None"})
    if not symbols:
        st.info("차트로 볼 종목이 없습니다.")
        return

    symbol = st.selectbox("차트 종목", symbols, key="chart_symbol")
    chart_data = _safe_call(lambda: _get_chart(symbol), "차트 조회", default={}) or {}
    bars = chart_data.get("bars", []) if isinstance(chart_data.get("bars"), list) else []
    events = chart_data.get("events", []) if isinstance(chart_data.get("events"), list) else []
    if not bars:
        st.warning("차트 데이터가 없습니다.")
        return

    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts", "close"]).sort_values("ts")
    ev = pd.DataFrame(events)
    if not ev.empty:
        ev["ts"] = pd.to_datetime(ev["ts"], errors="coerce")
        ev = ev.dropna(subset=["ts", "price"])

    line = alt.Chart(df).mark_line(color="#334155", strokeWidth=2).encode(x="ts:T", y=alt.Y("close:Q", title="가격"))
    layers = [line]
    if not ev.empty:
        points = alt.Chart(ev).mark_point(filled=True, size=170).encode(
            x="ts:T",
            y="price:Q",
            color=alt.Color("event:N", scale=alt.Scale(domain=["BUY", "SELL"], range=["#16a34a", "#dc2626"])),
            tooltip=["ts:T", "event:N", "price:Q", "qty:Q"],
        )
        layers.append(points)
    st.altair_chart(alt.layer(*layers).properties(height=380), use_container_width=True)


def _render_ops_tab() -> None:
    if st.button("장전 AI 후보 강제 갱신"):
        res = _safe_call(lambda: _api_post("/candidates/refresh", {"force": True}), "AI 후보 갱신", default={}) or {}
        result = res.get("result", {}) if isinstance(res.get("result"), dict) else {}
        st.write(result)
    st.divider()
    agreed = st.checkbox("테스트 기록 삭제에 동의합니다.")
    only_dry = st.checkbox("DRY-RUN 데이터만 삭제", value=True)
    vacuum = st.checkbox("VACUUM 실행", value=False)
    if st.button("리포트 기록 삭제", disabled=not agreed):
        res = _safe_call(lambda: _api_post("/report/clear", {"only_dry": only_dry, "vacuum": vacuum}), "기록 삭제", default={}) or {}
        st.write(res.get("result", res))


for key in ("portfolio_snapshot", "portfolio_snapshot_source", "portfolio_snapshot_updated_at", "portfolio_last_error"):
    st.session_state.setdefault(key, None)

_inject_style()
st.markdown(
    """
<div class="hero">
  <h1>국내주식 자동매매 운영센터</h1>
  <p>AI 장전 종목선정, 실계좌 전환 상태점검, 포트폴리오 모니터링, 매수/매도 시점 차트를 한 화면에서 관리합니다.</p>
</div>
""",
    unsafe_allow_html=True,
)

tab_status, tab_port, tab_chart, tab_ops = st.tabs(["운영상태", "포트폴리오", "매매차트", "운영도구"])
with tab_status:
    hb = _render_status_tab()
with tab_port:
    hb_for_port = _safe_call(_get_heartbeat, "엔진 상태 조회", default={}) or {}
    _render_portfolio_tab(hb_for_port)
with tab_chart:
    _render_chart_tab()
with tab_ops:
    _render_ops_tab()
