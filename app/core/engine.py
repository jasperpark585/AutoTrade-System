from __future__ import annotations

import copy
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import ConfigManager
from app.core.database import Database
from app.core.market_hours import MarketStatus, get_market_status
from app.core.risk import RiskGuard
from app.core.strategy import ScoreResult, StageStrategy
from app.services.gpt_scout import GPTScout
from app.services.kakao import KakaoNotifier
from app.services.kis_client import KISClient, KISCooldownError, KISError, Quote
from app.services.portfolio_service import PortfolioService
from app.utils.errors import unwrap_exception

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


@dataclass
class EngineRuntime:
    enabled: bool = False
    open_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    daily_trades: int = 0
    daily_loss_krw: float = 0.0
    fatal_error: str | None = None
    current_profile: str = "base"
    blocker: str = ""
    blocker_next_retry_at: str | None = None
    orderable_cash: float = 0.0
    available_cash: float = 0.0
    d2_cash: float = 0.0
    orderable_cash_source: str = "unknown"
    orderable_cash_stale: bool = True
    orderable_cash_last_updated_at: str | None = None
    snapshot_warning: str = ""
    candidates_count: int = 0
    recent_blockers: list[dict[str, Any]] = field(default_factory=list)
    last_portfolio_refresh_epoch: float = 0.0
    watchlist_symbols: list[str] = field(default_factory=list)
    watchlist_source: str = "default"
    watchlist_updated_at: str | None = None
    watchlist_date_kst: str | None = None
    ai_candidates: list[dict[str, Any]] = field(default_factory=list)
    openai_guard: dict[str, Any] = field(default_factory=dict)
    last_hourly_alert_at: str | None = None


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str


class AutoTradingEngine:
    def __init__(self, config_manager: ConfigManager, db: Database, notifier: KakaoNotifier):
        self.cfg_mgr = config_manager
        self.db = db
        self.notifier = notifier
        self.runtime = EngineRuntime()
        self.portfolio_service = PortfolioService()
        self.gpt_scout = GPTScout()
        self.base_config: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.runtime_flags: dict[str, Any] = {
            "mode": "DRY-RUN",
            "explicit_live": False,
            "env_dry_run": True,
            "dry_run": True,
            "mock_order": False,
            "live_order_enabled": False,
        }
        self._reload_config()
        self._check_runtime_writable_paths()
        self._restore_last_good_orderable(source="cached")
        self._load_watchlist_from_storage()

    def _check_runtime_writable_paths(self) -> None:
        targets = [Path("data"), Path("logs"), Path("strategy.yaml")]
        for target in targets:
            try:
                parent = target if target.is_dir() else target.parent
                parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                logger.warning("event=PERMISSION_WARN path=%s error=%s", target, exc)

    def _default_runtime_flags(self, config: dict[str, Any]) -> dict[str, Any]:
        mode = str(config.get("mode", "DRY-RUN")).upper()
        return {
            "mode": mode,
            "explicit_live": False,
            "env_dry_run": mode != "LIVE",
            "dry_run": mode != "LIVE",
            "mock_order": False,
            "live_order_enabled": False,
        }

    def _reload_config(self) -> None:
        self.base_config = self.cfg_mgr.load()
        self.config = copy.deepcopy(self.base_config)
        if hasattr(self.cfg_mgr, "runtime_flags"):
            flags = self.cfg_mgr.runtime_flags(self.config)
        else:
            flags = self._default_runtime_flags(self.config)
        self.runtime_flags = flags

        if not hasattr(self, "kis"):
            self.kis = KISClient(dry_run=bool(flags.get("dry_run", True)))
        self.kis.update_runtime_flags(
            dry_run=bool(flags.get("dry_run", True)),
            mock_live_order=bool(flags.get("mock_order", False)),
            explicit_live=bool(flags.get("explicit_live", False)),
            force_dry_run=bool(flags.get("env_dry_run", True)),
        )
        self.strategy = StageStrategy(self.config)
        self.risk = RiskGuard(self.config)

    def _sync_enabled_from_db(self) -> None:
        val = self.db.get_engine_state("auto_trading_enabled")
        if val is not None:
            self.runtime.enabled = val.lower() in {"1", "true", "yes", "on"}

    def enable(self, is_on: bool) -> None:
        self.set_auto_trading_enabled(is_on)

    def set_auto_trading_enabled(self, is_on: bool) -> None:
        self.runtime.enabled = is_on
        self.db.set_engine_state("auto_trading_enabled", "true" if is_on else "false")

    def get_auto_trading_enabled(self) -> bool:
        self._sync_enabled_from_db()
        return self.runtime.enabled

    def _record_blocker(self, reason: str, next_retry_at: str | None = None) -> None:
        self.runtime.blocker = reason
        self.runtime.blocker_next_retry_at = next_retry_at
        event = {
            "event": "BLOCKER",
            "reason": reason,
            "next_retry_at": next_retry_at,
            "ts": datetime.utcnow().isoformat(),
        }
        self.runtime.recent_blockers.append(event)
        self.runtime.recent_blockers = self.runtime.recent_blockers[-30:]

    def _clear_blocker(self) -> None:
        self.runtime.blocker = ""
        self.runtime.blocker_next_retry_at = None

    @staticmethod
    def _parse_iso_utc(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _is_cooldown_error(self, exc: Exception) -> tuple[bool, str | None]:
        if isinstance(exc, KISCooldownError):
            detail = exc.detail or {}
            return True, str(detail.get("next_retry_at") or "") or None
        if not isinstance(exc, KISError):
            return False, None
        detail = exc.detail or {}
        reason = str(detail.get("reason", "")).upper()
        if reason == "KIS_TOKEN_COOLDOWN":
            return True, str(detail.get("next_retry_at") or "") or None
        if detail.get("next_retry_at"):
            return True, str(detail.get("next_retry_at"))
        if int(detail.get("http_status", 0) or 0) == 403:
            return True, None
        lowered = f"{str(exc)} {detail.get('msg1', '')}".lower()
        if any(token in lowered for token in ("cooldown", "temporarily unavailable", "egw00133")):
            return True, None
        return False, None

    @staticmethod
    def _money_to_float(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).replace(",", "").strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _compute_orderable_cash(self, account: dict[str, Any]) -> tuple[float, str]:
        raw = account.get("raw_summary", {}) if isinstance(account.get("raw_summary"), dict) else {}
        candidates: list[tuple[str, Any]] = [
            ("ord_psbl_cash", account.get("ord_psbl_cash", raw.get("ord_psbl_cash"))),
            ("available_cash", account.get("available_cash")),
            ("raw_summary.dnca_tot_amt", raw.get("dnca_tot_amt")),
            ("cash", account.get("cash")),
        ]
        for source, value in candidates:
            amount = self._money_to_float(value)
            if amount > 0:
                return amount, source
        return 0.0, "none"

    def _restore_last_good_orderable(self, source: str = "cached") -> None:
        last_good = self.db.get_engine_state_float("last_good_orderable_cash", default=0.0)
        if last_good > 0:
            self.runtime.orderable_cash = last_good
            self.runtime.available_cash = max(self.runtime.available_cash, last_good)
            self.runtime.orderable_cash_source = source
            self.runtime.orderable_cash_stale = True
        self.runtime.orderable_cash_last_updated_at = self.db.get_engine_state("last_good_orderable_at")

    def _save_last_good_orderable(self, value: float) -> None:
        self.db.set_engine_state_float("last_good_orderable_cash", value)
        now_iso = datetime.utcnow().isoformat()
        self.db.set_engine_state("last_good_orderable_at", now_iso)
        self.runtime.orderable_cash_last_updated_at = now_iso

    def _normalize_symbols(self, symbols: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            clean = "".join(ch for ch in str(symbol).strip() if ch.isdigit())
            if not clean:
                continue
            normalized = clean.zfill(6)[-6:]
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    def _default_watchlist_symbols(self) -> list[str]:
        universe_cfg = self.config.get("universe", {})
        from_cfg = universe_cfg.get("default_symbols") if isinstance(universe_cfg, dict) else None
        if isinstance(from_cfg, list) and from_cfg:
            normalized = self._normalize_symbols([str(x) for x in from_cfg])
            if normalized:
                return normalized
        env_symbols = os.getenv("KIS_SYMBOLS", "005930,000660,035420,251270,068270,207940")
        return self._normalize_symbols([x.strip() for x in env_symbols.split(",") if x.strip()])

    def _update_watchlist_from_payload(self, payload: dict[str, Any], source: str | None = None) -> None:
        symbols = payload.get("symbols", []) if isinstance(payload.get("symbols"), list) else []
        normalized = self._normalize_symbols([str(x) for x in symbols])
        if not normalized:
            normalized = self._default_watchlist_symbols()
        self.runtime.watchlist_symbols = normalized
        self.runtime.watchlist_source = source or str(payload.get("source") or "default")
        self.runtime.watchlist_updated_at = str(payload.get("generated_at") or payload.get("updated_at") or datetime.utcnow().isoformat())
        self.runtime.watchlist_date_kst = str(payload.get("date_kst") or datetime.now(KST).date().isoformat())
        candidates = payload.get("candidates", []) if isinstance(payload.get("candidates"), list) else []
        self.runtime.ai_candidates = [row for row in candidates if isinstance(row, dict)]
        guard = payload.get("openai_guard", {}) if isinstance(payload.get("openai_guard"), dict) else {}
        self.runtime.openai_guard = guard

    def _load_watchlist_from_storage(self) -> None:
        raw = self.db.get_engine_state("daily_watchlist_payload")
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                self._update_watchlist_from_payload(payload)
                if self.runtime.watchlist_symbols:
                    return

        cached = self.gpt_scout.get_cached_payload()
        if isinstance(cached, dict):
            self._update_watchlist_from_payload(cached)
            if self.runtime.watchlist_symbols:
                self.db.set_engine_state("daily_watchlist_payload", json.dumps(cached, ensure_ascii=False))
                return

        self.runtime.watchlist_symbols = self._default_watchlist_symbols()
        self.runtime.watchlist_source = "default"
        self.runtime.watchlist_updated_at = datetime.utcnow().isoformat()
        self.runtime.watchlist_date_kst = datetime.now(KST).date().isoformat()
        self.runtime.ai_candidates = []
        self.runtime.openai_guard = {}

    def _watchlist_refresh_time(self) -> dt_time:
        cfg = self.config.get("gpt_scout", {})
        raw = str(cfg.get("premarket_refresh_time_kst", "08:20"))
        try:
            hour_text, minute_text = raw.split(":", 1)
            return dt_time(hour=int(hour_text), minute=int(minute_text))
        except Exception:
            return dt_time(hour=8, minute=20)

    def get_runtime_mode_flags(self) -> dict[str, Any]:
        return dict(self.runtime_flags)

    def refresh_daily_candidates(self, force: bool = False, trigger: str = "tick") -> dict[str, Any]:
        scout_cfg = self.config.get("gpt_scout", {})
        self.runtime.openai_guard = self.gpt_scout.get_guard_status(scout_cfg)
        enabled = bool(scout_cfg.get("enabled", True))
        defaults = self._default_watchlist_symbols()

        if not enabled:
            if not self.runtime.watchlist_symbols:
                self.runtime.watchlist_symbols = defaults
                self.runtime.watchlist_source = "default"
                self.runtime.watchlist_updated_at = datetime.utcnow().isoformat()
            return {
                "ok": True,
                "reason": "GPT_SCOUT_DISABLED",
                "symbols": self.runtime.watchlist_symbols,
                "source": self.runtime.watchlist_source,
                "openai_guard": self.runtime.openai_guard,
            }

        now_kst = datetime.now(KST)
        today_kst = now_kst.date().isoformat()
        last_date = self.db.get_engine_state("gpt_watchlist_date_kst")
        refresh_time = self._watchlist_refresh_time()
        should_refresh = bool(force or (now_kst.time() >= refresh_time and last_date != today_kst))

        if not should_refresh:
            if not self.runtime.watchlist_symbols:
                self._load_watchlist_from_storage()
            return {
                "ok": True,
                "reason": "SKIP_ALREADY_REFRESHED",
                "symbols": self.runtime.watchlist_symbols,
                "source": self.runtime.watchlist_source,
                "date_kst": self.runtime.watchlist_date_kst,
                "openai_guard": self.runtime.openai_guard,
            }

        openai_cfg = dict(scout_cfg)
        buy_budget = self._candidate_buy_budget_krw()
        if buy_budget > 0:
            # Guide GPT to return symbols that can be bought with current account budget.
            openai_cfg["affordable_price_cap_krw"] = round(float(buy_budget), 2)
        allow_external_call = bool(scout_cfg.get("allow_external_call", False))
        if allow_external_call:
            openai_cfg["api_key"] = str(openai_cfg.get("api_key") or os.getenv("OPENAI_API_KEY", "")).strip()
        else:
            openai_cfg["api_key"] = ""
        try:
            payload = self.gpt_scout.refresh_daily_candidates(openai_cfg, fallback_symbols=defaults)
            self._update_watchlist_from_payload(payload)
            self.db.set_engine_state("daily_watchlist_payload", json.dumps(payload, ensure_ascii=False))
            self.db.set_engine_state("gpt_watchlist_date_kst", str(payload.get("date_kst", today_kst)))
            self.db.set_engine_state("gpt_watchlist_last_source", str(payload.get("source", "unknown")))
            if bool(scout_cfg.get("notify_on_refresh", True)):
                top_symbols = ", ".join(self.runtime.watchlist_symbols[:5]) or "-"
                self.notifier.send(f"[\uc7a5\uc804 \ud6c4\ubcf4 \ub4f1\ub85d] {payload.get('date_kst')} {top_symbols}")
            return {
                "ok": True,
                "reason": "REFRESHED",
                "source": payload.get("source"),
                "symbols": self.runtime.watchlist_symbols,
                "candidates": self.runtime.ai_candidates,
                "date_kst": payload.get("date_kst"),
                "openai_guard": self.runtime.openai_guard,
            }
        except Exception as exc:
            logger.warning("daily candidate refresh failed trigger=%s error=%s", trigger, exc)
            if not self.runtime.watchlist_symbols:
                self.runtime.watchlist_symbols = defaults
                self.runtime.watchlist_source = "default_on_error"
                self.runtime.watchlist_updated_at = datetime.utcnow().isoformat()
                self.runtime.watchlist_date_kst = today_kst
            return {
                "ok": False,
                "reason": self.format_exception(exc),
                "symbols": self.runtime.watchlist_symbols,
                "source": self.runtime.watchlist_source,
                "date_kst": self.runtime.watchlist_date_kst,
                "openai_guard": self.runtime.openai_guard,
            }

    def _resolve_watchlist_symbols(self) -> list[str]:
        if self.runtime.watchlist_symbols:
            return self.runtime.watchlist_symbols
        symbols = self._default_watchlist_symbols()
        self.runtime.watchlist_symbols = symbols
        self.runtime.watchlist_source = "default"
        return symbols

    def heartbeat(self) -> dict[str, Any]:
        flags = self.get_runtime_mode_flags()
        return {
            "enabled": self.get_auto_trading_enabled(),
            "fatal_error": self.runtime.fatal_error,
            "blocker": self.runtime.blocker,
            "next_retry_at": self.runtime.blocker_next_retry_at,
            "current_profile": self.runtime.current_profile,
            "orderable_cash": self.runtime.orderable_cash,
            "available_cash": self.runtime.available_cash,
            "d2_cash": self.runtime.d2_cash,
            "orderable_cash_source": self.runtime.orderable_cash_source,
            "orderable_cash_stale": self.runtime.orderable_cash_stale,
            "orderable_cash_last_updated_at": self.runtime.orderable_cash_last_updated_at,
            "snapshot_warning": self.runtime.snapshot_warning,
            "candidates_count": self.runtime.candidates_count,
            "recent_blockers": self.runtime.recent_blockers[-5:],
            "open_positions": len(self.runtime.open_positions),
            "daily_trades": self.runtime.daily_trades,
            "daily_loss_krw": self.runtime.daily_loss_krw,
            "mode": flags["mode"],
            "dry_run": flags["dry_run"],
            "mock_order": flags["mock_order"],
            "explicit_live": flags.get("explicit_live", False),
            "env_dry_run": flags.get("env_dry_run", True),
            "live_order_enabled": flags["live_order_enabled"],
            "live_block_reasons": flags.get("live_block_reasons", []),
            "watchlist_symbols": self.runtime.watchlist_symbols,
            "watchlist_source": self.runtime.watchlist_source,
            "watchlist_updated_at": self.runtime.watchlist_updated_at,
            "watchlist_date_kst": self.runtime.watchlist_date_kst,
            "ai_candidates": self.runtime.ai_candidates[:10],
            "openai_guard": self.runtime.openai_guard,
            "last_hourly_alert_at": self.runtime.last_hourly_alert_at,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = self._deep_merge(out[key], value)
            else:
                out[key] = copy.deepcopy(value)
        return out

    def _apply_profile_by_cash(self, orderable_cash_krw: float) -> None:
        cfg = copy.deepcopy(self.base_config)
        profile = "base"
        small = cfg.get("small_cash_profile", {})
        auto = small.get("auto_switch", {})
        threshold = float(auto.get("threshold_orderable_cash_krw", 300000))
        if bool(small.get("enabled", True)) and (
            bool(small.get("force_enabled", False))
            or (bool(auto.get("enabled", True)) and orderable_cash_krw < threshold)
        ):
            cfg = self._deep_merge(cfg, small.get("overrides", {}))
            profile = "small_cash"
            risk_cfg = cfg.setdefault("risk_limits", {})
            dynamic_max = min(150000.0, max(20000.0, orderable_cash_krw * 0.4))
            risk_cfg["max_positions"] = min(int(risk_cfg.get("max_positions", 2)), 2)
            risk_cfg["max_buy_amount_per_trade_krw"] = dynamic_max
            risk_cfg["max_buy_amount_per_trade"] = dynamic_max

        self.config = cfg
        self.strategy = StageStrategy(self.config)
        self.risk.update(self.config)
        self.runtime.current_profile = profile

    def tick(self) -> None:
        self._reload_config()
        self.refresh_daily_candidates(force=False, trigger="tick")
        self._sync_enabled_from_db()
        logger.info("tick start enabled=%s blocker=%s", self.runtime.enabled, self.runtime.blocker or "NONE")

        if not self.runtime.enabled:
            logger.info("tick skip reason=DISABLED")
            return

        retry_dt = self._parse_iso_utc(self.runtime.blocker_next_retry_at)
        if retry_dt and datetime.now(timezone.utc) < retry_dt:
            logger.info(
                "tick skip reason=%s next_retry_at=%s",
                self.runtime.blocker,
                self.runtime.blocker_next_retry_at,
            )
            return

        try:
            if self._is_live_mode():
                refresh = self.refresh_portfolio_snapshot(force=False, trigger="tick")
                if not bool(refresh.get("ok")) and self.runtime.blocker == "KIS_TOKEN_COOLDOWN":
                    self.runtime.fatal_error = None
                    self._restore_last_good_orderable(source="cached")
                    return

            snap = self.get_cached_portfolio_snapshot() or {}
            account = snap.get("account", {}) if isinstance(snap, dict) else {}
            live_orderable, source = self._compute_orderable_cash(account if isinstance(account, dict) else {})
            self.runtime.available_cash = self._money_to_float(
                account.get("available_cash", live_orderable) if isinstance(account, dict) else live_orderable
            )
            self.runtime.d2_cash = self._money_to_float(
                account.get("d2_cash", account.get("d2_deposit", 0)) if isinstance(account, dict) else 0
            )
            self.runtime.snapshot_warning = str((snap or {}).get("warning") or "")

            if live_orderable > 0:
                self.runtime.orderable_cash = live_orderable
                self.runtime.orderable_cash_source = source
                self.runtime.orderable_cash_stale = False
                self._save_last_good_orderable(live_orderable)
            else:
                self._restore_last_good_orderable(source="cached")
            self._apply_profile_by_cash(self.runtime.orderable_cash)
            self._clear_blocker()
        except Exception as exc:
            is_cooldown, next_retry = self._is_cooldown_error(exc)
            if is_cooldown:
                self._record_blocker("KIS_TOKEN_COOLDOWN", next_retry)
                self.runtime.fatal_error = None
                self._restore_last_good_orderable(source="cached")
                logger.warning("tick skip reason=KIS_TOKEN_COOLDOWN next_retry_at=%s", next_retry)
                return
            self._restore_last_good_orderable(source="cached")
            logger.warning("tick skip reason=PORTFOLIO_FETCH_FAIL detail=%s", self.format_exception(exc))

        market = get_market_status()
        if not market.can_place_order:
            logger.info("tick skip reason=MARKET_CLOSED")
            return

        self._maybe_send_hourly_portfolio_update(market)

        risk_ok, risk_reason = self.risk_check_detail()
        if not risk_ok:
            self._record_blocker("RISK_LIMIT", None)
            logger.warning("tick skip reason=RISK detail=%s", risk_reason)
            return

        try:
            quotes = self._filter_small_cash_universe(self._load_universe_quotes())
            candidates = self.build_candidates(quotes)
            self.runtime.candidates_count = len(candidates)
            logger.info("tick candidate_count=%s", len(candidates))
            for candidate in candidates:
                self.db.insert_signal(
                    candidate["symbol"],
                    candidate["total_score"],
                    json.dumps(candidate["stage_scores"], ensure_ascii=False),
                    candidate["pass_fail"],
                    candidate["reason"],
                )
            self._attempt_buy_candidates(candidates)
            self._manage_positions(quotes)
            self.runtime.fatal_error = None
        except Exception as exc:
            is_cooldown, next_retry = self._is_cooldown_error(exc)
            if is_cooldown:
                self._record_blocker("KIS_TOKEN_COOLDOWN", next_retry)
                self.runtime.fatal_error = None
                self._restore_last_good_orderable(source="cached")
                logger.warning("tick skip reason=KIS_TOKEN_COOLDOWN next_retry_at=%s", next_retry)
                return
            formatted = self.format_exception(exc)
            self.runtime.fatal_error = formatted
            self.runtime.enabled = False
            self.db.set_engine_state("auto_trading_enabled", "false")
            self.notifier.send(f"[FATAL] AutoTrade stopped: {formatted[:280]}")
            logger.exception("Fatal engine error: %s", formatted)

    def _load_universe_quotes(self) -> list[Quote]:
        symbols = self._resolve_watchlist_symbols()
        return self.kis.fetch_universe_quotes(symbols=symbols)

    def _candidate_buy_budget_krw(self) -> float:
        orderable = float(self.runtime.orderable_cash or 0.0)
        available = float(self.runtime.available_cash or 0.0)
        cash_budget = orderable if orderable > 0 else available
        if cash_budget <= 0:
            return 0.0

        max_buy = self._max_buy_per_trade()
        if max_buy > 0:
            return max(0.0, min(cash_budget, max_buy))
        return max(0.0, cash_budget)

    def _filter_small_cash_universe(self, quotes: list[Quote]) -> list[Quote]:
        budget_cap = self._candidate_buy_budget_krw()
        if self.runtime.current_profile == "small_cash":
            # Keep low-price bias for small account profile.
            budget_cap = min(80000.0, budget_cap) if budget_cap > 0 else 80000.0

        if budget_cap <= 0:
            return [] if self.runtime.current_profile == "small_cash" else quotes
        return [q for q in quotes if q.price <= budget_cap]

    @staticmethod
    def format_exception(exc: Exception) -> str:
        err_type, message, _ = unwrap_exception(exc)
        return f"{err_type}: {message}"

    def risk_check_detail(self) -> tuple[bool, str]:
        decision = self.risk.check_daily_order_limit(self.runtime.daily_trades)
        return decision.allowed, decision.reason

    def _candidate_priority_score(self, quote: Quote, result: ScoreResult) -> float:
        scout_cfg = self.config.get("gpt_scout", {})
        prefer_price = float(scout_cfg.get("prefer_price_krw", 40000) or 40000)
        price_cap = float(scout_cfg.get("price_cap_krw", 120000) or 120000)
        overheat_vol = float(scout_cfg.get("overheat_volatility_pct", 6.5) or 6.5)

        score = float(result.total_score)
        if quote.price <= prefer_price:
            score += 8.0
        else:
            premium_ratio = (quote.price - prefer_price) / max(prefer_price, 1.0)
            score -= min(20.0, premium_ratio * 8.0)
        if quote.price > price_cap:
            score -= 25.0
        if quote.volatility_pct >= overheat_vol:
            score -= min(20.0, (quote.volatility_pct - overheat_vol) * 4.0)
        return round(score, 4)

    def build_candidates(self, quotes: list[Quote]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for quote in quotes:
            result: ScoreResult = self.strategy.evaluate(quote)
            priority_score = self._candidate_priority_score(quote, result)
            rows.append(
                {
                    "symbol": quote.symbol,
                    "price": quote.price,
                    "total_score": result.total_score,
                    "priority_score": priority_score,
                    "pass_fail": "PASS" if result.passed else "FAIL",
                    "reason": result.reason,
                    "stage_scores": result.stage_scores,
                    "stage_checks": result.stage_checks,
                    "strategy_pass": result.passed,
                }
            )
        rows.sort(key=lambda row: (row["priority_score"], row["total_score"], -row.get("price", 0)), reverse=True)
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
        while qty > 0 and self._estimate_cost(price, qty) > budget:
            qty -= 1
        return max(0, qty)

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
        if qty <= 0:
            return False, "QTY_ZERO", detail
        if price > available_cash:
            return False, "INSUFFICIENT_CASH_FOR_1_SHARE", detail
        if estimated_cost > available_cash:
            return False, "INSUFFICIENT_CASH", detail
        if max_buy > 0 and estimated_cost > max_buy:
            return False, "MAX_BUY_EXCEEDED", detail
        return True, "OK", detail

    def _attempt_buy_candidates(self, candidates: list[dict[str, Any]]) -> None:
        account = self.kis.fetch_account_summary()
        available_cash = float(account.get("orderable_cash", account.get("available_cash", 0)) or 0)
        if available_cash <= 0:
            logger.warning("event=BUY_SKIP reason=CASH_ZERO orderable_cash=0 candidates=%s", len(candidates))
            return

        pos_decision = self.risk.check_position_limit(len(self.runtime.open_positions))
        if not pos_decision.allowed:
            self._record_blocker("MAX_POSITIONS", None)
            return

        for candidate in candidates:
            if candidate["pass_fail"] != "PASS" or candidate["symbol"] in self.runtime.open_positions:
                continue
            qty = self.determine_order_qty(candidate["price"], available_cash)
            ok, reason, _ = self.check_affordability(candidate["price"], qty, available_cash)
            if not ok:
                self.runtime.recent_blockers.append(
                    {
                        "event": "BUY_SKIP",
                        "reason": reason,
                        "symbol": candidate["symbol"],
                        "ts": datetime.utcnow().isoformat(),
                    }
                )
                continue
            self._try_entry(candidate["symbol"], candidate["price"], candidate["reason"], qty=qty)
            return

    def _try_entry(self, symbol: str, price: float, reason: str, qty: int | None = None) -> None:
        if symbol in self.runtime.open_positions:
            return
        order_qty = 1 if qty is None else qty
        order = self.kis.place_order(symbol=symbol, qty=order_qty, side="BUY", price=price)
        if order.get("status") in {"SIMULATED", "FILLED", "ACCEPTED"}:
            trade_id = self.db.open_trade(symbol, order_qty, price, reason)
            self.runtime.open_positions[symbol] = {
                "trade_id": trade_id,
                "entry_price": price,
                "qty": order_qty,
                "opened_at": datetime.utcnow().isoformat(),
                "highest_price": price,
            }
            self.runtime.daily_trades += 1

    def _parse_iso(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _evaluate_exit_decision(self, position: dict[str, Any], quote: Quote) -> ExitDecision:
        exit_cfg = self.config.get("stages", {}).get("exit", {})
        mgmt_cfg = self.config.get("position_management", {})

        stop_loss_pct = float(exit_cfg.get("stop_loss_pct", 1.8))
        take_profit_pct = float(exit_cfg.get("take_profit_pct", 4.2))
        trailing_stop_pct = float(mgmt_cfg.get("trailing_stop_pct", 1.7))
        weak_trend_exit_slope = float(mgmt_cfg.get("weak_trend_exit_slope", -0.10))
        max_holding_hours = float(mgmt_cfg.get("max_holding_hours", 18))
        min_gain_for_trailing = float(mgmt_cfg.get("min_gain_for_trailing_pct", 0.8))
        max_pullback_pct = float(mgmt_cfg.get("max_pullback_from_peak_pct", 1.4))

        entry_price = float(position.get("entry_price", 0) or 0)
        if entry_price <= 0:
            return ExitDecision(True, "INVALID_ENTRY_PRICE")

        highest_price = max(float(position.get("highest_price", entry_price) or entry_price), quote.price)
        position["highest_price"] = highest_price
        change_pct = (quote.price / entry_price - 1.0) * 100.0
        peak_gain_pct = (highest_price / entry_price - 1.0) * 100.0
        drawdown_from_peak_pct = (quote.price / highest_price - 1.0) * 100.0

        if change_pct <= -abs(stop_loss_pct):
            return ExitDecision(True, f"STOP_LOSS({change_pct:.2f}%)")
        if change_pct >= abs(take_profit_pct):
            return ExitDecision(True, f"TAKE_PROFIT({change_pct:.2f}%)")

        if peak_gain_pct >= min_gain_for_trailing:
            if drawdown_from_peak_pct <= -abs(min(max_pullback_pct, trailing_stop_pct)):
                return ExitDecision(
                    True,
                    f"TRAILING_STOP(peak={peak_gain_pct:.2f}%,pullback={drawdown_from_peak_pct:.2f}%)",
                )

        if quote.trend_slope <= weak_trend_exit_slope and change_pct <= 0.5:
            return ExitDecision(True, f"TREND_WEAKENING(slope={quote.trend_slope:.3f})")

        opened_at = self._parse_iso(position.get("opened_at"))
        if opened_at and max_holding_hours > 0:
            held_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600.0
            if held_hours >= max_holding_hours and change_pct <= 0.8:
                return ExitDecision(True, f"TIME_EXIT({held_hours:.1f}h)")

        return ExitDecision(False, "HOLD")

    def _manage_positions(self, quotes: list[Quote]) -> None:
        for quote in quotes:
            if quote.symbol not in self.runtime.open_positions:
                continue
            position = self.runtime.open_positions[quote.symbol]
            decision = self._evaluate_exit_decision(position, quote)
            if not decision.should_exit:
                continue
            order = self.kis.place_order(symbol=quote.symbol, qty=position["qty"], side="SELL", price=quote.price)
            if order.get("status") in {"SIMULATED", "FILLED", "ACCEPTED"}:
                self.db.close_trade(position["trade_id"], quote.price, fees=500, reason_exit=decision.reason)
                del self.runtime.open_positions[quote.symbol]

    def _build_hourly_message(self, now_kst: datetime) -> str:
        snap = self.get_cached_portfolio_snapshot() or {}
        positions = snap.get("positions", []) if isinstance(snap.get("positions"), list) else []
        mode = self.runtime_flags.get("mode", "DRY-RUN")
        lines = [
            f"[\uc790\ub3d9\ub9e4\ub9e4 \uc2dc\uac04\ubcf4\uace0] {now_kst.strftime('%Y-%m-%d %H:%M')} KST",
            f"\ubaa8\ub4dc={mode} \ubcf4\uc720\uc885\ubaa9={len(positions)}\uac1c",
        ]
        if positions:
            for row in positions[:5]:
                symbol = str(row.get("symbol", "-"))
                qty = int(float(row.get("qty", 0) or 0))
                pnl_pct = self._money_to_float(row.get("pnl_pct", 0))
                price = self._money_to_float(row.get("eval_price", row.get("avg_price", 0)))
                lines.append(
                    f"- {symbol} {qty}\uc8fc \ud604\uc7ac\uac00 {price:,.0f}\uc6d0 "
                    f"\uc218\uc775\ub960 {pnl_pct:+.2f}%"
                )
        else:
            lines.append("- \ubcf4\uc720 \uc885\ubaa9 \uc5c6\uc74c")
        lines.append(f"\uac00\uc6a9\ud604\uae08 {self.runtime.orderable_cash:,.0f}\uc6d0")
        return "\n".join(lines)

    def _maybe_send_hourly_portfolio_update(self, market: MarketStatus) -> None:
        if not market.is_open:
            return
        cfg = self.config.get("hourly_alert", {})
        if not bool(cfg.get("enabled", True)):
            return
        if not getattr(self.notifier, "token", None):
            return

        now_kst = datetime.now(KST)
        send_minute = int(cfg.get("minute", 0))
        grace_min = int(cfg.get("grace_minutes", 8))
        if now_kst.minute < send_minute or now_kst.minute >= (send_minute + max(grace_min, 1)):
            return

        hour_key = now_kst.strftime("%Y%m%d%H")
        last_key = self.db.get_engine_state("hourly_alert_last_key")
        if last_key == hour_key:
            return

        message = self._build_hourly_message(now_kst)
        if self.notifier.send(message):
            self.db.set_engine_state("hourly_alert_last_key", hour_key)
            self.db.set_engine_state("hourly_alert_last_sent_at", now_kst.isoformat())
            self.runtime.last_hourly_alert_at = now_kst.isoformat()

    def get_cached_portfolio_snapshot(self) -> dict[str, Any] | None:
        snap, _ = self.portfolio_service.get_snapshot(force_refresh=False)
        return snap

    def clear_report_data(self, only_dry: bool = True, vacuum: bool = False) -> dict[str, Any]:
        return self.db.clear_report_data(only_dry=only_dry, vacuum=vacuum)

    def get_recent_trades(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.fetch_recent_trades(limit=limit)

    def get_watchlist_payload(self) -> dict[str, Any]:
        return {
            "symbols": self.runtime.watchlist_symbols,
            "source": self.runtime.watchlist_source,
            "updated_at": self.runtime.watchlist_updated_at,
            "date_kst": self.runtime.watchlist_date_kst,
            "candidates": self.runtime.ai_candidates,
            "openai_guard": self.runtime.openai_guard,
        }

    def get_symbol_chart(self, symbol: str, count: int = 120) -> dict[str, Any]:
        clean = "".join(ch for ch in str(symbol or "") if ch.isdigit()).zfill(6)[-6:]
        if not clean:
            return {"symbol": "", "bars": [], "events": [], "reason": "INVALID_SYMBOL"}

        bars = self.kis.fetch_intraday_bars(clean, count=count)
        trades = self.get_recent_trades(limit=600)
        events: list[dict[str, Any]] = []
        for row in trades:
            if str(row.get("symbol", "")) != clean:
                continue
            if row.get("entry_time") and row.get("entry_price"):
                events.append(
                    {
                        "ts": str(row.get("entry_time")),
                        "price": float(row.get("entry_price")),
                        "event": "BUY",
                        "qty": int(float(row.get("qty", 0) or 0)),
                    }
                )
            if row.get("exit_time") and row.get("exit_price"):
                events.append(
                    {
                        "ts": str(row.get("exit_time")),
                        "price": float(row.get("exit_price")),
                        "event": "SELL",
                        "qty": int(float(row.get("qty", 0) or 0)),
                    }
                )
        return {"symbol": clean, "bars": bars, "events": events}

    def get_config_summary(self) -> dict[str, Any]:
        cfg = self.cfg_mgr.load()
        return {
            "mode": str(cfg.get("mode", "DRY-RUN")),
            "scan_interval_seconds": int(cfg.get("scan_interval_seconds", 60)),
            "portfolio_refresh_interval_sec": int(cfg.get("portfolio_refresh_interval_sec", 300)),
            "gpt_scout_enabled": bool(cfg.get("gpt_scout", {}).get("enabled", True)),
        }

    def set_mode(self, mode: str) -> dict[str, Any]:
        mode_u = str(mode or "").strip().upper()
        if mode_u in {"DRY", "DRYRUN", "DRY_RUN"}:
            mode_u = "DRY-RUN"
        if mode_u not in {"DRY-RUN", "LIVE"}:
            raise ValueError("mode must be DRY-RUN or LIVE")
        cfg = self.cfg_mgr.load()
        cfg["mode"] = mode_u
        self.cfg_mgr.save(cfg)
        self._reload_config()
        return self.get_config_summary()

    def _is_live_mode(self) -> bool:
        return bool(self.runtime_flags.get("live_order_enabled"))

    def refresh_portfolio_snapshot(self, force: bool = False, trigger: str = "manual") -> dict[str, Any]:
        self._reload_config()
        now = time.time()
        min_gap_sec = 60 if trigger == "manual" else int(self.config.get("portfolio_refresh_interval_sec", 300))
        next_allowed = float(self.db.get_engine_state_float("portfolio_refresh_next_retry_epoch", default=0.0))
        if not force and now < next_allowed:
            next_retry_at = datetime.utcfromtimestamp(next_allowed).isoformat()
            return {
                "ok": False,
                "reason": "PORTFOLIO_REFRESH_RATE_LIMIT",
                "next_retry_at": next_retry_at,
                "source": "CACHE",
                "snapshot": self.get_cached_portfolio_snapshot(),
            }

        self.db.set_engine_state_float("portfolio_refresh_next_retry_epoch", now + min_gap_sec)
        if not self._is_live_mode() and trigger != "tick":
            snap = self.get_cached_portfolio_snapshot() or self.get_portfolio_snapshot(force_refresh=False)
            return {"ok": True, "reason": "DRY_RUN_CACHE", "source": "CACHE", "snapshot": snap}

        if not self._is_live_mode() and trigger == "tick":
            return {"ok": True, "reason": "SKIP_NON_LIVE_TICK", "source": "CACHE", "snapshot": self.get_cached_portfolio_snapshot()}

        try:
            snap = self.get_portfolio_snapshot(force_refresh=True)
            self.runtime.last_portfolio_refresh_epoch = now
            self.db.set_engine_state("last_portfolio_refresh_at", datetime.utcnow().isoformat())
            return {"ok": True, "reason": "LIVE_REFRESHED", "source": "LIVE", "snapshot": snap}
        except Exception as exc:
            is_cooldown, next_retry = self._is_cooldown_error(exc)
            if is_cooldown:
                self._record_blocker("KIS_TOKEN_COOLDOWN", next_retry)
            return {
                "ok": False,
                "reason": self.format_exception(exc),
                "next_retry_at": next_retry,
                "source": "CACHE",
                "snapshot": self.get_cached_portfolio_snapshot(),
            }

    def get_portfolio_snapshot(self, force_refresh: bool = False) -> dict[str, Any]:
        self._reload_config()
        snap, state = self.portfolio_service.get_snapshot(force_refresh=force_refresh)
        if snap:
            return snap

        account = self.kis.fetch_account_summary()
        orderable_cash, source = self._compute_orderable_cash(account if isinstance(account, dict) else {})
        available_cash = self._money_to_float(account.get("available_cash", orderable_cash))
        if available_cash <= 0 and orderable_cash > 0:
            available_cash = orderable_cash
        d2_cash = self._money_to_float(account.get("d2_cash", account.get("d2_deposit", 0)))

        warning = ""
        selected_keys = account.get("selected_keys", {}) if isinstance(account.get("selected_keys"), dict) else {}
        if orderable_cash == 0 and d2_cash > 0:
            warning = "ORDERABLE_CASH_MAPPING_SUSPECT"
            logger.warning(
                "event=ACCOUNT_MAPPING_WARN warning=%s orderable_cash=%s d2_cash=%s selected_keys=%s",
                warning,
                int(orderable_cash),
                int(d2_cash),
                selected_keys,
            )

        account["orderable_cash"] = orderable_cash
        account["available_cash"] = available_cash
        account["d2_cash"] = d2_cash

        positions = self.kis.fetch_positions()
        orders = self.kis.fetch_recent_orders(limit=20)
        payload = {
            "account": account,
            "orderable_cash": orderable_cash,
            "available_cash": available_cash,
            "d2_cash": d2_cash,
            "snapshot_source": "account_summary_v2",
            "warning": warning,
            "orderable_cash_source": source if orderable_cash > 0 else "unknown",
            "orderable_cash_stale": False,
            "orderable_cash_last_updated_at": self.db.get_engine_state("last_good_orderable_at"),
            "positions": positions,
            "orders": orders,
            "token_status": self.kis.get_token_status(),
            "throttle": state,
            "ts": datetime.utcnow().isoformat(),
        }
        self.portfolio_service.set_cached(payload)
        return payload
