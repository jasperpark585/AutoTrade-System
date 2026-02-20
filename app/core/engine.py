from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.config import ConfigManager
from app.core.database import Database
from app.core.market_hours import get_market_status
from app.core.strategy import StageStrategy
from app.services.kakao import KakaoNotifier
from app.services.kis_client import KISClient

logger = logging.getLogger(__name__)


@dataclass
class EngineRuntime:
    enabled: bool = False
    open_positions: dict[str, dict] = field(default_factory=dict)
    daily_trades: int = 0
    daily_loss_krw: float = 0.0
    consecutive_losses: int = 0
    cooldown_until_epoch: float = 0.0
    fatal_error: str | None = None


class AutoTradingEngine:
    def __init__(self, config_manager: ConfigManager, db: Database, notifier: KakaoNotifier):
        self.cfg_mgr = config_manager
        self.db = db
        self.runtime = EngineRuntime()
        self.notifier = notifier
        self._reload_config()

    def _reload_config(self) -> None:
        self.config = self.cfg_mgr.load()
        self.strategy = StageStrategy(self.config)
        self.kis = KISClient(dry_run=self.config.get("mode", "DRY-RUN") == "DRY-RUN")

    def enable(self, is_on: bool) -> None:
        self.runtime.enabled = is_on
        logger.info("Auto trading set to %s", is_on)

    def heartbeat(self) -> dict:
        return {
            "enabled": self.runtime.enabled,
            "fatal_error": self.runtime.fatal_error,
            "open_positions": len(self.runtime.open_positions),
            "daily_trades": self.runtime.daily_trades,
            "daily_loss_krw": self.runtime.daily_loss_krw,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def tick(self) -> None:
        self._reload_config()
        if not self.runtime.enabled:
            return

        status = get_market_status()
        if not status.can_place_order:
            logger.info("Order blocked: %s", status.reason)

        if self.runtime.cooldown_until_epoch > time.time():
            return

        risk_ok, risk_reason = self.risk_check_detail()
        if not risk_ok:
            logger.warning("Risk limit reached, trading paused. %s", risk_reason)
            return

        try:
            quotes = self.kis.fetch_universe_quotes()
            for q in quotes:
                result = self.strategy.evaluate(q)
                self.db.insert_signal(
                    q.symbol,
                    result.total_score,
                    json.dumps(result.stage_scores, ensure_ascii=False),
                    "PASS" if result.passed else "FAIL",
                    result.reason,
                )
                if result.passed and status.can_place_order:
                    self._try_entry(q.symbol, q.price, result.reason)

            self._manage_positions(quotes)
        except Exception as exc:
            self.runtime.fatal_error = str(exc)
            self.runtime.enabled = False
            self.notifier.send(f"[치명오류] 자동매매 중지: {exc}")
            logger.exception("Fatal engine error")

    def risk_check_detail(self) -> tuple[bool, str]:
        risk = self.config["risk_limits"]
        max_orders_per_day = int(risk.get("max_orders_per_day", risk.get("max_daily_trades", 0)))
        if max_orders_per_day > 0 and self.runtime.daily_trades >= max_orders_per_day:
            return False, f"일 주문횟수 제한 도달({self.runtime.daily_trades}/{max_orders_per_day})"

        max_daily_loss_krw = float(risk.get("max_daily_loss_krw", 0))
        if max_daily_loss_krw > 0 and self.runtime.daily_loss_krw <= -max_daily_loss_krw:
            return False, f"일 손실한도 초과({self.runtime.daily_loss_krw:,.0f}원)"

        max_daily_loss_pct = float(risk.get("max_daily_loss_pct", 0))
        if max_daily_loss_pct > 0:
            equity = float(os.getenv("AUTOTRADE_EQUITY_BASE_KRW", str(risk.get("equity_base_krw", 0))))
            if equity > 0:
                loss_pct = abs(self.runtime.daily_loss_krw) / equity * 100
                if loss_pct >= max_daily_loss_pct:
                    return False, f"일 손실률 제한 초과({loss_pct:.2f}%/{max_daily_loss_pct}%)"

        if self.runtime.cooldown_until_epoch > time.time():
            remain = int(self.runtime.cooldown_until_epoch - time.time())
            return False, f"연속손실 쿨다운({remain}초 남음)"

        return True, "정상"

    def run_manual_diagnosis(self) -> dict[str, Any]:
        """Manual check: identify blockers and stage-by-stage pass/fail per symbol."""
        self._reload_config()
        market = get_market_status()
        risk_ok, risk_reason = self.risk_check_detail()

        env_check = {
            "mode": self.config.get("mode", "DRY-RUN"),
            "kis_appkey": bool(os.getenv("KIS_APPKEY")),
            "kis_appsecret": bool(os.getenv("KIS_APPSECRET")),
            "kis_account_no": bool(os.getenv("KIS_ACCOUNT_NO")),
        }

        if env_check["mode"] == "LIVE" and not all([env_check["kis_appkey"], env_check["kis_appsecret"], env_check["kis_account_no"]]):
            env_reason = "LIVE 필수 환경변수 누락"
        else:
            env_reason = "정상"

        rows: list[dict[str, Any]] = []
        try:
            quotes = self.kis.fetch_universe_quotes()
            for q in quotes:
                result = self.strategy.evaluate(q)
                row: dict[str, Any] = {
                    "symbol": q.symbol,
                    "price": round(q.price, 2),
                    "total_score": result.total_score,
                    "strategy_pass": result.passed,
                    "strategy_reason": result.reason,
                }
                for stage, info in result.stage_checks.items():
                    row[f"{stage}_pass"] = info["passed"]
                    row[f"{stage}_reason"] = info["reason"]
                row["can_auto_order_now"] = bool(result.passed and market.can_place_order and risk_ok and env_reason == "정상")
                if not row["can_auto_order_now"]:
                    blockers = []
                    if not result.passed:
                        blockers.append("전략미통과")
                    if not market.can_place_order:
                        blockers.append(f"시장:{market.reason}")
                    if not risk_ok:
                        blockers.append(f"리스크:{risk_reason}")
                    if env_reason != "정상":
                        blockers.append(env_reason)
                    row["blocker"] = " | ".join(blockers)
                else:
                    row["blocker"] = "없음"
                rows.append(row)
        except Exception as exc:
            return {
                "market": market,
                "risk_ok": risk_ok,
                "risk_reason": risk_reason,
                "env_check": env_check,
                "env_reason": env_reason,
                "error": str(exc),
                "rows": rows,
            }

        return {
            "market": market,
            "risk_ok": risk_ok,
            "risk_reason": risk_reason,
            "env_check": env_check,
            "env_reason": env_reason,
            "error": None,
            "rows": rows,
        }

    def manual_place_order(self, symbol: str, qty: int, side: str, price: float) -> dict[str, Any]:
        """Manual order endpoint for operator-driven smoke checks from UI."""
        self._reload_config()
        order = self.kis.place_order(symbol=symbol, qty=qty, side=side, price=price)
        logger.info("manual order result=%s", order)
        return order

    def _try_entry(self, symbol: str, price: float, reason: str) -> None:
        if symbol in self.runtime.open_positions:
            return
        risk = self.config["risk_limits"]
        if len(self.runtime.open_positions) >= risk["max_positions"]:
            return
        budget = risk["max_buy_amount_per_trade_krw"]
        qty = int(budget // price)
        if qty <= 0:
            return

        order = self.kis.place_order(symbol=symbol, qty=qty, side="BUY", price=price)
        if order.get("status") in {"SIMULATED", "FILLED"}:
            trade_id = self.db.open_trade(symbol, qty, price, reason)
            self.runtime.open_positions[symbol] = {"trade_id": trade_id, "entry_price": price, "qty": qty}
            self.runtime.daily_trades += 1
            self.notifier.send(f"[진입] {symbol} {qty}주 @ {price:,.0f}")

    def _manage_positions(self, quotes) -> None:
        exit_cfg = self.config["stages"]["exit"]
        for q in quotes:
            if q.symbol not in self.runtime.open_positions:
                continue
            pos = self.runtime.open_positions[q.symbol]
            change_pct = (q.price / pos["entry_price"] - 1) * 100
            should_exit = change_pct <= -exit_cfg["stop_loss_pct"] or change_pct >= exit_cfg["take_profit_pct"]
            if should_exit:
                order = self.kis.place_order(symbol=q.symbol, qty=pos["qty"], side="SELL", price=q.price)
                if order.get("status") in {"SIMULATED", "FILLED"}:
                    self.db.close_trade(pos["trade_id"], q.price, fees=500, reason_exit="auto_exit")
                    pnl = (q.price - pos["entry_price"]) * pos["qty"] - 500
                    self.runtime.daily_loss_krw += min(0, pnl)
                    self.runtime.consecutive_losses = self.runtime.consecutive_losses + 1 if pnl < 0 else 0
                    if self.runtime.consecutive_losses >= self.config["risk_limits"]["cooldown_after_consecutive_losses"]:
                        self.runtime.cooldown_until_epoch = time.time() + self.config["risk_limits"]["cooldown_minutes"] * 60
                    del self.runtime.open_positions[q.symbol]
                    self.notifier.send(f"[청산] {q.symbol} 손익 {pnl:,.0f}원")
