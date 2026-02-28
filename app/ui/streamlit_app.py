from __future__ import annotations

import logging
import os
from datetime import datetime
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

st.set_page_config(page_title="실전 자동매매 운영센터", layout="wide")


def _inject_style() -> None:
    st.markdown(
        """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');

html, body, [class*="css"], [data-testid="stMarkdownContainer"] {
  font-family: "Pretendard", "Noto Sans KR", "Apple SD Gothic Neo", sans-serif !important;
}

[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(1200px 700px at 10% -20%, rgba(13, 148, 136, 0.10), transparent 55%),
    radial-gradient(1000px 700px at 110% -10%, rgba(30, 64, 175, 0.12), transparent 50%),
    linear-gradient(180deg, #f7fafc 0%, #eef2f7 100%);
}

.main .block-container {
  padding-top: 1.6rem;
  padding-bottom: 2.5rem;
  max-width: 1280px;
}

.hero {
  background: linear-gradient(130deg, #0f172a 0%, #1e293b 45%, #0f766e 100%);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 22px;
  padding: 24px 28px;
  color: #f8fafc;
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.25);
  margin-bottom: 1rem;
}

.hero h1 {
  margin: 0;
  font-size: 2.15rem;
  letter-spacing: -0.02em;
}

.hero p {
  margin: 8px 0 0 0;
  opacity: 0.9;
  font-size: 0.98rem;
}

.panel {
  background: rgba(255,255,255,0.82);
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 16px;
  padding: 12px 14px;
  box-shadow: 0 8px 28px rgba(15, 23, 42, 0.08);
}

[data-testid="stMetric"] {
  background: rgba(255,255,255,0.82);
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 14px;
  padding: 10px 12px;
  box-shadow: 0 7px 20px rgba(15, 23, 42, 0.06);
}
</style>
""",
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    st.markdown(
        """
<div class="hero">
  <h1>실전 자동매매 운영센터</h1>
  <p>엔진 상태, LIVE 전환 점검, 포트폴리오 모니터링, 테스트 데이터 정리까지 한 화면에서 관리합니다.</p>
</div>
""",
        unsafe_allow_html=True,
    )


def _api_get(path: str) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 필요합니다.")
    response = requests.get(f"{ENGINE_API_URL}{path}", timeout=8)
    payload = response.json() if response.content else {}
    if response.status_code != 200:
        raise RuntimeError(f"GET {path} 실패: HTTP_{response.status_code}, payload={payload}")
    return payload if isinstance(payload, dict) else {}


def _api_post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 필요합니다.")
    response = requests.post(f"{ENGINE_API_URL}{path}", json=payload or {}, timeout=10)
    data = response.json() if response.content else {}
    if response.status_code != 200:
        raise RuntimeError(f"POST {path} 실패: HTTP_{response.status_code}, payload={data}")
    return data if isinstance(data, dict) else {}


def _safe_call(fn, label: str, default: Any = None) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.exception("ui_call_failed label=%s", label)
        st.warning(f"{label} 처리 중 오류: {exc}")
        return default


def _is_snapshot_valid(snap: dict[str, Any] | None) -> bool:
    if not isinstance(snap, dict) or not snap:
        return False
    return bool(snap.get("account")) or bool(snap.get("positions"))


def _remember_snapshot(snap: dict[str, Any], source: str) -> None:
    st.session_state["portfolio_snapshot"] = snap
    st.session_state["portfolio_snapshot_updated_at"] = str(snap.get("ts") or "-")
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


def _get_config() -> dict[str, Any]:
    payload = _api_get("/config")
    return payload.get("config", {}) if isinstance(payload.get("config"), dict) else {}


def _get_trades(limit: int = 300) -> list[dict[str, Any]]:
    payload = _api_get(f"/trades?limit={int(limit)}")
    rows = payload.get("rows", [])
    return rows if isinstance(rows, list) else []


def _render_live_readiness(hb: dict[str, Any]) -> None:
    st.markdown("#### LIVE 실주문 준비 상태")
    if bool(hb.get("live_order_enabled", False)):
        st.success("실주문 경로가 활성화되어 있습니다.")
        return

    reasons = hb.get("live_block_reasons", [])
    st.warning("현재는 실주문이 차단되어 있습니다.")
    if isinstance(reasons, list) and reasons:
        for reason in reasons:
            st.markdown(f"- {reason}")
    st.caption("권장: `mode=LIVE` + `.env`의 `LIVE=true`, `DRY_RUN=false`, `KIS_MOCK_ORDER=false`")


def _render_status_tab() -> dict[str, Any]:
    hb = _safe_call(_get_heartbeat, "상태 조회", default={}) or {}
    cfg = _safe_call(_get_config, "설정 조회", default={}) or {}
    if not hb:
        st.error("엔진 상태를 불러오지 못했습니다.")
        return {}

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("자동매매", "ON" if hb.get("enabled") else "OFF")
    c2.metric("운용 모드", str(hb.get("mode", "-")))
    c3.metric("실주문 활성", "가능" if hb.get("live_order_enabled") else "차단")
    c4.metric("후보 수", f"{int(hb.get('candidates_count', 0) or 0)}개")

    st.markdown('<div class="panel">', unsafe_allow_html=True)
    left, right = st.columns([1.2, 1.2])

    with left:
        st.markdown("##### 운용 제어")
        current_enabled = bool(hb.get("enabled", False))
        if "auto_enabled_toggle" not in st.session_state:
            st.session_state["auto_enabled_toggle"] = current_enabled
        toggled = st.toggle("자동매매 ON/OFF", key="auto_enabled_toggle")
        if toggled != current_enabled:
            result = _safe_call(lambda: _api_post("/engine/enable", {"enabled": toggled}), "자동매매 토글", default={})
            if isinstance(result, dict) and result.get("ok"):
                st.success(f"자동매매가 {'ON' if toggled else 'OFF'}으로 변경되었습니다.")
                st.rerun()
            else:
                st.session_state["auto_enabled_toggle"] = current_enabled

    with right:
        st.markdown("##### 모드 전환")
        current_mode = str(cfg.get("mode", hb.get("mode", "DRY-RUN")))
        mode_ui = st.radio(
            "운용 모드 선택",
            options=["DRY-RUN", "LIVE"],
            horizontal=True,
            index=0 if current_mode == "DRY-RUN" else 1,
            key="mode_selector",
        )
        if st.button("모드 저장", type="primary"):
            result = _safe_call(lambda: _api_post("/config/mode", {"mode": mode_ui}), "모드 저장", default={})
            if isinstance(result, dict) and result.get("ok"):
                st.success(f"모드가 {mode_ui}로 저장되었습니다.")
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
    _render_live_readiness(hb)

    if hb.get("next_retry_at"):
        kst, utc = format_retry_time_kst(str(hb.get("next_retry_at")))
        st.info(f"다음 재시도 시각 (KST): {kst}  |  UTC: {utc}")

    st.markdown("#### 엔진 상세 상태")
    st.json(
        {
            "enabled": hb.get("enabled"),
            "fatal_error": hb.get("fatal_error"),
            "blocker": hb.get("blocker"),
            "next_retry_at": hb.get("next_retry_at"),
            "mode": hb.get("mode"),
            "dry_run": hb.get("dry_run"),
            "explicit_live": hb.get("explicit_live"),
            "env_dry_run": hb.get("env_dry_run"),
            "live_order_enabled": hb.get("live_order_enabled"),
            "live_block_reasons": hb.get("live_block_reasons"),
            "daily_trades": hb.get("daily_trades"),
            "open_positions": hb.get("open_positions"),
            "recent_blockers": hb.get("recent_blockers"),
        }
    )
    return hb


def _build_trade_events(symbol: str, trades: list[dict[str, Any]], positions: list[dict[str, Any]], snapshot_ts: str | None) -> pd.DataFrame:
    events: list[dict[str, Any]] = []
    for row in trades:
        if str(row.get("symbol", "")) != symbol:
            continue
        entry_time = row.get("entry_time")
        entry_price = row.get("entry_price")
        if entry_time and entry_price:
            events.append(
                {
                    "ts": pd.to_datetime(entry_time, errors="coerce"),
                    "price": float(entry_price),
                    "event": "매수",
                    "qty": int(float(row.get("qty", 0) or 0)),
                }
            )
        exit_time = row.get("exit_time")
        exit_price = row.get("exit_price")
        if exit_time and exit_price:
            events.append(
                {
                    "ts": pd.to_datetime(exit_time, errors="coerce"),
                    "price": float(exit_price),
                    "event": "매도",
                    "qty": int(float(row.get("qty", 0) or 0)),
                }
            )

    now_point_time = pd.to_datetime(snapshot_ts, errors="coerce")
    if pd.isna(now_point_time):
        now_point_time = pd.to_datetime(datetime.utcnow())

    for pos in positions:
        if str(pos.get("symbol", "")) != symbol:
            continue
        current_price = float(pos.get("eval_price", pos.get("avg_price", 0)) or 0)
        if current_price > 0:
            events.append({"ts": now_point_time, "price": current_price, "event": "현재가", "qty": int(float(pos.get("qty", 0) or 0))})
        break

    if not events:
        return pd.DataFrame()
    df = pd.DataFrame(events).dropna(subset=["ts", "price"])
    if df.empty:
        return df
    return df.sort_values("ts").reset_index(drop=True)


def _render_trade_chart(symbol: str, trades: list[dict[str, Any]], snap: dict[str, Any]) -> None:
    positions = snap.get("positions", []) if isinstance(snap.get("positions"), list) else []
    df = _build_trade_events(symbol, trades, positions, str(snap.get("ts")))
    if df.empty:
        st.info("해당 종목의 매수/매도 이벤트 데이터가 아직 없습니다.")
        return

    line = (
        alt.Chart(df)
        .mark_line(color="#334155", strokeWidth=2)
        .encode(x=alt.X("ts:T", title="시각"), y=alt.Y("price:Q", title="가격(원)"))
    )
    points = (
        alt.Chart(df)
        .mark_point(filled=True, size=170)
        .encode(
            x="ts:T",
            y="price:Q",
            color=alt.Color(
                "event:N",
                scale=alt.Scale(domain=["매수", "매도", "현재가"], range=["#16a34a", "#dc2626", "#2563eb"]),
                legend=alt.Legend(title="이벤트"),
            ),
            shape=alt.Shape("event:N", legend=None),
            tooltip=[
                alt.Tooltip("ts:T", title="시각"),
                alt.Tooltip("event:N", title="이벤트"),
                alt.Tooltip("price:Q", title="가격", format=",.0f"),
                alt.Tooltip("qty:Q", title="수량"),
            ],
        )
    )
    chart = (line + points).properties(height=360)
    st.altair_chart(chart, use_container_width=True)


def _render_portfolio_tab(hb: dict[str, Any]) -> None:
    refresh_disabled = hb.get("blocker") == "KIS_TOKEN_COOLDOWN" and bool(hb.get("next_retry_at"))
    if st.button("포트폴리오 새로고침", disabled=refresh_disabled, type="primary"):
        payload = _safe_call(lambda: _api_post("/refresh/portfolio"), "포트폴리오 갱신", default={}) or {}
        result = payload.get("result", {}) if isinstance(payload.get("result"), dict) else {}
        snap = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else None
        if payload.get("ok") and _is_snapshot_valid(snap):
            _remember_snapshot(snap, "engine_api")
            st.session_state["portfolio_last_error"] = None
            st.success(f"갱신 완료 (source={result.get('source')})")
        else:
            st.session_state["portfolio_last_error"] = result
            st.warning(f"갱신 실패: {result.get('reason', 'unknown')}")

    show_snap = st.session_state.get("portfolio_snapshot")
    if not _is_snapshot_valid(show_snap):
        file_snap = _load_snapshot_file()
        if _is_snapshot_valid(file_snap):
            show_snap = file_snap
            _remember_snapshot(show_snap, "snapshot_file")

    if not _is_snapshot_valid(show_snap):
        st.error("포트폴리오 스냅샷이 없습니다. 먼저 갱신을 실행하세요.")
        return

    if st.session_state.get("portfolio_last_error"):
        err = st.session_state["portfolio_last_error"]
        st.warning(f"실시간 갱신 오류로 마지막 스냅샷을 표시 중입니다: {err.get('reason', 'unknown')}")
        if err.get("next_retry_at"):
            kst, utc = format_retry_time_kst(str(err.get("next_retry_at")))
            st.caption(f"다음 재시도 (KST): {kst} | UTC: {utc}")

    account = show_snap.get("account", {}) if isinstance(show_snap.get("account"), dict) else {}
    orderable_cash = float(hb.get("orderable_cash", show_snap.get("orderable_cash", 0)) or 0)
    d2_cash = float(hb.get("d2_cash", show_snap.get("d2_cash", account.get("d2_cash", 0))) or 0)
    total_eval = float(account.get("total_eval", 0) or 0)
    total_pnl = float(account.get("raw_summary", {}).get("evlu_pfls_smtl_amt", 0) or 0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("주문가능금액", f"{orderable_cash:,.0f}원")
    c2.metric("D+2 예수금", f"{d2_cash:,.0f}원")
    c3.metric("총평가금액", f"{total_eval:,.0f}원")
    c4.metric("평가손익", f"{total_pnl:,.0f}원")

    st.caption(f"스냅샷 시각: {st.session_state.get('portfolio_snapshot_updated_at', '-')}")

    positions = show_snap.get("positions", []) if isinstance(show_snap.get("positions"), list) else []
    orders = show_snap.get("orders", []) if isinstance(show_snap.get("orders"), list) else []

    st.markdown("#### 보유 종목")
    st.dataframe(pd.DataFrame(positions), use_container_width=True)
    st.markdown("#### 주문/체결 내역")
    st.dataframe(pd.DataFrame(orders), use_container_width=True)

    trades = _safe_call(lambda: _get_trades(limit=400), "거래 이력 조회", default=[]) or []
    symbols = []
    symbols.extend([str(x.get("symbol")) for x in positions if x.get("symbol")])
    symbols.extend([str(x.get("symbol")) for x in trades if x.get("symbol")])
    symbols = sorted({s for s in symbols if s})

    st.markdown("#### 보유종목 매수/매도 포인트 차트")
    if not symbols:
        st.info("차트를 표시할 종목이 없습니다.")
        return
    selected_symbol = st.selectbox("종목 선택", options=symbols, key="chart_symbol")
    _render_trade_chart(selected_symbol, trades, show_snap)


def _render_ops_tab() -> None:
    st.markdown("#### 테스트 기록 삭제")
    agreed = st.checkbox("삭제 작업에 동의합니다.", key="ops_agree")
    only_dry = st.checkbox("DRY-RUN 기록만 삭제", value=True, key="ops_only_dry")
    vacuum = st.checkbox("VACUUM 실행", value=False, key="ops_vacuum")

    if st.button("삭제 실행", disabled=not agreed):
        payload = _safe_call(
            lambda: _api_post("/report/clear", {"only_dry": only_dry, "vacuum": vacuum}),
            "기록 삭제",
            default={},
        )
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        if not result:
            st.error("삭제 실패: 응답이 비어 있습니다.")
            return
        st.success(f"완료: {result.get('message', '-')}")
        st.json(
            {
                "deleted": result.get("deleted", {}),
                "skipped": result.get("skipped", []),
                "only_dry": result.get("only_dry"),
                "vacuum": result.get("vacuum"),
            }
        )


for key, default in {
    "portfolio_snapshot": None,
    "portfolio_snapshot_updated_at": None,
    "portfolio_last_error": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

_inject_style()
_render_header()

tab_status, tab_portfolio, tab_ops = st.tabs(["운영 상태", "포트폴리오", "운영 도구"])

with tab_status:
    heartbeat = _render_status_tab()

with tab_portfolio:
    hb_for_portfolio = _safe_call(_get_heartbeat, "상태 조회", default={}) or {}
    _render_portfolio_tab(hb_for_portfolio)

with tab_ops:
    _render_ops_tab()
