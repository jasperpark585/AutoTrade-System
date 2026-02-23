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
from app.core.strategy import ScoreResult, StageStrategy
from app.services.kakao import KakaoNotifier
from app.services.kis_client import KISClient, KISError, Quote
from app.utils.errors import unwrap_exception

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
            candidates = self.build_candidates(quotes)

            for c in candidates:
                self.db.insert_signal(
                    c["symbol"],
                    c["total_score"],
                    json.dumps(c["stage_scores"], ensure_ascii=False),
                    c["pass_fail"],
                    c["reason"],
                )

            if status.can_place_order:
                self._attempt_buy_candidates(candidates)

            self._manage_positions(quotes)
        except Exception as exc:
            formatted = self.format_exception(exc)
            self.runtime.fatal_error = formatted
            self.runtime.enabled = False
            self.notifier.send(f"[치명오류] 자동매매 중지: {formatted[:280]}")
            logger.exception("Fatal engine error: %s", formatted)

    @staticmethod
    def format_exception(exc: Exception) -> str:
        err_type, message, _ = unwrap_exception(exc)
        return f"{err_type}: {message}"

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

    def build_candidates(self, quotes: list[Quote]) -> list[dict[str, Any]]:
        universe = self.config.get("stages", {}).get("universe", {})
        use_allowlist = bool(universe.get("use_allowlist", False))
        allowlist_symbols = set(universe.get("allowlist_symbols", []) or [])

        rows: list[dict[str, Any]] = []
        for q in quotes:
            result: ScoreResult = self.strategy.evaluate(q)
            if use_allowlist and allowlist_symbols and q.symbol not in allowlist_symbols:
                result = ScoreResult(
                    passed=False,
                    total_score=result.total_score,
                    stage_scores=result.stage_scores,
                    reason="allowlist 제외",
                    stage_checks=result.stage_checks,
                )

            row: dict[str, Any] = {
                "symbol": q.symbol,
                "price": q.price,
                "total_score": result.total_score,
                "pass_fail": "PASS" if result.passed else "FAIL",
                "reason": result.reason,
                "stage_scores": result.stage_scores,
                "stage_checks": result.stage_checks,
                "strategy_pass": result.passed,
            }
            rows.append(row)

        rows.sort(key=lambda x: x["total_score"], reverse=True)
        return rows

    def get_buy_candidates_preview(self, top_n: int = 10) -> list[dict[str, Any]]:
        self._reload_config()
        quotes = self.kis.fetch_universe_quotes()
        candidates = self.build_candidates(quotes)

        try:
            account = self.kis.get_account_summary()
            available_cash = float(account.get("available_cash", 0) or 0)
        except Exception:
            available_cash = 0.0

        max_buy = float(self.config["risk_limits"].get("max_buy_amount_per_trade_krw", 0))
        out: list[dict[str, Any]] = []
        for c in candidates[:top_n]:
            qty = 1
            estimated_fees = max(0.0, c["price"] * qty * 0.00015)
            estimated_cost = c["price"] * qty + estimated_fees
            affordable = c["strategy_pass"] and estimated_cost <= available_cash and estimated_cost <= max_buy
            skip_reason = ""
            if not c["strategy_pass"]:
                skip_reason = "STRATEGY_FAIL"
            elif estimated_cost > available_cash:
                skip_reason = "INSUFFICIENT_CASH"
            elif estimated_cost > max_buy:
                skip_reason = "RISK_BLOCK"
            out.append(
                {
                    "symbol": c["symbol"],
                    "total_score": c["total_score"],
                    "stage_summary": ", ".join([f"{k}:{'P' if v['passed'] else 'F'}" for k, v in c["stage_checks"].items()]),
                    "price": c["price"],
                    "estimated_cost": round(estimated_cost, 2),
                    "affordable": affordable,
                    "skip_reason": skip_reason,
                }
            )
        return out

    def _attempt_buy_candidates(self, candidates: list[dict[str, Any]]) -> None:
        account = self.kis.get_account_summary()
        available_cash = float(account.get("available_cash", 0) or 0)
        max_buy = float(self.config["risk_limits"].get("max_buy_amount_per_trade_krw", 0))

        for c in candidates:
            if c["pass_fail"] != "PASS":
                continue
            if c["symbol"] in self.runtime.open_positions:
                continue
            qty = 1
            estimated_fees = max(0.0, c["price"] * qty * 0.00015)
            estimated_cost = c["price"] * qty + estimated_fees

            if estimated_cost > available_cash or estimated_cost > max_buy:
                reason_detail = {
                    "symbol": c["symbol"],
                    "price": c["price"],
                    "qty": qty,
                    "estimated_cost": estimated_cost,
                    "available_cash": available_cash,
                    "max_buy_amount_per_trade": max_buy,
                }
                logger.info("blocker=INSUFFICIENT_CASH detail=%s", reason_detail)
                continue

            try:
                self._try_entry(c["symbol"], c["price"], c["reason"], qty=qty)
                # one successful/accepted order per tick
                return
            except Exception as exc:
                err_type, _, detail = unwrap_exception(exc)
                logger.warning("candidate order failed symbol=%s err_type=%s detail=%s", c["symbol"], err_type, detail)
                if err_type.startswith("RetryError") or (detail and (detail.get("http_status") in {401, 403, 429, 500, 503})):
                    logger.error("blocker=API_ERROR stop candidate loop")
                    raise
                continue

    def run_manual_diagnosis(self) -> dict[str, Any]:
        self._reload_config()
        market = get_market_status()
        risk_ok, risk_reason = self.risk_check_detail()

        env_check = {
            "mode": self.config.get("mode", "DRY-RUN"),
            "kis_appkey": bool(os.getenv("KIS_APPKEY")),
            "kis_appsecret": bool(os.getenv("KIS_APPSECRET")),
            "kis_account_no": bool(os.getenv("KIS_ACCOUNT_NO")),
        }

        env_reason = "정상"
        if env_check["mode"] == "LIVE" and not all([env_check["kis_appkey"], env_check["kis_appsecret"], env_check["kis_account_no"]]):
            env_reason = "LIVE 필수 환경변수 누락"

        rows: list[dict[str, Any]] = []
        try:
            quotes = self.kis.fetch_universe_quotes()
            candidates = self.build_candidates(quotes)
            for c in candidates:
                row: dict[str, Any] = {
                    "symbol": c["symbol"],
                    "price": round(c["price"], 2),
                    "total_score": c["total_score"],
                    "strategy_pass": c["strategy_pass"],
                    "strategy_reason": c["reason"],
                }
                for stage, info in c["stage_checks"].items():
                    row[f"{stage}_pass"] = info["passed"]
                    row[f"{stage}_reason"] = info["reason"]
                row["can_auto_order_now"] = bool(c["strategy_pass"] and market.can_place_order and risk_ok and env_reason == "정상")
                if not row["can_auto_order_now"]:
                    blockers = []
                    if not c["strategy_pass"]:
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
            err_type, message, detail = unwrap_exception(exc)
            return {
                "market": market,
                "risk_ok": risk_ok,
                "risk_reason": risk_reason,
                "env_check": env_check,
                "env_reason": env_reason,
                "error": f"{err_type}: {message}",
                "error_type": err_type,
                "error_detail": detail,
                "rows": rows,
            }

        return {
            "market": market,
            "risk_ok": risk_ok,
            "risk_reason": risk_reason,
            "env_check": env_check,
            "env_reason": env_reason,
            "error": None,
            "error_type": None,
            "error_detail": {},
            "rows": rows,
        }

    def get_portfolio_snapshot(self) -> dict[str, Any]:
        self._reload_config()
        summary = self.kis.get_account_summary()
        positions = self.kis.get_positions()
        return {"summary": summary, "positions": positions, "ts": datetime.utcnow().isoformat()}

    def manual_place_order(self, symbol: str, qty: int, side: str, price: float) -> dict[str, Any]:
        self._reload_config()
        order = self.kis.place_order(symbol=symbol, qty=qty, side=side, price=price)
        logger.info("manual order result=%s", order)
        return order

    def _try_entry(self, symbol: str, price: float, reason: str, qty: int | None = None) -> None:
        if symbol in self.runtime.open_positions:
            return
        risk = self.config["risk_limits"]
        if len(self.runtime.open_positions) >= risk["max_positions"]:
            return
        if qty is None:
            budget = risk["max_buy_amount_per_trade_krw"]
            qty = int(budget // price)
        if qty <= 0:
            return

        order = self.kis.place_order(symbol=symbol, qty=qty, side="BUY", price=price)
        if order.get("status") in {"SIMULATED", "FILLED", "ACCEPTED"}:
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
                if order.get("status") in {"SIMULATED", "FILLED", "ACCEPTED"}:
                    self.db.close_trade(pos["trade_id"], q.price, fees=500, reason_exit="auto_exit")
                    pnl = (q.price - pos["entry_price"]) * pos["qty"] - 500
                    self.runtime.daily_loss_krw += min(0, pnl)
                    self.runtime.consecutive_losses = self.runtime.consecutive_losses + 1 if pnl < 0 else 0
                    if self.runtime.consecutive_losses >= self.config["risk_limits"]["cooldown_after_consecutive_losses"]:
                        self.runtime.cooldown_until_epoch = time.time() + self.config["risk_limits"]["cooldown_minutes"] * 60
                    del self.runtime.open_positions[q.symbol]
                    self.notifier.send(f"[청산] {q.symbol} 손익 {pnl:,.0f}원")
