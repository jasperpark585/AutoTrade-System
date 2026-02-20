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


with tab1:
    status = get_market_status()
    st.subheader("장 상태")
    st.write({"is_open": status.is_open, "can_place_order": status.can_place_order, "reason": status.reason})

    signals = db.fetch_df("SELECT created_at, symbol, total_score, stage_scores, pass_fail, reason FROM signals ORDER BY id DESC LIMIT 50")
    if not signals.empty:
        signals["stage_scores"] = signals["stage_scores"].apply(lambda x: json.loads(x))
    st.subheader("최근 종목 점수/근거")
    st.dataframe(signals, use_container_width=True)

    open_trades = db.fetch_df("SELECT * FROM trades WHERE status='OPEN' ORDER BY id DESC")
    st.subheader("보유 포지션")
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
    st.code(
        """# .env 예시
KIS_APPKEY=...
KIS_APPSECRET=...
KIS_ACCOUNT_NO=12345678-01
KAKAO_TOKEN=...
AUTOTRADE_EQUITY_BASE_KRW=30000000
KIS_MOCK_ORDER=false
""",
        language="bash",
    )

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
    st.caption("자동매매가 멈춘 경우 어떤 단계에서 막혔는지 즉시 확인하고, 수동 주문 테스트를 진행할 수 있습니다.")

    col1, col2, col3 = st.columns(3)
    if col1.button("1) 수동 진단 실행", use_container_width=True):
        diag = engine.run_manual_diagnosis()
        st.session_state["manual_diag"] = diag

    if "manual_diag" in st.session_state:
        diag = st.session_state["manual_diag"]
        market_icon = "✅" if diag["market"].can_place_order else "❌"
        risk_icon = "✅" if diag["risk_ok"] else "❌"
        env_icon = "✅" if diag["env_reason"] == "정상" else "❌"

        st.markdown(f"- {market_icon} **시장 단계**: {diag['market'].reason}")
        st.markdown(f"- {risk_icon} **리스크 단계**: {diag['risk_reason']}")
        st.markdown(f"- {env_icon} **환경변수 단계**: {diag['env_reason']}")

        if diag["error"]:
            st.error(f"시세/전략 진단 중 오류: {diag['error']}")

        if diag["rows"]:
            df_diag = pd.DataFrame(diag["rows"])
            st.subheader("2) 종목별 단계 통과/실패 현황")
            st.dataframe(df_diag, use_container_width=True)
            blocked = df_diag[df_diag["can_auto_order_now"] == False]
            if not blocked.empty:
                st.warning("자동매매 불가 종목이 있습니다. blocker 컬럼으로 원인을 즉시 확인하세요.")
            else:
                st.success("현재 기준 자동매매 진행 가능한 후보가 있습니다.")

    st.divider()
    st.subheader("3) 수동 주문 테스트")
    st.warning("실계좌 보호를 위해 LIVE 모드에서는 소량/모의환경(KIS_MOCK_ORDER=true)으로 먼저 테스트하세요.")

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
            st.error(f"수동 주문 실패: {exc}")
