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

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "운영 상태",
    "포트폴리오",
    "전략 설정",
    "환경변수",
    "리포트",
    "수동 진행/자동매매진단",
])


def _mask_env(value: str | None) -> str:
    if not value:
        return "(미설정)"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _show_portfolio(force_refresh: bool = False, include_controls: bool = False) -> None:
    if include_controls:
        c1, c2 = st.columns([1, 2])
        auto_on = c1.checkbox("자동 갱신", value=False, key="portfolio_auto")
        sec = c2.selectbox("자동 갱신 주기", [15, 30, 60], index=1, key="portfolio_auto_sec")
        if auto_on and st_autorefresh is not None:
            st_autorefresh(interval=sec * 1000, key="portfolio_refresh_counter")
        elif auto_on and st_autorefresh is None:
            st.warning("자동 갱신 패키지(streamlit-autorefresh)가 없어 수동 갱신만 가능합니다.")

    if st.button("포트폴리오 수동 갱신", key=f"refresh_portfolio_{'tab' if include_controls else 'inline'}"):
        force_refresh = True

    try:
        snap = engine.get_portfolio_snapshot(force_refresh=force_refresh)
        account = snap["account"]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("주문가능금액", f"{account.get('available_cash', 0):,.0f}원")
        c2.metric("D+2 예수금", f"{account.get('d2_deposit', 0):,.0f}원")
        c3.metric("총 평가금액", f"{account.get('total_eval', 0):,.0f}원")
        total_pnl = float(account.get("raw_summary", {}).get("evlu_pfls_smtl_amt", 0) or 0)
        total_ret = float(account.get("raw_summary", {}).get("evlu_pfls_rt", 0) or 0)
        c4.metric("총 손익/수익률", f"{total_pnl:,.0f}원 / {total_ret:.2f}%")

        st.caption(f"마지막 갱신: {snap.get('ts', '-')}")

        st.markdown("#### 보유종목")
        positions = pd.DataFrame(snap.get("positions", []))
        if positions.empty:
            st.info("보유 종목 없음")
        else:
            st.dataframe(positions, use_container_width=True)

        st.markdown("#### 주문/체결(최근 N건)")
        orders = pd.DataFrame(snap.get("orders", []))
        if orders.empty:
            st.info("주문/체결 내역 없음")
        else:
            st.dataframe(orders, use_container_width=True)

    except Exception as exc:
        err_type, message, detail = unwrap_exception(exc)
        st.error(f"포트폴리오 조회 실패 [{err_type}]: {message}")
        if detail:
            st.json(detail)


with tab1:
    status = get_market_status()
    st.subheader("장 상태")
    st.write({"is_open": status.is_open, "can_place_order": status.can_place_order, "reason": status.reason})

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

    st.markdown("#### 리스크 제한")
    for key, val in cfg["risk_limits"].items():
        cfg["risk_limits"][key] = st.number_input(f"risk_limits.{key}", value=float(val), key=f"risk_{key}")

    st.markdown("#### 뉴스 후보 설정")
    for key, val in cfg.get("news", {}).items():
        if isinstance(val, bool):
            cfg["news"][key] = st.checkbox(f"news.{key}", value=val, key=f"news_{key}")
        elif isinstance(val, (int, float)):
            cfg["news"][key] = st.number_input(f"news.{key}", value=float(val), key=f"news_{key}")
        else:
            cfg["news"][key] = st.text_input(f"news.{key}", value=str(val), key=f"news_{key}")

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
    if df.empty:
        st.info("아직 청산된 트레이드가 없습니다.")
    else:
        agg = aggregate_performance(df, period_map[period_name])
        st.dataframe(agg, use_container_width=True)
        contrib = symbol_contribution(df)
        st.dataframe(contrib, use_container_width=True)
        csv_buf = StringIO()
        agg.to_csv(csv_buf, index=False)
        st.download_button("CSV 다운로드", data=csv_buf.getvalue(), file_name="performance_report.csv", mime="text/csv")

with tab6:
    st.subheader("우량주 후보")
    news_status = engine.get_news_status()
    st.write(
        {
            "provider": news_status.get("provider"),
            "use_news_universe": news_status.get("use_news_universe"),
            "today_calls": news_status.get("state", {}).get("today_calls"),
            "next_update_at": news_status.get("state", {}).get("next_update_at"),
            "blocker": news_status.get("state", {}).get("blocker"),
            "candidate_count": news_status.get("candidate_count"),
        }
    )

    c1, c2 = st.columns([1, 1])
    auto_news = c1.checkbox("뉴스 자동갱신 ON", value=False, key="news_auto")
    auto_min = c2.selectbox("뉴스 자동갱신 주기(분)", [10, 30, 60], index=1, key="news_auto_min")
    if auto_news and st_autorefresh is not None:
        st_autorefresh(interval=auto_min * 60 * 1000, key="news_refresh_counter")
        try:
            engine.refresh_news_candidates(force=False)
        except Exception as exc:
            err_type, message, detail = unwrap_exception(exc)
            st.error(f"자동 뉴스 갱신 실패 [{err_type}]: {message}")
            if detail:
                st.json(detail)

    if st.button("지금 우량주 검색"):
        try:
            result = engine.refresh_news_candidates(force=True)
            st.success(f"후보 갱신 완료: {result}")
        except Exception as exc:
            err_type, message, detail = unwrap_exception(exc)
            st.error(f"뉴스 갱신 실패 [{err_type}]: {message}")
            if detail:
                st.json(detail)

    try:
        preview = engine.get_buy_candidates_preview(top_n=20)
        show_unaffordable = st.checkbox("구매불가 후보 보기", value=True)
        df_prev = pd.DataFrame(preview)
        if not show_unaffordable and not df_prev.empty and "affordable" in df_prev.columns:
            df_prev = df_prev[df_prev["affordable"] == True]
        st.dataframe(df_prev, use_container_width=True)
    except Exception as exc:
        err_type, message, detail = unwrap_exception(exc)
        st.error(f"후보 조회 실패 [{err_type}]: {message}")
        if detail:
            st.json(detail)

    st.divider()
    st.subheader("수동 주문 테스트")
    with st.form("manual_order_form"):
        symbol = st.text_input("종목코드", value="005930")
        side = st.selectbox("매수/매도", ["BUY", "SELL"])
        qty = st.number_input("수량", min_value=1, value=1, step=1)
        price = st.number_input("주문가격(시장가 테스트는 0)", min_value=0.0, value=70000.0, step=1.0)
        submit_order = st.form_submit_button("수동 주문 실행")

    if submit_order:
        try:
            if side == "BUY":
                precheck = engine.precheck_manual_buy(price=float(price), qty=int(qty))
                if not precheck["ok"]:
                    st.error(f"수동 주문 사전검증 실패: {precheck['reason']}")
                    st.json(precheck)
                    st.stop()
            result = engine.manual_place_order(symbol=symbol, qty=int(qty), side=side, price=float(price))
            st.success("수동 주문 요청 완료")
            st.json(result)
        except Exception as exc:
            err_type, message, detail = unwrap_exception(exc)
            st.error(f"수동 주문 실패 [{err_type}]: {message}")
            if detail:
                st.json(detail)
