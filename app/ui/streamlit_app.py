from __future__ import annotations

import json
import os
from io import StringIO

import pandas as pd
import streamlit as st

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

st.title("국내주식 완전자동 매매 시스템")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["운영 상태", "포트폴리오", "전략 설정", "환경변수", "리포트", "수동 진행/자동매매진단"])


def _mask_env(value: str | None) -> str:
    if not value:
        return "(미설정)"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _show_toggle_status() -> None:
    hb = engine.heartbeat()
    current = bool(hb.get("enabled", False))
    toggled = st.toggle("자동매매 ON/OFF", value=current)
    if toggled != current:
        engine.set_auto_trading_enabled(toggled)
        st.success(f"자동매매 {'ON' if toggled else 'OFF'} 저장 완료")
    hb = engine.heartbeat()
    if hb.get("enabled") and hb.get("blocker"):
        st.warning(f"자동매매는 ON이지만 현재 차단중: {hb.get('blocker')} (next_retry_at={hb.get('next_retry_at')})")


def _show_portfolio(force_refresh: bool = False, include_controls: bool = False) -> None:
    hb = engine.heartbeat()
    if include_controls:
        c1, c2 = st.columns([1, 2])
        auto_on = c1.checkbox("자동 갱신", value=False, key="portfolio_auto")
        sec = c2.selectbox("자동 갱신 주기", [15, 30, 60], index=1, key="portfolio_auto_sec")
        if auto_on and st_autorefresh is not None:
            st_autorefresh(interval=sec * 1000, key="portfolio_refresh_counter")

    cooldown_active = hb.get("blocker") == "KIS_TOKEN_COOLDOWN"
    refresh_disabled = bool(cooldown_active and hb.get("next_retry_at"))
    if st.button("포트폴리오 캐시 다시읽기", key=f"refresh_portfolio_{'tab' if include_controls else 'inline'}", disabled=refresh_disabled):
        st.rerun()

    if refresh_disabled:
        st.warning(f"KIS token cooldown active. next_retry_at={hb.get('next_retry_at')} (UI는 KIS 직접 호출 없이 캐시만 표시)")

    try:
        snap = engine.get_cached_portfolio_snapshot() or {}
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

        if hb.get("last_good_orderable_at"):
            st.caption(f"last_good_orderable_at: {hb.get('last_good_orderable_at')}")

        st.markdown("#### 보유종목")
        st.dataframe(pd.DataFrame(snap.get("positions", [])), use_container_width=True)
        st.markdown("#### 주문/체결(최근 N건)")
        st.dataframe(pd.DataFrame(snap.get("orders", [])), use_container_width=True)
        if snap.get("warning") == "ORDERABLE_CASH_MAPPING_SUSPECT" or hb.get("snapshot_warning") == "ORDERABLE_CASH_MAPPING_SUSPECT":
            st.warning("주문가능금액 매핑 의심: KIS 응답 키 확인 필요(디버그 로그 참조).")
    except Exception as exc:
        err_type, message, detail = unwrap_exception(exc)
        st.error(f"포트폴리오 조회 실패 [{err_type}]: {message}")
        if detail:
            st.json(detail)


with tab1:
    _show_toggle_status()
    status = get_market_status()
    st.subheader("장 상태")
    st.write({"is_open": status.is_open, "can_place_order": status.can_place_order, "reason": status.reason})
    st.subheader("엔진 상태")
    st.json(engine.heartbeat())
    _show_portfolio(force_refresh=False, include_controls=False)

    signals = db.fetch_df("SELECT created_at, symbol, total_score, stage_scores, pass_fail, reason FROM signals ORDER BY id DESC LIMIT 50")
    if not signals.empty:
        signals["stage_scores"] = signals["stage_scores"].apply(lambda x: json.loads(x))
    st.subheader("최근 종목 점수/근거")
    st.dataframe(signals, use_container_width=True)

with tab2:
    st.subheader("포트폴리오")
    _show_portfolio(force_refresh=False, include_controls=True)

with tab3:
    st.subheader("단계별 돌파 전략 파라미터")
    cfg = cfg_mgr.load()
    mode = st.selectbox("매매 모드", ["DRY-RUN", "LIVE"], index=0 if cfg["mode"] == "DRY-RUN" else 1)
    cfg["mode"] = mode
    cfg["scan_interval_seconds"] = st.slider("스캔 주기(초)", 30, 120, int(cfg["scan_interval_seconds"]))
    for key, val in cfg["risk_limits"].items():
        cfg["risk_limits"][key] = st.number_input(f"risk_limits.{key}", value=float(val), key=f"risk_{key}")
    if st.button("전략 저장(핫리로드)"):
        cfg_mgr.save(cfg)
        st.success("저장 완료")

with tab4:
    st.subheader("환경변수(.env) 기반 시크릿 상태")
    env_keys = ["KIS_APPKEY", "KIS_APPSECRET", "KIS_ACCOUNT_NO", "KAKAO_TOKEN", "NEWS_API_KEY"]
    st.table({"key": env_keys, "value(masked)": [_mask_env(os.getenv(k)) for k in env_keys]})

with tab5:
    st.subheader("성과 리포트")
    df = load_closed_trades(db)
    period_map = {"일별": "D", "월별": "M", "분기별": "Q", "연도별": "Y"}
    period_name = st.selectbox("집계 주기", list(period_map.keys()))
    if not df.empty:
        agg = aggregate_performance(df, period_map[period_name])
        st.dataframe(agg, use_container_width=True)
        st.dataframe(symbol_contribution(df), use_container_width=True)
        csv_buf = StringIO(); agg.to_csv(csv_buf, index=False)
        st.download_button("CSV 다운로드", data=csv_buf.getvalue(), file_name="performance_report.csv", mime="text/csv")

with tab6:
    st.subheader("우량주 후보")
    if st.button("지금 우량주 검색"):
        try:
            st.write(engine.refresh_news_candidates(force=True))
        except Exception as exc:
            err_type, message, detail = unwrap_exception(exc)
            st.error(f"뉴스 후보 갱신 실패 [{err_type}]: {message}")
            if detail:
                st.json(detail)

    try:
        st.write(engine.get_news_status())
    except Exception as exc:
        err_type, message, detail = unwrap_exception(exc)
        st.error(f"뉴스 상태 조회 실패 [{err_type}]: {message}")
        if detail:
            st.json(detail)

    try:
        preview = engine.get_buy_candidates_preview(top_n=20)
        reason = str(preview.get("reason", ""))
        next_retry_at = preview.get("next_retry_at")
        last_updated_at = preview.get("last_updated_at")
        next_update_at = preview.get("next_update_at")

        if reason != "OK":
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
    except Exception as exc:
        err_type, message, detail = unwrap_exception(exc)
        st.error(f"KIS token cooldown active: {message}")
        if detail and isinstance(detail, dict) and detail.get("next_retry_at"):
            st.caption(f"next_retry_at: {detail.get('next_retry_at')}")
        if detail:
            st.json(detail)
