from __future__ import annotations

import json
import logging
import os
from io import StringIO
from typing import Any, Callable

import pandas as pd
import streamlit as st

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

from app.core.config import ConfigManager
from app.core.database import Database
from app.core.engine import AutoTradingEngine
from app.core.market_hours import get_market_status
from app.core.reporting import aggregate_performance, load_closed_trades, symbol_contribution
from app.services.kakao import KakaoNotifier
from app.utils.errors import unwrap_exception

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

st.set_page_config(page_title="국내주식 완전자동 매매", layout="wide")

cfg_mgr = ConfigManager()
db = Database()
engine = AutoTradingEngine(cfg_mgr, db, KakaoNotifier(token=os.getenv("KAKAO_TOKEN")))
logger = logging.getLogger(__name__)
LOG_PATH_HINT = "/opt/AutoTrade-System/logs/engine.out.log, /opt/AutoTrade-System/logs/engine.err.log"


SENSITIVE_ENV_KEYS = {
    "KIS_APPKEY",
    "KIS_APPSECRET",
    "KIS_ACCOUNT_NO",
    "KAKAO_TOKEN",
    "NEWS_API_KEY",
}
STATUS_BLOCK_KEYS = {
    "orderable_cash",
    "available_cash",
    "cash",
    "dnca_tot_amt",
    "equity",
    "account",
    "portfolio",
    "positions",
    "orders",
    "trades",
    "raw_summary",
    "account_no",
}


def _can_write_target(path: str) -> bool:
    p = os.path.abspath(path)
    parent = os.path.dirname(p) if os.path.splitext(p)[1] else p
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception:
        return False
    if os.path.isfile(p):
        return os.access(p, os.W_OK)
    return os.access(parent, os.W_OK)


WRITE_ENABLED = all([
    _can_write_target("data"),
    _can_write_target("logs"),
    _can_write_target("strategy.yaml"),
])
if not WRITE_ENABLED:
    logger.warning("event=UI_PERMISSION_WARN writable=false targets=data/logs/strategy.yaml")

st.title("국내주식 완전자동 매매 시스템")


def _classify_reason(exc: Exception) -> str:
    err_type, message, detail = unwrap_exception(exc)
    combined = " ".join([str(err_type or ""), str(message or ""), str(detail or "")]).lower()
    if any(token in combined for token in ["cooldown", "token", "retryerror"]):
        return "토큰 쿨다운"
    if any(token in combined for token in ["market", "closed", "장", "휴장"]):
        return "장마감/장상태"
    if any(token in combined for token in ["network", "timeout", "connection", "http"]):
        return "네트워크"
    return "일시적 시스템 오류"


def safe_call(fn: Callable[[], Any], label: str, default: Any = None) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.exception("ui_safe_call_failed label=%s", label)
        reason = _classify_reason(exc)
        if "후보" in label:
            st.warning("후보 조회 실패(사유: KIS 토큰 쿨다운/장마감/네트워크). 잠시 후 재시도")
        else:
            st.error(f"{label} 갱신 실패(사유: {reason}).")
        st.caption(f"상세 원인은 서버 로그를 확인하세요: {LOG_PATH_HINT}")
        return default


def sanitize_status(payload: dict[str, Any]) -> dict[str, Any]:
    def _sanitize(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                key_l = str(k).lower()
                if any(block in key_l for block in STATUS_BLOCK_KEYS):
                    continue
                cleaned = _sanitize(v)
                if cleaned is not None:
                    out[k] = cleaned
            return out
        if isinstance(value, list):
            items = [_sanitize(v) for v in value]
            return [v for v in items if v is not None]
        if isinstance(value, str) and "account_no" in value.lower():
            return None
        return value

    return _sanitize(payload) or {}


def mask_env_rows(rows: list[tuple[str, str | None]]) -> tuple[list[dict[str, str]], dict[str, str]]:
    visible: list[dict[str, str]] = []
    has_kis = False
    has_kakao = False

    for key, value in rows:
        if key in SENSITIVE_ENV_KEYS:
            if key.startswith("KIS_"):
                has_kis = has_kis or bool(value)
            if key == "KAKAO_TOKEN":
                has_kakao = has_kakao or bool(value)
            continue
        visible.append({"key": key, "status": "설정됨" if value else "미설정"})

    summary = {
        "KIS 설정": "OK" if has_kis else "NOT SET",
        "KAKAO 설정": "OK" if has_kakao else "NOT SET",
    }
    return visible, summary


def _show_toggle_status() -> None:
    hb = safe_call(lambda: engine.heartbeat(), "운영 상태", default={}) or {}
    current = bool(hb.get("enabled", False))
    if "auto_trade_toggle" not in st.session_state:
        st.session_state["auto_trade_toggle"] = current
    toggled = st.toggle(
        "자동매매 ON/OFF",
        value=bool(st.session_state.get("auto_trade_toggle", current)),
        key="auto_trade_toggle",
        disabled=not WRITE_ENABLED,
    )
    if toggled != current:
        ok = safe_call(lambda: engine.set_auto_trading_enabled(toggled), "자동매매 토글 저장", default=False)
        if ok is not False:
            st.success(f"자동매매 {'ON' if toggled else 'OFF'} 저장 완료")
        else:
            st.session_state["auto_trade_toggle"] = current

    hb = safe_call(lambda: engine.heartbeat(), "운영 상태", default={}) or {}
    if hb.get("enabled") and hb.get("blocker"):
        st.warning(f"자동매매는 ON이지만 현재 차단중: {hb.get('blocker')} (next_retry_at={hb.get('next_retry_at')})")


def _show_portfolio(include_controls: bool = False) -> None:
    hb = safe_call(lambda: engine.heartbeat(), "포트폴리오", default={}) or {}
    if include_controls:
        c1, c2 = st.columns([1, 2])
        auto_on = c1.checkbox("자동 갱신", value=False, key="portfolio_auto")
        sec = c2.selectbox("자동 갱신 주기", [15, 30, 60], index=1, key="portfolio_auto_sec")
        if auto_on and st_autorefresh is not None:
            st_autorefresh(interval=sec * 1000, key="portfolio_refresh_counter")

    cooldown_active = hb.get("blocker") == "KIS_TOKEN_COOLDOWN"
    refresh_disabled = bool(cooldown_active and hb.get("next_retry_at"))

    if st.button("포트폴리오 수동 갱신", key="refresh_portfolio_tab", disabled=(refresh_disabled or not WRITE_ENABLED)):
        if requests is None:
            st.warning("requests 패키지가 없어 엔진 API 호출을 수행할 수 없습니다.")
        else:
            def _refresh() -> dict[str, Any]:
                resp = requests.post("http://127.0.0.1:8000/refresh/portfolio", timeout=8)
                return resp.json() if resp.status_code == 200 else {"ok": False, "result": {"reason": f"HTTP_{resp.status_code}"}}

            payload = safe_call(_refresh, "포트폴리오")
            if payload:
                result = payload.get("result", {})
                if payload.get("ok"):
                    st.success(f"포트폴리오 갱신 완료 source={result.get('source')} last_updated={result.get('snapshot', {}).get('ts')}")
                else:
                    st.warning(f"포트폴리오 갱신 실패/제한: reason={result.get('reason')} next_retry_at={result.get('next_retry_at')}")

    if refresh_disabled:
        st.warning(f"KIS token cooldown active. next_retry_at={hb.get('next_retry_at')} (UI는 KIS 직접 호출 없이 엔진 API/캐시만 사용)")

    snap = safe_call(lambda: engine.get_cached_portfolio_snapshot() or {}, "포트폴리오", default={}) or {}
    account = snap.get("account", {})

    st.caption(f"마지막 스냅샷: {snap.get('ts', '-')}")
    orderable_cash = float(hb.get("orderable_cash", 0) or 0)
    source = str(hb.get("orderable_cash_source", "unknown") or "unknown")
    d2_cash = float(hb.get("d2_cash", snap.get("d2_cash", account.get("d2_cash", 0))) or 0)

    c1, c2, c3, c4 = st.columns(4)
    orderable_label = "주문가능금액" if source != "cached" else "주문가능금액 (cached)"
    c1.metric(orderable_label, f"{orderable_cash:,.0f}원")
    c2.metric("D+2 예수금", f"{d2_cash:,.0f}원")
    c3.metric("총 평가금액", f"{account.get('total_eval', 0):,.0f}원")
    total_pnl = float(account.get("raw_summary", {}).get("evlu_pfls_smtl_amt", 0) or 0)
    total_ret = float(account.get("raw_summary", {}).get("evlu_pfls_rt", 0) or 0)
    c4.metric("총 손익/수익률", f"{total_pnl:,.0f}원 / {total_ret:.2f}%")

    st.caption(f"source={hb.get('orderable_cash_source')} stale={hb.get('orderable_cash_stale')} last_updated={hb.get('orderable_cash_last_updated_at')}")

    st.markdown("#### 보유종목")
    st.dataframe(pd.DataFrame(snap.get("positions", [])), use_container_width=True)
    st.markdown("#### 주문/체결(최근 N건)")
    st.dataframe(pd.DataFrame(snap.get("orders", [])), use_container_width=True)
    if snap.get("warning") == "ORDERABLE_CASH_MAPPING_SUSPECT" or hb.get("snapshot_warning") == "ORDERABLE_CASH_MAPPING_SUSPECT":
        st.warning("주문가능금액 매핑 의심: KIS 응답 키 확인 필요(디버그 로그 참조).")


tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["운영 상태", "포트폴리오", "전략 설정", "환경변수", "리포트", "수동 진행/자동매매진단"])

if not WRITE_ENABLED:
    st.warning("읽기 전용 환경 감지: data/logs/strategy.yaml 쓰기 권한 없음. 토글/저장/수동갱신이 비활성화됩니다.")


with tab1:
    _show_toggle_status()

    st.subheader("장 상태")
    status = safe_call(get_market_status, "장 상태")
    if status:
        st.write({"is_open": status.is_open, "can_place_order": status.can_place_order, "reason": status.reason})

    st.subheader("운영 상태 / 시스템 상태")
    hb = safe_call(lambda: engine.heartbeat(), "운영 상태", default={}) or {}
    sanitized_status = sanitize_status({
        "enabled": hb.get("enabled"),
        "blocker": hb.get("blocker"),
        "recent_blockers": hb.get("recent_blockers", []),
        "next_retry_at": hb.get("next_retry_at"),
        "current_profile": hb.get("current_profile"),
        "candidates_count": hb.get("candidates_count"),
        "open_positions": hb.get("open_positions"),
        "daily_trades": hb.get("daily_trades"),
    })
    st.json(sanitized_status)
    st.info("운영 상태 탭에서는 실계좌 금액/보유종목/주문내역을 표시하지 않습니다. 실계좌 정보는 포트폴리오 탭에서 확인하세요.")

with tab2:
    st.subheader("포트폴리오")
    _show_portfolio(include_controls=True)

with tab3:
    st.subheader("단계별 돌파 전략 파라미터")
    cfg = safe_call(cfg_mgr.load, "전략 설정", default={}) or {}
    if cfg:
        mode = st.selectbox("매매 모드", ["DRY-RUN", "LIVE"], index=0 if cfg.get("mode") == "DRY-RUN" else 1)
        cfg["mode"] = mode
        cfg["scan_interval_seconds"] = st.slider("스캔 주기(초)", 30, 120, int(cfg.get("scan_interval_seconds", 60)))
        for key, val in cfg.get("risk_limits", {}).items():
            cfg["risk_limits"][key] = st.number_input(f"risk_limits.{key}", value=float(val), key=f"risk_{key}")
        if st.button("전략 저장(핫리로드)", disabled=not WRITE_ENABLED):
            result = safe_call(lambda: cfg_mgr.save(cfg), "전략 저장", default=False)
            if result is not False:
                st.success("저장 완료")

with tab4:
    st.subheader("환경변수(.env) 기반 시크릿 상태")
    rows = [(k, os.getenv(k)) for k in ["AUTOTRADE_ENV", "AUTOTRADE_REGION", "LOG_LEVEL", "KIS_APPKEY", "KIS_APPSECRET", "KIS_ACCOUNT_NO", "KAKAO_TOKEN", "NEWS_API_KEY"]]
    visible_rows, summary = mask_env_rows(rows)
    st.table(pd.DataFrame(visible_rows))
    st.table(pd.DataFrame([{"항목": k, "상태": v} for k, v in summary.items()]))
    st.caption("보안을 위해 계좌번호/앱키/토큰 등 민감 환경변수 항목은 UI에서 숨김 처리됩니다.")

with tab5:
    st.subheader("성과 리포트")
    df = safe_call(lambda: load_closed_trades(db), "리포트", default=pd.DataFrame())
    period_map = {"일별": "D", "월별": "M", "분기별": "Q", "연도별": "Y"}
    period_name = st.selectbox("집계 주기", list(period_map.keys()))
    if isinstance(df, pd.DataFrame) and not df.empty:
        agg = safe_call(lambda: aggregate_performance(df, period_map[period_name]), "리포트", default=pd.DataFrame())
        contrib = safe_call(lambda: symbol_contribution(df), "리포트", default=pd.DataFrame())
        if isinstance(agg, pd.DataFrame) and not agg.empty:
            st.dataframe(agg, use_container_width=True)
            if isinstance(contrib, pd.DataFrame):
                st.dataframe(contrib, use_container_width=True)
            csv_buf = StringIO()
            agg.to_csv(csv_buf, index=False)
            st.download_button("CSV 다운로드", data=csv_buf.getvalue(), file_name="performance_report.csv", mime="text/csv")

with tab6:
    st.subheader("우량주 후보")
    if st.button("지금 우량주 검색"):
        refreshed = safe_call(lambda: engine.refresh_news_candidates(force=True), "뉴스 후보")
        if refreshed is not None:
            st.write(refreshed)

    status_payload = safe_call(engine.get_news_status, "뉴스 상태")
    if status_payload is not None:
        st.write(status_payload)

    preview = safe_call(lambda: engine.get_buy_candidates_preview(top_n=20), "후보 조회", default={}) or {}
    reason = str(preview.get("reason", ""))
    next_retry_at = preview.get("next_retry_at")
    last_updated_at = preview.get("last_updated_at")
    next_update_at = preview.get("next_update_at")

    if reason and reason != "OK":
        st.warning(reason if reason else "일시적으로 후보 미리보기를 표시할 수 없습니다.")
        if next_retry_at:
            st.caption(f"next_retry_at: {next_retry_at}")
        st.caption(f"last_updated_at: {last_updated_at}")
        st.caption(f"next_update_at: {next_update_at}")
    else:
        rows = preview.get("rows", [])
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("표시할 캐시 후보가 없습니다.")
            st.caption(f"last_updated_at: {last_updated_at}")
            st.caption(f"next_update_at: {next_update_at}")
