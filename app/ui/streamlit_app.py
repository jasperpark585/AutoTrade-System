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
from app.services.kis_client import KISError
from app.utils.errors import unwrap_exception

st.set_page_config(page_title="국내주식 완전자동 매매", layout="wide")

cfg_mgr = ConfigManager()
db = Database()
engine = AutoTradingEngine(cfg_mgr, db, KakaoNotifier(token=os.getenv("KAKAO_TOKEN")))

st.title("국내주식 완전자동 매매 시스템")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["운영 상태", "전략 설정", "환경변수", "리포트", "수동 진행/자동매매 진단"])


def _mask_env(value: str | None) -> str:
    if not value:
        return "(미설정)"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _render_portfolio_snapshot() -> None:
    st.subheader("계좌 요약 / 보유 종목")
    try:
        snap = engine.get_portfolio_snapshot()
        summary = snap["summary"]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("주문가능현금", f"{summary.get('available_cash', 0):,.0f}원")
        c2.metric("예수금", f"{summary.get('cash', 0):,.0f}원")
        c3.metric("D+2 예수금", f"{summary.get('d2_deposit', 0):,.0f}원")
        c4.metric("총평가", f"{summary.get('total_eval', 0):,.0f}원")
        c5.metric("총자산", f"{summary.get('total_asset', 0):,.0f}원")
        positions = pd.DataFrame(snap["positions"])
        st.dataframe(positions, use_container_width=True)
    except Exception as exc:
        err_type, message, detail = unwrap_exception(exc)
        st.error(f"잔고 조회 실패 [{err_type}]: {message}")
        if detail:
            st.json(detail)


with tab1:
    status = get_market_status()
    st.subheader("장 상태")
    st.write({"is_open": status.is_open, "can_place_order": status.can_place_order, "reason": status.reason})

    _render_portfolio_snapshot()

    signals = db.fetch_df("SELECT created_at, symbol, total_score, stage_scores, pass_fail, reason FROM signals ORDER BY id DESC LIMIT 50")
    if not signals.empty:
        signals["stage_scores"] = signals["stage_scores"].apply(lambda x: json.loads(x))
    st.subheader("최근 종목 점수/근거")
    st.dataframe(signals, use_container_width=True)

    open_trades = db.fetch_df("SELECT * FROM trades WHERE status='OPEN' ORDER BY id DESC")
    st.subheader("보유 포지션(엔진 내부)")
    st.dataframe(open_trades, use_container_width=True)

with tab2:
    st.subheader("단계별 돌파 전략 파라미터")
    cfg = cfg_mgr.load()
    mode = st.selectbox("매매 모드", ["DRY-RUN", "LIVE"], index=0 if cfg["mode"] == "DRY-RUN" else 1)
    cfg["mode"] = mode
    cfg["scan_interval_seconds"] = st.slider("스캔 주기(초)", 30, 120, int(cfg["scan_interval_seconds"]))

    st.markdown("#### 리스크 제한")
    for key, val in cfg["risk_limits"].items():
        cfg["risk_limits"][key] = st.number_input(f"risk_limits.{key}", value=float(val), key=f"risk_{key}")

    for stage_name, stage_cfg in cfg["stages"].items():
        with st.expander(f"{stage_name}", expanded=False):
            for key, val in list(stage_cfg.items()):
                if isinstance(val, bool):
                    stage_cfg[key] = st.checkbox(f"{stage_name}.{key}", value=val, key=f"{stage_name}_{key}")
                elif isinstance(val, (int, float)):
                    stage_cfg[key] = st.number_input(f"{stage_name}.{key}", value=float(val), key=f"{stage_name}_{key}")
                elif isinstance(val, list):
                    stage_cfg[key] = st.text_input(f"{stage_name}.{key} (comma)", value=",".join(map(str, val)), key=f"{stage_name}_{key}").split(",")
                elif isinstance(val, dict):
                    st.caption(f"{stage_name}.{key}: 유료 동일값 입력칸")
                    for sk, sv in val.items():
                        if isinstance(sv, bool):
                            val[sk] = st.checkbox(f"{stage_name}.{key}.{sk}", value=sv, key=f"{stage_name}_{key}_{sk}")
                        else:
                            val[sk] = st.text_input(f"{stage_name}.{key}.{sk}", value="" if sv is None else str(sv), key=f"{stage_name}_{key}_{sk}")
                else:
                    stage_cfg[key] = st.text_input(f"{stage_name}.{key}", value=str(val), key=f"{stage_name}_{key}")

    if st.button("전략 저장(핫리로드)"):
        for stage in cfg["stages"].values():
            for k, v in stage.items():
                if isinstance(v, list):
                    converted = []
                    for x in v:
                        try:
                            converted.append(float(x))
                        except ValueError:
                            converted.append(x)
                    stage[k] = converted
        cfg_mgr.save(cfg)
        st.success("저장 완료. 엔진은 다음 tick에서 자동 반영됩니다.")

with tab3:
    st.subheader("환경변수(.env) 기반 시크릿 상태")
    st.info("보안 정보는 UI 저장 없이 .env/시스템 환경변수에서만 로드됩니다.")
    env_keys = ["KIS_APPKEY", "KIS_APPSECRET", "KIS_ACCOUNT_NO", "KAKAO_TOKEN", "AUTOTRADE_MASTER_PASSPHRASE"]
    st.table({"key": env_keys, "value(masked)": [_mask_env(os.getenv(k)) for k in env_keys]})

with tab4:
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
        st.subheader("종목별 기여도")
        st.dataframe(contrib, use_container_width=True)
        csv_buf = StringIO()
        agg.to_csv(csv_buf, index=False)
        st.download_button("CSV 다운로드", data=csv_buf.getvalue(), file_name="performance_report.csv", mime="text/csv")

with tab5:
    st.subheader("수동 진행 기능")
    _render_portfolio_snapshot()

    st.markdown("### 자동매수 후보 TOP N")
    top_n = st.slider("후보 개수", min_value=3, max_value=20, value=10)
    if st.button("후보 새로고침"):
        try:
            st.session_state["candidate_preview"] = engine.get_buy_candidates_preview(top_n=top_n)
        except Exception as exc:
            err_type, message, detail = unwrap_exception(exc)
            st.error(f"후보 조회 실패 [{err_type}]: {message}")
            if detail:
                st.json(detail)

    if "candidate_preview" in st.session_state:
        st.dataframe(pd.DataFrame(st.session_state["candidate_preview"]), use_container_width=True)

    if st.button("1) 수동 진단 실행", use_container_width=True):
        diag = engine.run_manual_diagnosis()
        st.session_state["manual_diag"] = diag

    if "manual_diag" in st.session_state:
        diag = st.session_state["manual_diag"]
        st.markdown(f"- {'✅' if diag['market'].can_place_order else '❌'} **시장 단계**: {diag['market'].reason}")
        st.markdown(f"- {'✅' if diag['risk_ok'] else '❌'} **리스크 단계**: {diag['risk_reason']}")
        st.markdown(f"- {'✅' if diag['env_reason'] == '정상' else '❌'} **환경변수 단계**: {diag['env_reason']}")

        if diag["error"]:
            st.error(f"시세/전략 진단 실패 [{diag.get('error_type')}]: {diag['error']}")
            if diag.get("error_detail"):
                st.json(diag["error_detail"])

        if diag["rows"]:
            df_diag = pd.DataFrame(diag["rows"])
            st.dataframe(df_diag, use_container_width=True)

    st.divider()
    st.subheader("2) 수동 주문 테스트")
    with st.form("manual_order_form"):
        symbol = st.text_input("종목코드", value="005930")
        side = st.selectbox("매수/매도", ["BUY", "SELL"])
        qty = st.number_input("수량", min_value=1, value=1, step=1)
        price = st.number_input("주문가격(시장가 테스트는 0)", min_value=0.0, value=70000.0, step=1.0)
        submit_order = st.form_submit_button("수동 주문 실행")

    if submit_order:
        try:
            result = engine.manual_place_order(symbol=symbol, qty=int(qty), side=side, price=float(price))
            st.success("수동 주문 요청 완료")
            st.json(result)
        except Exception as exc:
            err_type, message, detail = unwrap_exception(exc)
            st.error(f"수동 주문 실패 [{err_type}]: {message}")
            if detail:
                st.json(detail)
