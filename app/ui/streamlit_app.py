from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import altair as alt
import pandas as pd
import streamlit as st

from app.ui.portfolio_fallback import choose_portfolio_snapshot
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
[data-testid="stAppViewContainer"] {background: linear-gradient(180deg, #f8fafc 0%, #ecf2f9 100%);}
.hero {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #0f766e 100%);
  color: #f8fafc; border-radius: 8px; padding: 20px 24px; margin-bottom: 14px;
  box-shadow: 0 14px 34px rgba(15,23,42,0.18);
}
.hero h1 {margin: 0; font-size: 2rem;}
.hero p {margin: 8px 0 0 0; opacity: 0.92;}
[data-testid="stMetric"] {
  background: rgba(255,255,255,0.86); border-radius: 8px; border: 1px solid rgba(15,23,42,0.08);
  box-shadow: 0 8px 18px rgba(15,23,42,0.05); padding: 8px 12px;
}
</style>
""",
        unsafe_allow_html=True,
    )


def _api_get(path: str) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 설치되어 있지 않습니다.")
    resp = requests.get(f"{ENGINE_API_URL}{path}", timeout=10)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise RuntimeError(f"GET {path} 실패: HTTP {resp.status_code}")
    return data if isinstance(data, dict) else {}


def _api_post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 설치되어 있지 않습니다.")
    resp = requests.post(f"{ENGINE_API_URL}{path}", json=payload or {}, timeout=12)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200:
        raise RuntimeError(f"POST {path} 실패: HTTP {resp.status_code} {data}")
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


def _get_broker_credentials() -> dict[str, Any]:
    payload = _api_get("/broker/credentials")
    credentials = payload.get("credentials", {})
    return credentials if isinstance(credentials, dict) else {}


def _get_trades(limit: int = 400) -> list[dict[str, Any]]:
    rows = _api_get(f"/trades?limit={int(limit)}").get("rows", [])
    return rows if isinstance(rows, list) else []


def _get_candidates() -> dict[str, Any]:
    payload = _api_get("/candidates")
    result = payload.get("result", {})
    return result if isinstance(result, dict) else {}


def _get_chart(symbol: str, count: int = 180) -> dict[str, Any]:
    payload = _api_get(f"/chart?symbol={symbol}&count={int(count)}")
    result = payload.get("result", {})
    return result if isinstance(result, dict) else {}


def _is_truthy_env(name: str) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _guard_reason_label(reason: str) -> str:
    reason_map = {
        "OPENAI_GUARD_OK": "정상",
        "OPENAI_PAID_OPT_IN_REQUIRED": "유료 호출 허용 환경변수 미설정",
        "OPENAI_GUARD_DAILY_LIMIT": "일일 호출 한도 초과",
        "OPENAI_GUARD_MONTHLY_LIMIT": "월간 호출 한도 초과",
        "OPENAI_GUARD_MONTHLY_BUDGET": "월간 예산 한도 초과",
        "OPENAI_GUARD_PER_CALL_ESTIMATE_LIMIT": "호출당 예상비용 한도 초과",
        "OPENAI_GUARD_COOLDOWN": "429 이후 쿨다운 차단 중",
        "OPENAI_HTTP_429": "OpenAI 429(쿼터 또는 요청 제한)",
    }
    return reason_map.get(reason, reason or "-")


def _render_live_readiness(hb: dict[str, Any]) -> None:
    st.markdown("#### LIVE 주문 준비 상태")
    if bool(hb.get("live_order_enabled")):
        st.success("실계좌 주문 경로가 활성화되어 있습니다.")
        return
    st.warning("실계좌 주문은 차단되어 있습니다.")
    reasons = hb.get("live_block_reasons", [])
    if isinstance(reasons, list):
        for reason in reasons:
            st.markdown(f"- {reason}")
    st.caption("필수 조건: mode=LIVE, LIVE=true, DRY_RUN=false, KIS_MOCK_ORDER=false")


def _render_openai_guard(hb: dict[str, Any], cands: dict[str, Any]) -> None:
    guard = hb.get("openai_guard", {}) if isinstance(hb.get("openai_guard"), dict) else {}
    if not guard:
        guard = cands.get("openai_guard", {}) if isinstance(cands.get("openai_guard"), dict) else {}

    st.markdown("#### OpenAI 쿼터/과금 가드")
    if not guard:
        st.info("가드 상태 데이터가 아직 없습니다. 후보 갱신 후 표시됩니다.")
        return

    req_today = int(guard.get("requests_today", 0) or 0)
    req_month = int(guard.get("requests_this_month", 0) or 0)
    max_day = int(guard.get("max_requests_per_day", 0) or 0)
    max_month = int(guard.get("max_requests_per_month", 0) or 0)
    cost_month = float(guard.get("cost_usd_this_month", 0.0) or 0.0)
    max_budget = float(guard.get("max_monthly_cost_usd", 0.0) or 0.0)
    paid_opt_in_env = str(guard.get("paid_opt_in_env") or "OPENAI_PAID_ALLOWED")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("오늘 호출", f"{req_today}/{max_day if max_day > 0 else '무제한'}")
    c2.metric("이번 달 호출", f"{req_month}/{max_month if max_month > 0 else '무제한'}")
    c3.metric("이번 달 비용(USD)", f"{cost_month:.4f}/{max_budget if max_budget > 0 else '무제한'}")
    c4.metric("유료 호출 허용", "허용" if _is_truthy_env(paid_opt_in_env) else "차단")

    last_reason = str(guard.get("last_reason") or "")
    reason_label = _guard_reason_label(last_reason)
    if last_reason and last_reason not in {"OPENAI_OK", "OPENAI_GUARD_OK"}:
        st.warning(f"최근 가드 사유: {reason_label}")
    else:
        st.success(f"최근 가드 사유: {reason_label}")

    block_until = str(guard.get("block_until_utc") or "").strip()
    if block_until:
        kst, utc = format_retry_time_kst(block_until)
        st.info(f"OpenAI 재시도 가능 시각 (KST): {kst} | UTC: {utc}")

    reserve_ratio = float(guard.get("reserve_ratio", 0.9) or 0.9)
    st.caption(f"예산 보호 배수: {reserve_ratio:.2f} | {paid_opt_in_env}=true 일 때만 유료 호출 허용")


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
        st.info(f"KIS 재시도 가능 시각 (KST): {kst} | UTC: {utc}")

    _render_live_readiness(hb)
    _render_openai_guard(hb, cands)
    st.caption(f"마지막 시간보고 알림: {hb.get('last_hourly_alert_at') or '-'}")

    st.markdown("#### 오늘 AI 선정 종목")
    st.write(f"출처: `{cands.get('source', '-')}` / 기준일: `{cands.get('date_kst', '-')}`")
    price_updated_at = str(cands.get("price_updated_at") or "-")
    st.caption(f"가격 기준: KIS 실시간 우선 (갱신 시각 UTC: {price_updated_at})")
    cand_df = pd.DataFrame(cands.get("candidates", []))
    if not cand_df.empty and "price_source" in cand_df.columns:
        total_count = len(cand_df.index)
        live_count = int((cand_df["price_source"] == "KIS_LIVE").sum())
        if live_count < total_count:
            st.warning("일부 종목은 실시간 시세를 가져오지 못해 추정가 또는 0원으로 표시됩니다.")
    st.dataframe(cand_df, width="stretch")
    return hb


def _render_account_tab() -> None:
    creds = _safe_call(_get_broker_credentials, "계좌 연결 상태 조회", default={}) or {}
    status_label = "저장됨" if creds.get("configured") else "미설정"
    st.metric("한국투자증권 OpenAPI 키", status_label)
    st.caption(
        f"AppKey: {creds.get('appkey') or '-'} | "
        f"계좌번호: {creds.get('account_no') or '-'} | "
        f"모의투자: {'예' if creds.get('is_paper') else '아니오'}"
    )

    with st.form("broker_credentials_form"):
        st.markdown("#### 계좌 연결 정보 입력")
        appkey = st.text_input("KIS AppKey", type="password")
        appsecret = st.text_input("KIS AppSecret", type="password")
        account_no = st.text_input("계좌번호", placeholder="12345678-01")
        base_url = st.text_input("KIS Base URL", value=str(creds.get("base_url") or "https://openapi.koreainvestment.com:9443"))
        is_paper = st.checkbox("모의투자 키입니다", value=bool(creds.get("is_paper", False)))
        submitted = st.form_submit_button("로컬에 암호화 저장", type="primary")

    if submitted:
        payload = {
            "appkey": appkey,
            "appsecret": appsecret,
            "account_no": account_no,
            "base_url": base_url,
            "is_paper": is_paper,
        }
        res = _safe_call(lambda: _api_post("/broker/credentials", payload), "계좌 연결 정보 저장", default={}) or {}
        if res.get("ok"):
            st.success("계좌 연결 정보를 로컬에 암호화 저장했습니다.")
            st.rerun()

    if st.button("저장된 연결 정보 점검"):
        res = _safe_call(lambda: _api_post("/broker/test", {}), "계좌 연결 점검", default={}) or {}
        if res.get("ok"):
            st.success(res.get("reason", "CREDENTIALS_SAVED"))
            st.write(res.get("summary", {}))
        else:
            st.warning(res.get("reason", "INVALID_CREDENTIALS"))
            st.write(res)


def _render_portfolio_tab(hb: dict[str, Any]) -> None:
    live_result: dict[str, Any] | None = None
    if st.button("포트폴리오 새로고침", type="primary"):
        live_result = _safe_call(lambda: _api_post("/refresh/portfolio"), "포트폴리오 갱신", default={}) or {}

    choice = choose_portfolio_snapshot(
        live_result=live_result,
        cached_snapshot=None,
        file_snapshot=_load_snapshot_file(),
        session_snapshot=st.session_state.get("portfolio_snapshot"),
    )
    snapshot = choice.snapshot
    if isinstance(snapshot, dict):
        _remember_snapshot(snapshot, source=choice.source)
    if live_result is not None:
        if choice.source == "live_refresh":
            st.session_state["portfolio_last_error"] = None
            st.success("포트폴리오를 갱신했습니다.")
        else:
            st.session_state["portfolio_last_error"] = live_result.get("result", live_result)
            st.warning(f"갱신 실패: {choice.warning or 'unknown'}")

    if not isinstance(snapshot, dict):
        st.error("표시할 포트폴리오 스냅샷이 없습니다.")
        return

    if choice.warning:
        st.warning(f"최근 갱신 오류: {choice.warning}")

    account = snapshot.get("account", {}) if isinstance(snapshot.get("account"), dict) else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("주문가능현금", f"{float(hb.get('orderable_cash', 0) or 0):,.0f}원")
    c2.metric("D+2 예수금", f"{float(hb.get('d2_cash', 0) or 0):,.0f}원")
    c3.metric("총평가금액", f"{float(account.get('total_eval', 0) or 0):,.0f}원")
    c4.metric("보유종목", f"{len(snapshot.get('positions') or [])}개")

    st.caption(f"스냅샷 시각: {st.session_state.get('portfolio_snapshot_updated_at', '-')}")
    st.markdown("#### 보유 종목")
    st.dataframe(pd.DataFrame(snapshot.get("positions", [])), width="stretch")
    st.markdown("#### 주문/체결")
    st.dataframe(pd.DataFrame(snapshot.get("orders", [])), width="stretch")


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
    cross_signals = chart_data.get("cross_signals", []) if isinstance(chart_data.get("cross_signals"), list) else []
    if not bars:
        st.warning("차트 데이터가 없습니다.")
        return

    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts", "close"]).sort_values("ts")
    ma_cols = [col for col in ["ma_short", "ma_long"] if col in df.columns]
    ma_df = (
        df.melt(id_vars=["ts"], value_vars=ma_cols, var_name="average", value_name="average_price").dropna(subset=["average_price"])
        if ma_cols
        else pd.DataFrame()
    )
    ev = pd.DataFrame(events)
    if not ev.empty:
        ev["ts"] = pd.to_datetime(ev["ts"], errors="coerce")
        ev = ev.dropna(subset=["ts", "price"])
    sig = pd.DataFrame(cross_signals)
    if not sig.empty:
        sig["ts"] = pd.to_datetime(sig["ts"], errors="coerce")
        sig = sig.dropna(subset=["ts", "price"])

    line = alt.Chart(df).mark_line(color="#334155", strokeWidth=2).encode(x="ts:T", y=alt.Y("close:Q", title="가격"))
    layers = [line]
    if not ma_df.empty:
        ma_line = alt.Chart(ma_df).mark_line(strokeDash=[6, 3], strokeWidth=1.8).encode(
            x="ts:T",
            y="average_price:Q",
            color=alt.Color("average:N", scale=alt.Scale(domain=["ma_short", "ma_long"], range=["#f59e0b", "#2563eb"])),
            tooltip=["ts:T", "average:N", "average_price:Q"],
        )
        layers.append(ma_line)
    if not ev.empty:
        points = alt.Chart(ev).mark_point(filled=True, size=170).encode(
            x="ts:T",
            y="price:Q",
            color=alt.Color("event:N", scale=alt.Scale(domain=["BUY", "SELL"], range=["#16a34a", "#dc2626"])),
            tooltip=["ts:T", "event:N", "price:Q", "qty:Q"],
        )
        layers.append(points)
    if not sig.empty:
        signal_points = alt.Chart(sig).mark_point(filled=True, size=230, shape="diamond").encode(
            x="ts:T",
            y="price:Q",
            color=alt.Color("signal:N", scale=alt.Scale(domain=["GOLDEN_CROSS", "DEAD_CROSS"], range=["#f59e0b", "#7f1d1d"])),
            tooltip=["ts:T", "signal:N", "price:Q", "ma_short:Q", "ma_long:Q"],
        )
        layers.append(signal_points)
    st.altair_chart(alt.layer(*layers).properties(height=380), width="stretch")


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
  <p>계좌 연결, 운영상태, 포트폴리오, 후보 종목, 매매 차트를 한 화면에서 점검합니다.</p>
</div>
""",
    unsafe_allow_html=True,
)

tab_status, tab_account, tab_port, tab_chart, tab_ops = st.tabs(["운영상태", "계좌연결", "포트폴리오", "매매차트", "운영도구"])
with tab_status:
    _render_status_tab()
with tab_account:
    _render_account_tab()
with tab_port:
    hb_for_port = _safe_call(_get_heartbeat, "엔진 상태 조회", default={}) or {}
    _render_portfolio_tab(hb_for_port)
with tab_chart:
    _render_chart_tab()
with tab_ops:
    _render_ops_tab()
