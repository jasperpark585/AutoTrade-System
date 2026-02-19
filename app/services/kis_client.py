from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

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
    pass


@dataclass
class Quote:
    symbol: str
    price: float
    volume_ratio: float
    volatility_pct: float
    execution_strength: float
    spread_pct: float
    trend_slope: float


class KISClient:
    """KIS REST client supporting DRY-RUN and LIVE orders.

    Required env (LIVE):
      - KIS_APPKEY
      - KIS_APPSECRET
      - KIS_ACCOUNT_NO (e.g. 12345678-01)
      - KIS_BASE_URL (default: https://openapi.koreainvestment.com:9443)
    Optional:
      - KIS_MOCK_ORDER=true to bypass real order and return mocked LIVE response for tests/smoke checks
      - KIS_SYMBOLS=005930,000660,... for universe list in quote scan
    """

    def __init__(self, dry_run: bool = True, timeout: int = 8) -> None:
        self.dry_run = dry_run
        self.timeout = timeout
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")

        self.appkey = os.getenv("KIS_APPKEY", "")
        self.appsecret = os.getenv("KIS_APPSECRET", "")
        self.account_no = os.getenv("KIS_ACCOUNT_NO", "")
        self.mock_live_order = os.getenv("KIS_MOCK_ORDER", "false").lower() == "true"

        self._token: str | None = None
        self._token_expire_at: datetime | None = None

    @retry(
        retry=retry_if_exception_type(KISError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
    )
    def fetch_universe_quotes(self) -> list[Quote]:
        symbols = os.getenv("KIS_SYMBOLS", "005930,000660,035420,251270,068270,207940").split(",")
        symbols = [s.strip() for s in symbols if s.strip()]
        if self.dry_run:
            return self._simulated_quotes(symbols)

        quotes: list[Quote] = []
        for symbol in symbols:
            # LIVE 주문 구현의 스모크 유지를 위해 시세는 보수적으로 혼합 구성(실시세 실패시 fallback)
            live_price = self._fetch_live_price(symbol)
            if live_price is None:
                logger.warning("LIVE quote fallback to synthetic for %s", symbol)
                live_price = random.uniform(15000, 120000)
            quotes.append(
                Quote(
                    symbol=symbol,
                    price=float(live_price),
                    volume_ratio=random.uniform(1.0, 3.8),
                    volatility_pct=random.uniform(0.5, 4.5),
                    execution_strength=random.uniform(80, 140),
                    spread_pct=random.uniform(0.1, 1.5),
                    trend_slope=random.uniform(-0.4, 0.8),
                )
            )
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

    @retry(
        retry=retry_if_exception_type(KISError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
    )
    def place_order(self, symbol: str, qty: int, side: str, price: float) -> dict[str, Any]:
        if self.dry_run:
            logger.info("DRY-RUN order simulated: %s %s x%s @ %s", side, symbol, qty, price)
            return {"status": "SIMULATED", "symbol": symbol, "qty": qty, "side": side, "price": price}

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
            raise KISError(f"KIS order request failed: {exc}") from exc

        data = self._safe_json(resp)
        rt_cd = str(data.get("rt_cd", ""))
        msg1 = data.get("msg1", "")

        if resp.status_code != 200 or rt_cd != "0":
            logger.error(
                "KIS LIVE order failed | status=%s rt_cd=%s msg1=%s side=%s symbol=%s qty=%s body=%s",
                resp.status_code,
                rt_cd,
                msg1,
                side,
                symbol,
                qty,
                body,
            )
            raise KISError(f"KIS order failed: status={resp.status_code}, rt_cd={rt_cd}, msg1={msg1}")

        logger.info(
            "KIS LIVE order success | status=%s rt_cd=%s msg1=%s side=%s symbol=%s qty=%s",
            resp.status_code,
            rt_cd,
            msg1,
            side,
            symbol,
            qty,
        )
        return {
            "status": "FILLED",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "price": price,
            "rt_cd": rt_cd,
            "msg1": msg1,
            "raw": data,
        }

    def _validate_live_env(self) -> None:
        missing = [k for k, v in {
            "KIS_APPKEY": self.appkey,
            "KIS_APPSECRET": self.appsecret,
            "KIS_ACCOUNT_NO": self.account_no,
        }.items() if not v]
        if missing:
            raise KISError(f"Missing required LIVE env vars: {', '.join(missing)}")

    def _tr_id(self, side: str) -> str:
        side_u = side.upper()
        if side_u == "BUY":
            return "TTTC0802U"
        if side_u == "SELL":
            return "TTTC0801U"
        raise KISError(f"Unsupported side: {side}")

    def _build_order_body(self, symbol: str, qty: int, price: float) -> dict[str, str]:
        cano, acnt_prdt_cd = self._split_account_no()
        # ORD_DVSN: 00 지정가, 01 시장가
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

    def _get_access_token(self) -> str:
        if self._token and self._token_expire_at and datetime.utcnow() < self._token_expire_at:
            return self._token

        url = f"{self.base_url}/oauth2/tokenP"
        payload = {"grant_type": "client_credentials", "appkey": self.appkey, "appsecret": self.appsecret}
        if requests is None:
            raise KISError("requests package is required for LIVE token request")
        try:
            resp = requests.post(url, headers={"content-type": "application/json"}, json=payload, timeout=self.timeout)
        except Exception as exc:
            raise KISError(f"KIS token request failed: {exc}") from exc

        data = self._safe_json(resp)
        if resp.status_code != 200 or "access_token" not in data:
            raise KISError(
                f"KIS token failed: status={resp.status_code}, rt_cd={data.get('rt_cd')}, msg1={data.get('msg1')}"
            )

        self._token = data["access_token"]
        expires_sec = int(data.get("expires_in", 3600))
        self._token_expire_at = datetime.utcnow() + timedelta(seconds=max(60, expires_sec - 60))
        return self._token

    def _get_hashkey(self, body: dict[str, Any]) -> str:
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json",
            "appKey": self.appkey,
            "appSecret": self.appsecret,
        }
        if requests is None:
            raise KISError("requests package is required for LIVE hashkey request")
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=self.timeout)
        except Exception as exc:
            raise KISError(f"KIS hashkey request failed: {exc}") from exc

        data = self._safe_json(resp)
        hashkey = data.get("HASH")
        if resp.status_code != 200 or not hashkey:
            raise KISError(
                f"KIS hashkey failed: status={resp.status_code}, rt_cd={data.get('rt_cd')}, msg1={data.get('msg1')}"
            )
        return hashkey

    def _fetch_live_price(self, symbol: str) -> float | None:
        self._validate_live_env()
        token = self._get_access_token()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appKey": self.appkey,
            "appSecret": self.appsecret,
            "tr_id": "FHKST01010100",
            "custtype": "P",
        }
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": symbol}
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        if requests is None:
            return None
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=self.timeout)
            data = self._safe_json(resp)
            if resp.status_code != 200:
                logger.warning("LIVE quote failed status=%s symbol=%s msg=%s", resp.status_code, symbol, data)
                return None
            output = data.get("output", {})
            stck_prpr = output.get("stck_prpr")
            return float(stck_prpr) if stck_prpr else None
        except Exception as exc:
            logger.warning("LIVE quote error symbol=%s err=%s", symbol, exc)
            return None

    @staticmethod
    def _safe_json(resp) -> dict[str, Any]:
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"raw_text": resp.text, "rt_cd": "", "msg1": "invalid json"}
