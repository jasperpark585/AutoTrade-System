from __future__ import annotations

import copy
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
from app.services.kis_client import KISClient, Quote
from app.services.news_client import NewsClient
from app.services.portfolio_service import PortfolioService
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
    portfolio_cache: dict[str, Any] = field(default_factory=dict)
    portfolio_cache_ts: float = 0.0
    current_profile: str = "base"
    recent_blockers: list[dict[str, Any]] = field(default_factory=list)


class AutoTradingEngine:
    def __init__(self, config_manager: ConfigManager, db: Database, notifier: KakaoNotifier):
        self.cfg_mgr = config_manager
        self.db = db
        self.runtime = EngineRuntime()
        self.notifier = notifier
        self.portfolio_service = PortfolioService()
        self.news_client: NewsClient | None = None
        self.base_config: dict[str, Any] = {}
        self._reload_config()

    def _reload_config(self) -> None:
        self.base_config = self.cfg_mgr.load()
        self.config = copy.deepcopy(self.base_config)
        self.kis = KISClient(dry_run=self.config.get("mode", "DRY-RUN") == "DRY-RUN")
        self.news_client = NewsClient(self.config)
        self.strategy = StageStrategy(self.config)

    def _sync_enabled_from_db(self) -> None:
        val = self.db.get_engine_state("auto_trading_enabled")
        if val is not None:
            self.runtime.enabled = val.lower() in {"1", "true", "yes", "on"}

    def set_auto_trading_enabled(self, is_on: bool) -> None:
        self.runtime.enabled = is_on
        self.db.set_engine_state("auto_trading_enabled", "true" if is_on else "false")
        logger.info("event=AUTO_TRADE_TOGGLE enabled=%s", is_on)

    def get_auto_trading_enabled(self) -> bool:
        self._sync_enabled_from_db()
        return self.runtime.enabled

    def heartbeat(self) -> dict:
        return {
            "enabled": self.get_auto_trading_enabled(),
            "fatal_error": self.runtime.fatal_error,
            "open_positions": len(self.runtime.open_positions),
            "daily_trades": self.runtime.daily_trades,
            "daily_loss_krw": self.runtime.daily_loss_krw,
            "current_profile": self.runtime.current_profile,
            "recent_blockers": self.runtime.recent_blockers[-10:],
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(base)
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = self._deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out

    def _apply_profile_by_cash(self, orderable_cash_krw: float) -> None:
        cfg = copy.deepcopy(self.base_config)
        sp = cfg.get("small_cash_profile", {})
        enabled = bool(sp.get("enabled", False))
        force_small = bool(sp.get("force_enabled", False))
        auto_cfg = sp.get("auto_switch", {})
        auto_on = bool(auto_cfg.get("enabled", False))
        threshold = float(auto_cfg.get("threshold_orderable_cash_krw", 300000))

        profile = "base"
        if enabled and (force_small or (auto_on and orderable_cash_krw < threshold)):
            cfg = self._deep_merge(cfg, sp.get("overrides", {}))
            profile = "small_cash"

        if profile != self.runtime.current_profile:
            logger.info(
                "event=PROFILE_SWITCH profile=%s orderable_cash=%s threshold=%s",
                profile,
                int(orderable_cash_krw),
                int(threshold),
            )
            self.runtime.recent_blockers.append(
                {
                    "event": "PROFILE_SWITCH",
                    "profile": profile,
                    "orderable_cash": int(orderable_cash_krw),
                    "threshold": int(threshold),
                    "ts": datetime.utcnow().isoformat(),
                }
            )
        self.runtime.current_profile = profile
        self.config = cfg
        self.strategy = StageStrategy(self.config)

    def tick(self) -> None:
        self._reload_config()
        self._sync_enabled_from_db()
        if not self.runtime.enabled:
            return

        try:
            snap = self.get_portfolio_snapshot(force_refresh=False)
            orderable_cash = float(snap.get("account", {}).get("available_cash", 0) or 0)
            self._apply_profile_by_cash(orderable_cash)
        except Exception as exc:
            logger.warning("event=BUY_BLOCK reason=PORTFOLIO_FETCH_FAIL detail=%s", self.format_exception(exc))

        status = get_market_status()
        if not status.can_place_order:
            return

        risk_ok, risk_reason = self.risk_check_detail()
        if not risk_ok:
            logger.warning("event=BUY_BLOCK reason=RISK detail=%s", risk_reason)
            return

        try:
            quotes, score_map = self._load_universe_quotes()
            candidates = self.build_candidates(quotes, external_score_map=score_map)
            for c in candidates:
                self.db.insert_signal(c["symbol"], c["total_score"], json.dumps(c["stage_scores"], ensure_ascii=False), c["pass_fail"], c["reason"])
            self._attempt_buy_candidates(candidates)
            self._manage_positions(quotes)
        except Exception as exc:
            formatted = self.format_exception(exc)
            self.runtime.fatal_error = formatted
            self.runtime.enabled = False
            self.db.set_engine_state("auto_trading_enabled", "false")
            self.notifier.send(f"[치명오류] 자동매매 중지: {formatted[:280]}")
            logger.exception("Fatal engine error: %s", formatted)

    def _load_universe_quotes(self) -> tuple[list[Quote], dict[str, float]]:
        if self.config.get("news", {}).get("use_news_universe", False) and self.news_client is not None:
            self.news_client.update_candidates(force=False)
            news_candidates = self.news_client.load_candidates()
            symbols = [x.get("symbol", "") for x in news_candidates if x.get("symbol")]
            score_map = {x.get("symbol"): float(x.get("score", 0)) for x in news_candidates}
            return self.kis.fetch_universe_quotes(symbols=symbols), score_map
        return self.kis.fetch_universe_quotes(), {}

    @staticmethod
    def format_exception(exc: Exception) -> str:
        err_type, message, _ = unwrap_exception(exc)
        return f"{err_type}: {message}"

    def risk_check_detail(self) -> tuple[bool, str]:
        risk = self.config["risk_limits"]
        max_orders_per_day = int(risk.get("max_orders_per_day", risk.get("max_daily_trades", 0)))
        if max_orders_per_day > 0 and self.runtime.daily_trades >= max_orders_per_day:
            return False, f"일 주문횟수 제한 도달({self.runtime.daily_trades}/{max_orders_per_day})"
        return True, "정상"

    def build_candidates(self, quotes: list[Quote], external_score_map: dict[str, float] | None = None) -> list[dict[str, Any]]:
        universe = self.config.get("stages", {}).get("universe", {})
        use_allowlist = bool(universe.get("use_allowlist", False))
        allowlist_symbols = set(universe.get("allowlist_symbols", []) or [])
        rows: list[dict[str, Any]] = []
        for q in quotes:
            result: ScoreResult = self.strategy.evaluate(q)
            if use_allowlist and allowlist_symbols and q.symbol not in allowlist_symbols:
                result = ScoreResult(False, result.total_score, result.stage_scores, "allowlist 제외", result.stage_checks)
            ext_score = (external_score_map or {}).get(q.symbol, 0.0)
            rows.append(
                {
                    "symbol": q.symbol,
                    "price": q.price,
                    "total_score": result.total_score + ext_score,
                    "pass_fail": "PASS" if result.passed else "FAIL",
                    "reason": result.reason,
                    "stage_scores": result.stage_scores,
                    "stage_checks": result.stage_checks,
                    "strategy_pass": result.passed,
                    "news_score": ext_score,
                }
            )
        rows.sort(key=lambda x: (x["total_score"], x.get("news_score", 0), -x.get("price", 0)), reverse=True)
        return rows

    def _max_buy_per_trade(self) -> float:
        risk = self.config.get("risk_limits", {})
        return float(risk.get("max_buy_amount_per_trade", risk.get("max_buy_amount_per_trade_krw", 0)))

    def _estimate_cost(self, price: float, qty: int) -> float:
        return price * qty + max(0.0, price * qty * 0.00015)

    def determine_order_qty(self, price: float, available_cash: float) -> int:
        if price <= 0:
            return 0
        max_buy = self._max_buy_per_trade()
        budget = min(available_cash, max_buy if max_buy > 0 else available_cash)
        qty = int(budget // price)
        return max(1, qty)

    def check_affordability(self, price: float, qty: int, available_cash: float) -> tuple[bool, str, dict[str, Any]]:
        max_buy = self._max_buy_per_trade()
        estimated_cost = self._estimate_cost(price, qty)
        detail = {
            "price": price,
            "qty": qty,
            "estimated_cost": estimated_cost,
            "available_cash": available_cash,
            "max_buy_amount_per_trade": max_buy,
        }
        if available_cash <= 0:
            return False, "CASH_ZERO", detail
        if price > available_cash:
            return False, "INSUFFICIENT_CASH_FOR_1_SHARE", detail
        if estimated_cost > available_cash:
            return False, "INSUFFICIENT_CASH", detail
        if max_buy > 0 and estimated_cost > max_buy:
            return False, "MAX_BUY_EXCEEDED", detail
        return True, "OK", detail

    def get_buy_candidates_preview(self, top_n: int = 10) -> list[dict[str, Any]]:
        self._reload_config()
        quotes, score_map = self._load_universe_quotes()
        candidates = self.build_candidates(quotes, external_score_map=score_map)
        try:
            account = self.kis.fetch_account_summary()
            available_cash = float(account.get("available_cash", 0) or 0)
        except Exception:
            available_cash = 0.0
        out: list[dict[str, Any]] = []
        for c in candidates[:top_n]:
            qty = self.determine_order_qty(c["price"], available_cash)
            ok, reason, detail = self.check_affordability(c["price"], qty, available_cash)
            out.append(
                {
                    "symbol": c["symbol"],
                    "total_score": c["total_score"],
                    "price_used": c["price"],
                    "qty": qty,
                    "estimated_cost": round(detail["estimated_cost"], 2),
                    "affordable": c["strategy_pass"] and ok,
                    "reason": reason if not ok else "OK",
                }
            )
        return out

    def _attempt_buy_candidates(self, candidates: list[dict[str, Any]]) -> None:
        account = self.kis.fetch_account_summary()
        available_cash = float(account.get("available_cash", 0) or 0)
        if available_cash <= 0:
            logger.warning("event=BUY_SKIP reason=CASH_ZERO orderable_cash=0 candidates=%s", len(candidates))
            return

        for c in candidates:
            if c["pass_fail"] != "PASS" or c["symbol"] in self.runtime.open_positions:
                continue
            qty = self.determine_order_qty(c["price"], available_cash)
            ok, reason, detail = self.check_affordability(c["price"], qty, available_cash)
            if not ok:
                if reason == "INSUFFICIENT_CASH_FOR_1_SHARE":
                    logger.info(
                        "event=BUY_SKIP reason=INSUFFICIENT_CASH_FOR_1_SHARE symbol=%s price=%s orderable_cash=%s",
                        c["symbol"],
                        int(c["price"]),
                        int(available_cash),
                    )
                elif reason == "CASH_ZERO":
                    logger.info("event=BUY_SKIP reason=CASH_ZERO orderable_cash=0 candidates=%s", len(candidates))
                elif reason == "MAX_BUY_EXCEEDED":
                    logger.info(
                        "event=BUY_SKIP reason=QTY_ZERO symbol=%s price=%s max_buy=%s orderable_cash=%s",
                        c["symbol"],
                        int(c["price"]),
                        int(self._max_buy_per_trade()),
                        int(available_cash),
                    )
                self.runtime.recent_blockers.append({"event": "BUY_SKIP", "reason": reason, "symbol": c["symbol"], "ts": datetime.utcnow().isoformat()})
                continue

            try:
                self._try_entry(c["symbol"], c["price"], c["reason"], qty=qty)
                return
            except Exception as exc:
                err_type, _, detail = unwrap_exception(exc)
                if err_type.startswith("RetryError") or (detail and detail.get("next_retry_at")):
                    logger.warning("event=BUY_BLOCK reason=TOKEN_COOLDOWN next_retry_at=%s", detail.get("next_retry_at"))
                    self.runtime.recent_blockers.append({"event": "BUY_BLOCK", "reason": "TOKEN_COOLDOWN", "next_retry_at": detail.get("next_retry_at"), "ts": datetime.utcnow().isoformat()})
                    raise
                continue

    def run_manual_diagnosis(self) -> dict[str, Any]:
        self._reload_config()
        market = get_market_status()
        risk_ok, risk_reason = self.risk_check_detail()
        rows: list[dict[str, Any]] = []
        try:
            quotes, score_map = self._load_universe_quotes()
            candidates = self.build_candidates(quotes, external_score_map=score_map)
            rows = [{"symbol": c["symbol"], "total_score": c["total_score"], "strategy_pass": c["strategy_pass"], "blocker": "없음" if c["strategy_pass"] else "전략미통과"} for c in candidates]
        except Exception as exc:
            err_type, message, detail = unwrap_exception(exc)
            return {"market": market, "risk_ok": risk_ok, "risk_reason": risk_reason, "error": f"{err_type}: {message}", "error_detail": detail, "rows": rows}
        return {"market": market, "risk_ok": risk_ok, "risk_reason": risk_reason, "error": None, "error_detail": {}, "rows": rows}

    def get_portfolio_snapshot(self, force_refresh: bool = False) -> dict[str, Any]:
        self._reload_config()
        if not force_refresh:
            cached = self.portfolio_service.get_cached()
            if cached:
                return cached
        account = self.kis.fetch_account_summary()
        positions = self.kis.fetch_positions()
        orders = self.kis.fetch_recent_orders(limit=20)
        snap = {"account": account, "positions": positions, "orders": orders, "token_status": self.kis.get_token_status(), "ts": datetime.utcnow().isoformat()}
        self.portfolio_service.set_cached(snap)
        return snap

    def get_news_status(self) -> dict[str, Any]:
        self._reload_config()
        if self.news_client is None:
            return {"enabled": False}
        return {"enabled": True, "provider": self.news_client.cfg.mode, "state": self.news_client.load_state(), "candidate_count": len(self.news_client.load_candidates()), "use_news_universe": bool(self.config.get("news", {}).get("use_news_universe", False))}

    def refresh_news_candidates(self, force: bool = True) -> dict[str, Any]:
        self._reload_config()
        if self.news_client is None:
            return {"updated": False, "reason": "NEWS_CLIENT_DISABLED"}
        return self.news_client.update_candidates(force=force)

    def precheck_manual_buy(self, price: float, qty: int) -> dict[str, Any]:
        self._reload_config()
        account = self.kis.fetch_account_summary()
        available_cash = float(account.get("available_cash", 0) or 0)
        ok, reason, detail = self.check_affordability(price, qty, available_cash)
        return {"ok": ok, "reason": reason, **detail}

    def manual_place_order(self, symbol: str, qty: int, side: str, price: float) -> dict[str, Any]:
        self._reload_config()
        return self.kis.place_order(symbol=symbol, qty=qty, side=side, price=price)

    def _try_entry(self, symbol: str, price: float, reason: str, qty: int | None = None) -> None:
        if symbol in self.runtime.open_positions:
            return
        if qty is None:
            qty = 1
        order = self.kis.place_order(symbol=symbol, qty=qty, side="BUY", price=price)
        if order.get("status") in {"SIMULATED", "FILLED", "ACCEPTED"}:
            trade_id = self.db.open_trade(symbol, qty, price, reason)
            self.runtime.open_positions[symbol] = {"trade_id": trade_id, "entry_price": price, "qty": qty}
            self.runtime.daily_trades += 1

    def _manage_positions(self, quotes) -> None:
        if "stages" not in self.config or "exit" not in self.config["stages"]:
            return
        exit_cfg = self.config["stages"]["exit"]
        for q in quotes:
            if q.symbol not in self.runtime.open_positions:
                continue
            pos = self.runtime.open_positions[q.symbol]
            change_pct = (q.price / pos["entry_price"] - 1) * 100
            should_exit = change_pct <= -exit_cfg.get("stop_loss_pct", 1.8) or change_pct >= exit_cfg.get("take_profit_pct", 4.2)
            if should_exit:
                order = self.kis.place_order(symbol=q.symbol, qty=pos["qty"], side="SELL", price=q.price)
                if order.get("status") in {"SIMULATED", "FILLED", "ACCEPTED"}:
                    self.db.close_trade(pos["trade_id"], q.price, fees=500, reason_exit="auto_exit")
                    del self.runtime.open_positions[q.symbol]
