from __future__ import annotations

import json
import logging
import os
import random
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover
    np = None

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None

try:
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
except ModuleNotFoundError:  # pragma: no cover
    def retry(*args, **kwargs):
        def deco(fn):
            return fn
        return deco

    def retry_if_exception_type(*args, **kwargs):
        return None

    def stop_after_attempt(*args, **kwargs):
        return None

    def wait_exponential(*args, **kwargs):
        return None

from app.core.market_hours import get_market_status

logger = logging.getLogger(__name__)


class KISError(RuntimeError):
    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error_type": type(self).__name__, "message": str(self), "detail": self.detail}


class KISCooldownError(KISError):
    """Transient cooldown error raised when KIS token/API is temporarily blocked."""


@dataclass
class Quote:
    symbol: str
    price: float
    volume_ratio: float
    volatility_pct: float
    execution_strength: float
    spread_pct: float
    trend_slope: float


def calc_spread_pct(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100


def calc_trend_slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    if np is None:
        # fallback without numpy
        x = list(range(len(values)))
        x_mean = sum(x) / len(x)
        y_mean = sum(values) / len(values)
        num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, values))
        den = sum((xi - x_mean) ** 2 for xi in x)
        return num / den if den else 0.0
    x = np.arange(len(values), dtype=float)
    y = np.array(values, dtype=float)
    slope = np.polyfit(x, y, 1)[0]
    return float(slope)


class KISClient:
    def __init__(self, dry_run: bool = True, timeout: int = 8) -> None:
        self.dry_run = dry_run
        self.timeout = timeout
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")

        self.appkey = os.getenv("KIS_APPKEY", "")
        self.appsecret = os.getenv("KIS_APPSECRET", "")
        self.account_no = os.getenv("KIS_ACCOUNT_NO", "")
        self.mock_live_order = os.getenv("KIS_MOCK_ORDER", "false").lower() == "true"
        self.explicit_live = os.getenv("LIVE", "false").lower() in {"1", "true", "yes", "on"}
        self.force_dry_run = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes", "on"}

        self.trend_window = int(os.getenv("KIS_TREND_WINDOW", "20"))
        self.volume_avg_days = int(os.getenv("KIS_VOLUME_AVG_DAYS", "20"))

        self._token: str | None = None
        self._token_expire_at: datetime | None = None
        self._token_retry_after_epoch: float = 0.0
        self._token_lock = threading.Lock()

    def update_runtime_flags(self, dry_run: bool, mock_live_order: bool, explicit_live: bool, force_dry_run: bool) -> None:
        self.dry_run = dry_run
        self.mock_live_order = mock_live_order
        self.explicit_live = explicit_live
        self.force_dry_run = force_dry_run

    @retry(
        retry=retry_if_exception_type(KISError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
    )
    def fetch_universe_quotes(self, symbols: list[str] | None = None) -> list[Quote]:
        symbols = symbols or os.getenv("KIS_SYMBOLS", "005930,000660,035420,251270,068270,207940").split(",")
        symbols = [s.strip() for s in symbols if s.strip()]
        if self.dry_run:
            return self._simulated_quotes(symbols)

        self._validate_live_env()

        quotes: list[Quote] = []
        for symbol in symbols:
            quote = self._build_live_quote(symbol)
            if quote is None:
                logger.warning("LIVE quote SKIP symbol=%s (insufficient live data)", symbol)
                continue
            quotes.append(quote)
        return quotes

    def _simulated_quotes(self, symbols: list[str]) -> list[Quote]:
        quotes: list[Quote] = []
        for s in symbols:
            base = random.uniform(15000, 120000)
            quotes.append(
                Quote(
                    symbol=s,
                    price=base,
                    volume_ratio=random.uniform(0.8, 3.8),
                    volatility_pct=random.uniform(0.5, 4.5),
                    execution_strength=random.uniform(80, 140),
                    spread_pct=random.uniform(0.1, 1.5),
                    trend_slope=random.uniform(-0.4, 0.8),
                )
            )
        return quotes

    def fetch_intraday_bars(self, symbol: str, count: int = 120) -> list[dict[str, Any]]:
        clean_symbol = str(symbol or "").strip()
        if not clean_symbol:
            return []
        safe_count = max(10, min(int(count), 390))
        if self.dry_run:
            return self._simulated_intraday_bars(clean_symbol, safe_count)

        try:
            bars = self._fetch_minute_bars(clean_symbol, safe_count)
        except Exception as exc:
            logger.warning("LIVE intraday bars fetch failed symbol=%s error=%s", clean_symbol, exc)
            return []

        normalized: list[dict[str, Any]] = []
        for row in bars:
            one = self._normalize_live_minute_bar(row)
            if one:
                normalized.append(one)
        normalized.sort(key=lambda x: x.get("ts", ""))
        return normalized

    def _simulated_intraday_bars(self, symbol: str, count: int) -> list[dict[str, Any]]:
        price = random.uniform(15000, 80000)
        now = datetime.utcnow()
        out: list[dict[str, Any]] = []
        for idx in range(count):
            step = random.uniform(-0.9, 1.3)
            open_price = max(1000.0, price)
            close_price = max(1000.0, open_price + step * random.uniform(40, 220))
            high_price = max(open_price, close_price) + random.uniform(10, 130)
            low_price = max(1000.0, min(open_price, close_price) - random.uniform(10, 130))
            volume = int(random.uniform(1000, 25000))
            ts = (now - timedelta(minutes=(count - idx))).replace(second=0, microsecond=0).isoformat()
            out.append(
                {
                    "symbol": symbol,
                    "ts": ts,
                    "open": round(open_price, 2),
                    "high": round(high_price, 2),
                    "low": round(low_price, 2),
                    "close": round(close_price, 2),
                    "volume": volume,
                }
            )
            price = close_price
        return out

    def _normalize_live_minute_bar(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        close_price = float(row.get("stck_prpr", 0) or 0)
        if close_price <= 0:
            return None
        open_price = float(row.get("stck_oprc", close_price) or close_price)
        high_price = float(row.get("stck_hgpr", close_price) or close_price)
        low_price = float(row.get("stck_lwpr", close_price) or close_price)
        volume = int(float(row.get("cntg_vol", row.get("acml_vol", 0)) or 0))

        date_text = str(row.get("stck_bsop_date", datetime.utcnow().strftime("%Y%m%d")))
        time_text = str(row.get("stck_cntg_hour", "")).zfill(6)
        if len(date_text) == 8 and len(time_text) == 6 and date_text.isdigit() and time_text.isdigit():
            ts = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}T{time_text[:2]}:{time_text[2:4]}:{time_text[4:6]}"
        else:
            ts = datetime.utcnow().replace(second=0, microsecond=0).isoformat()

        return {
            "ts": ts,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
        }

    def _build_live_quote(self, symbol: str) -> Quote | None:
        price_data = self._fetch_price_full(symbol)
        if not price_data:
            return None

        bid_ask = self._fetch_bid_ask(symbol)
        day_bars = self._fetch_daily_bars(symbol, self.volume_avg_days + 1)
        minute_bars = self._fetch_minute_bars(symbol, self.trend_window)

        current_price = float(price_data.get("stck_prpr", 0) or 0)
        open_price = float(price_data.get("stck_oprc", 0) or 0)
        high_price = float(price_data.get("stck_hgpr", 0) or 0)
        low_price = float(price_data.get("stck_lwpr", 0) or 0)
        current_volume = float(price_data.get("acml_vol", 0) or 0)
        execution_strength = float(price_data.get("tday_rltv", 0) or 0)

        if current_price <= 0 or open_price <= 0 or high_price <= 0 or low_price <= 0:
            return None

        spread_pct = calc_spread_pct(bid_ask.get("bid", 0), bid_ask.get("ask", 0)) if bid_ask else None
        if spread_pct is None:
            return None

        volume_ratio = self._calc_volume_ratio(current_volume, day_bars)
        volatility_pct = ((high_price - low_price) / open_price) * 100 if open_price > 0 else 0
        trend_slope = self._calc_trend_slope(minute_bars)

        if volume_ratio is None or trend_slope is None:
            return None

        return Quote(
            symbol=symbol,
            price=current_price,
            volume_ratio=float(volume_ratio),
            volatility_pct=float(volatility_pct),
            execution_strength=float(execution_strength),
            spread_pct=float(spread_pct),
            trend_slope=float(trend_slope),
        )

    def _calc_volume_ratio(self, current_volume: float, day_bars: list[dict[str, Any]]) -> float | None:
        if current_volume <= 0 or len(day_bars) < 2:
            return None
        hist = day_bars[1:]  # exclude today
        vols = [float(x.get("acml_vol", 0) or 0) for x in hist if float(x.get("acml_vol", 0) or 0) > 0]
        if not vols:
            return None
        avg_vol = sum(vols) / len(vols)
        if avg_vol <= 0:
            return None
        return current_volume / avg_vol

    def _calc_trend_slope(self, minute_bars: list[dict[str, Any]]) -> float | None:
        closes = [float(x.get("stck_prpr", 0) or 0) for x in minute_bars if float(x.get("stck_prpr", 0) or 0) > 0]
        if len(closes) < 2:
            return None
        return calc_trend_slope(closes)

    @retry(
        retry=retry_if_exception_type(KISError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
    )
    def place_order(self, symbol: str, qty: int, side: str, price: float) -> dict[str, Any]:
        if self.dry_run:
            logger.info("DRY-RUN order simulated: %s %s x%s @ %s", side, symbol, qty, price)
            return {"status": "SIMULATED", "symbol": symbol, "qty": qty, "side": side, "price": price}

        if not self.explicit_live or self.force_dry_run:
            logger.warning("LIVE order blocked - explicit LIVE=true and DRY_RUN=false required")
            return {"status": "BLOCKED", "reason": "LIVE_FLAG_REQUIRED", "symbol": symbol, "qty": qty, "side": side}

        market = get_market_status()
        if not market.can_place_order:
            msg = f"LIVE order blocked - market closed: {market.reason}"
            logger.warning(msg)
            return {"status": "BLOCKED", "reason": market.reason, "symbol": symbol, "qty": qty, "side": side}

        self._validate_live_env()

        if self.mock_live_order:
            logger.info("LIVE-MOCK order success: %s %s x%s @ %s", side, symbol, qty, price)
            return {
                "status": "FILLED",
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "price": price,
                "rt_cd": "0",
                "msg1": "LIVE mock order success",
            }

        token = self._get_access_token()
        body = self._build_order_body(symbol=symbol, qty=qty, price=price)
        hashkey = self._get_hashkey(body)
        tr_id = self._tr_id(side)

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appKey": self.appkey,
            "appSecret": self.appsecret,
            "tr_id": tr_id,
            "custtype": "P",
            "hashkey": hashkey,
        }

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        if requests is None:
            raise KISError("requests package is required for LIVE order execution")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
        except Exception as exc:
            raise KISError("KIS order request failed", detail={"url": url, "side": side, "symbol": symbol, "error": str(exc)}) from exc

        data = self._safe_json(resp)
        rt_cd = str(data.get("rt_cd", ""))
        msg1 = data.get("msg1", "")
        output = data.get("output", {}) if isinstance(data.get("output"), dict) else {}

        if resp.status_code != 200 or rt_cd != "0":
            detail = {
                "http_status": resp.status_code,
                "rt_cd": rt_cd,
                "msg1": msg1,
                "url": url,
                "request_body": body,
                "raw_response": data,
            }
            logger.error("KIS LIVE order failed detail=%s", detail)
            raise KISError(f"KIS order failed: status={resp.status_code} rt_cd={rt_cd} msg1={msg1}", detail=detail)

        result = {
            "status": "ACCEPTED",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": price,
            "rt_cd": rt_cd,
            "msg1": msg1,
            "odno": output.get("ODNO") or output.get("odno"),
            "ord_tmd": output.get("ORD_TMD") or output.get("ord_tmd"),
            "raw": data,
        }
        logger.info("KIS LIVE order accepted result=%s", {k: result.get(k) for k in ["status", "symbol", "qty", "side", "odno", "ord_tmd", "rt_cd", "msg1"]})
        return result

    def _validate_live_env(self) -> None:
        if not self.explicit_live or self.force_dry_run:
            raise KISError(
                "LIVE mode is blocked. Set LIVE=true and DRY_RUN=false to allow real account calls.",
                detail={"required_env": {"LIVE": "true", "DRY_RUN": "false"}},
            )
        missing = [k for k, v in {
            "KIS_APPKEY": self.appkey,
            "KIS_APPSECRET": self.appsecret,
            "KIS_ACCOUNT_NO": self.account_no,
        }.items() if not v]
        if missing:
            raise KISError(f"Missing required LIVE env vars: {', '.join(missing)}", detail={"missing": missing})

    def _tr_id(self, side: str) -> str:
        side_u = side.upper()
        if side_u == "BUY":
            return "TTTC0802U"
        if side_u == "SELL":
            return "TTTC0801U"
        raise KISError(f"Unsupported side: {side}")

    def _build_order_body(self, symbol: str, qty: int, price: float) -> dict[str, str]:
        cano, acnt_prdt_cd = self._split_account_no()
        ord_unpr = "0" if int(round(price)) <= 0 else str(int(round(price)))
        ord_dvsn = "00" if ord_unpr != "0" else "01"
        return {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": ord_unpr,
        }

    def _split_account_no(self) -> tuple[str, str]:
        raw = self.account_no.replace("-", "")
        if len(raw) < 10:
            raise KISError("KIS_ACCOUNT_NO format invalid. expected e.g. 12345678-01")
        return raw[:8], raw[8:10]

    def _auth_headers(self, tr_id: str) -> dict[str, str]:
        token = self._get_access_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appKey": self.appkey,
            "appSecret": self.appsecret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get_access_token(self) -> str:
        with self._token_lock:
            now = datetime.utcnow()
            now_epoch = now.timestamp()
            if self._token and self._token_expire_at and now < self._token_expire_at:
                return self._token
            if now_epoch < self._token_retry_after_epoch:
                retry_at = datetime.utcfromtimestamp(self._token_retry_after_epoch).isoformat()
                raise KISCooldownError("KIS token cooldown active", detail={"reason": "KIS_TOKEN_COOLDOWN", "next_retry_at": retry_at, "http_status": 403})

            url = f"{self.base_url}/oauth2/tokenP"
            payload = {"grant_type": "client_credentials", "appkey": self.appkey, "appsecret": self.appsecret}
            if requests is None:
                raise KISError("requests package is required for LIVE token request")
            try:
                resp = requests.post(url, headers={"content-type": "application/json"}, json=payload, timeout=self.timeout)
            except Exception as exc:
                self._token_retry_after_epoch = datetime.utcnow().timestamp() + 60
                raise KISCooldownError("KIS token request failed", detail={"reason": "KIS_TOKEN_COOLDOWN", "url": url, "error": str(exc), "next_retry_at": datetime.utcfromtimestamp(self._token_retry_after_epoch).isoformat(), "http_status": 403}) from exc

            data = self._safe_json(resp)
            if resp.status_code != 200 or "access_token" not in data:
                self._token_retry_after_epoch = datetime.utcnow().timestamp() + 60
                msg = str(data.get("msg1", ""))
                raise KISCooldownError(
                    "KIS token temporarily unavailable",
                    detail={"reason": "KIS_TOKEN_COOLDOWN", "http_status": resp.status_code, "rt_cd": data.get("rt_cd"), "msg1": msg, "url": url, "raw_response": data, "next_retry_at": datetime.utcfromtimestamp(self._token_retry_after_epoch).isoformat()},
                )

            self._token = data["access_token"]
            expires_sec = int(data.get("expires_in", 3600))
            self._token_expire_at = datetime.utcnow() + timedelta(seconds=max(60, expires_sec - 60))
            self._token_retry_after_epoch = 0.0
            return self._token

    def _get_hashkey(self, body: dict[str, Any]) -> str:
        url = f"{self.base_url}/uapi/hashkey"
        headers = {"content-type": "application/json", "appKey": self.appkey, "appSecret": self.appsecret}
        if requests is None:
            raise KISError("requests package is required for LIVE hashkey request")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
        except Exception as exc:
            raise KISError("KIS hashkey request failed", detail={"url": url, "request_body": body, "error": str(exc)}) from exc

        data = self._safe_json(resp)
        hashkey = data.get("HASH")
        if resp.status_code != 200 or not hashkey:
            raise KISError(
                "KIS hashkey failed",
                detail={"http_status": resp.status_code, "rt_cd": data.get("rt_cd"), "msg1": data.get("msg1"), "url": url, "raw_response": data},
            )
        return hashkey

    def _fetch_price_full(self, symbol: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = self._auth_headers("FHKST01010100")
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": symbol}
        data = self._request_json("GET", url, headers=headers, params=params)
        if not data:
            return None
        output = data.get("output", {})
        return output if isinstance(output, dict) else None

    def _fetch_bid_ask(self, symbol: str) -> dict[str, float] | None:
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
        headers = self._auth_headers("FHKST01010200")
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": symbol}
        data = self._request_json("GET", url, headers=headers, params=params)
        if not data:
            return None
        out = data.get("output1", {}) if isinstance(data.get("output1"), dict) else {}
        ask = float(out.get("askp1", 0) or 0)
        bid = float(out.get("bidp1", 0) or 0)
        if ask <= 0 or bid <= 0:
            return None
        return {"ask": ask, "bid": bid}

    def _fetch_daily_bars(self, symbol: str, count: int) -> list[dict[str, Any]]:
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = self._auth_headers("FHKST03010100")
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
            "fid_period_div_code": "D",
            "fid_org_adj_prc": "1",
        }
        data = self._request_json("GET", url, headers=headers, params=params)
        if not data:
            return []
        output2 = data.get("output2", [])
        if not isinstance(output2, list):
            return []
        return output2[:count]

    def _fetch_minute_bars(self, symbol: str, count: int) -> list[dict[str, Any]]:
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        headers = self._auth_headers("FHKST03010200")
        params = {
            "fid_etc_cls_code": "",
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": symbol,
            "fid_input_hour_1": "153000",
            "fid_pw_data_incu_yn": "Y",
        }
        data = self._request_json("GET", url, headers=headers, params=params)
        if not data:
            return []
        output2 = data.get("output2", [])
        if not isinstance(output2, list):
            return []
        return output2[:count]



    @staticmethod
    def parse_money(x: Any) -> float:
        if x is None:
            return 0.0
        if isinstance(x, (int, float)):
            return float(x)
        text = str(x).replace(",", "").strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _pick_money(self, rows: list[dict[str, Any]], keys: list[str]) -> tuple[float, str | None]:
        for key in keys:
            for row in rows:
                if key in row and row.get(key) not in (None, ""):
                    return self.parse_money(row.get(key)), key
        return 0.0, None

    def _extract_account_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        output1 = data.get("output1")
        output2 = data.get("output2")

        rows: list[dict[str, Any]] = []
        if output:
            rows.append(output)
        if isinstance(output1, dict):
            rows.append(output1)
        elif isinstance(output1, list) and output1 and isinstance(output1[0], dict):
            rows.append(output1[0])
        if isinstance(output2, dict):
            rows.append(output2)
        elif isinstance(output2, list) and output2 and isinstance(output2[0], dict):
            rows.append(output2[0])

        orderable_keys = [
            "ord_psbl_cash",
            "ord_psbl_amt",
            "ord_psbl_cash_amt",
            "psbl_cash",
            "cash_psbl_amt",
        ]
        d2_keys = ["nxdy_excc_amt", "d2_auto_rdpt_amt", "prvs_rcdl_excc_amt", "d2_psbl_cash"]
        cash_keys = ["dnca_tot_amt", "dnca_tot_amt_amt", "tot_dnca_amt"]

        orderable_cash, orderable_key = self._pick_money(rows, orderable_keys)
        d2_cash, d2_key = self._pick_money(rows, d2_keys)
        cash, cash_key = self._pick_money(rows, cash_keys)

        fallback_used = False
        if orderable_cash <= 0 and cash > 0:
            orderable_cash = cash
            orderable_key = f"fallback:{cash_key or 'dnca_tot_amt'}"
            fallback_used = True

        total_eval, total_eval_key = self._pick_money(rows, ["tot_evlu_amt", "scts_evlu_amt", "tot_evlu_pfls_amt"])
        total_asset, total_asset_key = self._pick_money(rows, ["tot_asst_amt", "asst_icdc_amt"])
        if total_asset <= 0:
            total_asset = cash + total_eval

        if str(os.getenv("KIS_DEBUG_RAW", "0")).lower() in {"1", "true", "yes"}:
            o1_keys = sorted(list(rows[1].keys())) if len(rows) > 1 else []
            o2_keys = sorted(list(rows[-1].keys())) if rows else []
            logger.info(
                "account_summary keys output1=%s output2=%s orderable_key=%s d2_key=%s cash_key=%s fallback_used=%s orderable_cash=%s",
                o1_keys,
                o2_keys,
                orderable_key,
                d2_key,
                cash_key,
                fallback_used,
                int(orderable_cash),
            )

        return {
            "orderable_cash": orderable_cash,
            "available_cash": orderable_cash,
            "d2_cash": d2_cash,
            "d2_deposit": d2_cash,
            "cash": cash,
            "total_eval": total_eval,
            "total_asset": total_asset,
            "selected_keys": {
                "orderable_key": orderable_key,
                "d2_key": d2_key,
                "cash_key": cash_key,
                "total_eval_key": total_eval_key,
                "total_asset_key": total_asset_key,
                "fallback_used": fallback_used,
            },
            "raw_summary": rows[0] if rows else {},
        }

    def fetch_account_summary(self) -> dict[str, Any]:
        return self.get_account_summary()

    def fetch_positions(self) -> list[dict[str, Any]]:
        return self.get_positions()

    def get_account_summary(self) -> dict[str, Any]:
        if self.dry_run:
            equity = float(os.getenv("AUTOTRADE_EQUITY_BASE_KRW", "10000000"))
            return {
                "orderable_cash": equity,
                "available_cash": equity,
                "cash": equity,
                "d2_cash": equity,
                "d2_deposit": equity,
                "total_eval": 0.0,
                "total_asset": equity,
                "snapshot_source": "dry_run",
                "mode": "DRY-RUN",
            }

        cano, acnt_prdt_cd = self._split_account_no()
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = self._auth_headers("TTTC8434R")
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json("GET", url, headers=headers, params=params)
        return self._extract_account_summary(data if isinstance(data, dict) else {})

    def get_positions(self) -> list[dict[str, Any]]:
        if self.dry_run:
            return [
                {"symbol": "005930", "name": "삼성전자", "qty": 5, "avg_price": 70000.0, "eval_price": 71000.0, "eval_amount": 355000.0, "pnl": 5000.0, "pnl_pct": 1.43}
            ]

        cano, acnt_prdt_cd = self._split_account_no()
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = self._auth_headers("TTTC8434R")
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json("GET", url, headers=headers, params=params)
        output1 = data.get("output1", [])
        if not isinstance(output1, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in output1:
            qty = int(float(item.get("hldg_qty", 0) or 0))
            if qty <= 0:
                continue
            rows.append(
                {
                    "symbol": item.get("pdno", ""),
                    "name": item.get("prdt_name", ""),
                    "qty": qty,
                    "avg_price": float(item.get("pchs_avg_pric", 0) or 0),
                    "eval_price": float(item.get("prpr", 0) or item.get("stck_prpr", 0) or 0),
                    "eval_amount": float(item.get("evlu_amt", 0) or 0),
                    "pnl": float(item.get("evlu_pfls_amt", 0) or 0),
                    "pnl_pct": float(item.get("evlu_pfls_rt", 0) or 0),
                }
            )
        return rows

    def fetch_recent_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        if self.dry_run:
            return [
                {"symbol": "005930", "side": "BUY", "qty": 1, "price": 70000, "status": "MOCK", "order_time": datetime.utcnow().isoformat()}
            ]

        cano, acnt_prdt_cd = self._split_account_no()
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        headers = self._auth_headers("TTTC8001R")
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "INQR_STRT_DT": datetime.utcnow().strftime("%Y%m%d"),
            "INQR_END_DT": datetime.utcnow().strftime("%Y%m%d"),
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._request_json("GET", url, headers=headers, params=params)
        rows = data.get("output1", []) if isinstance(data.get("output1"), list) else []
        out = []
        for r in rows[:limit]:
            out.append(
                {
                    "symbol": r.get("pdno", ""),
                    "side": "BUY" if r.get("sll_buy_dvsn_cd") == "02" else "SELL",
                    "qty": int(float(r.get("ord_qty", 0) or 0)),
                    "price": float(r.get("ord_unpr", 0) or 0),
                    "status": r.get("ord_gno_brno", ""),
                    "order_time": r.get("ord_dt", ""),
                }
            )
        return out

    def get_token_status(self) -> dict[str, Any]:
        now_epoch = datetime.utcnow().timestamp()
        cooldown = self._token_retry_after_epoch > now_epoch
        return {
            "has_token": bool(self._token),
            "token_expire_at": self._token_expire_at.isoformat() if self._token_expire_at else None,
            "next_retry_at": datetime.utcfromtimestamp(self._token_retry_after_epoch).isoformat() if self._token_retry_after_epoch else None,
            "cooldown_active": cooldown,
        }

    def _request_json(self, method: str, url: str, headers: dict[str, str], params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if requests is None:
            raise KISError("requests package is required for LIVE API calls")
        try:
            resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=self.timeout)
        except Exception as exc:
            raise KISError("KIS request error", detail={"url": url, "method": method, "params": params, "body": json_body, "error": str(exc)}) from exc
        data = self._safe_json(resp)
        if resp.status_code != 200:
            raise KISError(
                "KIS request failed",
                detail={"http_status": resp.status_code, "rt_cd": data.get("rt_cd"), "msg1": data.get("msg1"), "url": url, "params": params, "body": json_body, "raw_response": data},
            )
        return data

    @staticmethod
    def _safe_json(resp) -> dict[str, Any]:
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"raw_text": resp.text, "rt_cd": "", "msg1": "invalid json"}
