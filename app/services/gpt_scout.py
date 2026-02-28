from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)
UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")


def _normalize_symbol(symbol: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(symbol or ""))
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass
class ScoutRankConfig:
    max_candidates: int = 8
    price_cap_krw: float = 120000.0
    prefer_price_krw: float = 40000.0
    overheat_score_limit: float = 75.0


class GPTScout:
    def __init__(
        self,
        snapshot_path: str = "data/gpt_candidates.json",
        guard_state_path: str = "data/openai_guard_state.json",
    ) -> None:
        self.snapshot_path = Path(snapshot_path)
        self.guard_state_path = Path(guard_state_path)

    def _load_json_file(self) -> dict[str, Any] | None:
        if not self.snapshot_path.exists():
            return None
        try:
            raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _save_json_file(self, payload: dict[str, Any]) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.snapshot_path.with_suffix(self.snapshot_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.snapshot_path)

    def _load_guard_state(self) -> dict[str, Any]:
        if not self.guard_state_path.exists():
            return {}
        try:
            raw = json.loads(self.guard_state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_guard_state(self, state: dict[str, Any]) -> None:
        self.guard_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.guard_state_path.with_suffix(self.guard_state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.guard_state_path)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _fallback_candidates(symbols: list[str], max_candidates: int) -> list[dict[str, Any]]:
        fallback: list[dict[str, Any]] = []
        for idx, symbol in enumerate(symbols[:max_candidates]):
            fallback.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "thesis": "fallback watchlist item",
                    "risk_note": "AI unavailable; fallback used",
                    "price_krw": 0,
                    "momentum_score": max(40, 70 - idx * 3),
                    "pre_breakout_score": max(35, 65 - idx * 2),
                    "overheat_score": 20 + idx,
                }
            )
        return fallback

    @staticmethod
    def _rank_candidates(candidates: list[dict[str, Any]], rank_cfg: ScoutRankConfig) -> list[dict[str, Any]]:
        seen: set[str] = set()
        ranked_rows: list[tuple[float, dict[str, Any]]] = []
        for row in candidates:
            symbol = _normalize_symbol(row.get("symbol"))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)

            price = _to_float(row.get("price_krw"), default=0.0)
            momentum = _to_float(row.get("momentum_score"), default=50.0)
            pre_breakout = _to_float(row.get("pre_breakout_score"), default=50.0)
            overheat = _to_float(row.get("overheat_score"), default=50.0)
            score = (momentum * 0.35) + (pre_breakout * 0.45) + ((100.0 - overheat) * 0.20)

            if price > 0:
                if price <= rank_cfg.prefer_price_krw:
                    score += 12.0
                else:
                    premium = (price - rank_cfg.prefer_price_krw) / max(rank_cfg.prefer_price_krw, 1.0)
                    score -= min(25.0, premium * 10.0)
                if price > rank_cfg.price_cap_krw:
                    score -= 40.0
            if overheat >= rank_cfg.overheat_score_limit:
                score -= 25.0

            out = dict(row)
            out["symbol"] = symbol
            out["price_krw"] = price
            out["momentum_score"] = momentum
            out["pre_breakout_score"] = pre_breakout
            out["overheat_score"] = overheat
            out["priority_score"] = round(score, 3)
            ranked_rows.append((score, out))

        ranked_rows.sort(key=lambda x: x[0], reverse=True)
        return [row for _, row in ranked_rows[: max(1, rank_cfg.max_candidates)]]

    def _guard_pricing(self, cfg: dict[str, Any]) -> tuple[float, float]:
        guard_cfg = cfg.get("quota_guard", {}) if isinstance(cfg.get("quota_guard"), dict) else {}
        pricing = guard_cfg.get("pricing_per_1m_tokens", {}) if isinstance(guard_cfg.get("pricing_per_1m_tokens"), dict) else {}
        # Conservative defaults (USD / 1M tokens) so safety guard is strict.
        input_usd = max(0.0, _to_float(pricing.get("input_usd"), default=0.40))
        output_usd = max(0.0, _to_float(pricing.get("output_usd"), default=1.60))
        return input_usd, output_usd

    def _cost_usd(self, prompt_tokens: int, completion_tokens: int, cfg: dict[str, Any]) -> float:
        input_usd, output_usd = self._guard_pricing(cfg)
        cost = ((max(prompt_tokens, 0) * input_usd) + (max(completion_tokens, 0) * output_usd)) / 1_000_000.0
        return round(cost, 8)

    @staticmethod
    def _state_count(state: dict[str, Any], bucket: str, key: str) -> int:
        obj = state.get(bucket, {})
        if not isinstance(obj, dict):
            return 0
        return _to_int(obj.get(key), 0)

    @staticmethod
    def _state_cost(state: dict[str, Any], month_key: str) -> float:
        obj = state.get("cost_usd_by_month", {})
        if not isinstance(obj, dict):
            return 0.0
        return _to_float(obj.get(month_key), 0.0)

    def _openai_guard_check(self, cfg: dict[str, Any]) -> tuple[bool, str]:
        guard_cfg = cfg.get("quota_guard", {}) if isinstance(cfg.get("quota_guard"), dict) else {}
        if not bool(guard_cfg.get("enabled", True)):
            return True, "OPENAI_GUARD_DISABLED"

        now_utc = datetime.now(UTC)
        now_kst = now_utc.astimezone(KST)
        date_key = now_kst.date().isoformat()
        month_key = now_kst.strftime("%Y-%m")
        state = self._load_guard_state()

        block_until = _parse_iso_utc(str(state.get("block_until_utc") or ""))
        if block_until and now_utc < block_until:
            return False, "OPENAI_GUARD_COOLDOWN"

        if bool(guard_cfg.get("require_paid_opt_in", True)):
            env_name = str(guard_cfg.get("paid_opt_in_env", "OPENAI_PAID_ALLOWED") or "OPENAI_PAID_ALLOWED")
            if not _env_bool(env_name, default=False):
                return False, "OPENAI_PAID_OPT_IN_REQUIRED"

        max_requests_per_day = _to_int(guard_cfg.get("max_requests_per_day"), 0)
        max_requests_per_month = _to_int(guard_cfg.get("max_requests_per_month"), 0)
        if max_requests_per_day > 0 and self._state_count(state, "requests_by_date", date_key) >= max_requests_per_day:
            return False, "OPENAI_GUARD_DAILY_LIMIT"
        if max_requests_per_month > 0 and self._state_count(state, "requests_by_month", month_key) >= max_requests_per_month:
            return False, "OPENAI_GUARD_MONTHLY_LIMIT"

        est_prompt = _to_int(guard_cfg.get("estimated_prompt_tokens_per_call"), 1200)
        est_completion = _to_int(guard_cfg.get("estimated_completion_tokens_per_call"), 500)
        est_cost = self._cost_usd(est_prompt, est_completion, cfg)
        max_estimated_cost_per_call_usd = _to_float(guard_cfg.get("max_estimated_cost_per_call_usd"), 0.0)
        if max_estimated_cost_per_call_usd > 0 and est_cost > max_estimated_cost_per_call_usd:
            return False, "OPENAI_GUARD_PER_CALL_ESTIMATE_LIMIT"

        max_monthly_cost_usd = _to_float(guard_cfg.get("max_monthly_cost_usd"), 0.0)
        reserve_ratio = min(1.0, max(0.0, _to_float(guard_cfg.get("reserve_ratio"), 0.9)))
        if max_monthly_cost_usd > 0:
            monthly_cost = self._state_cost(state, month_key)
            if (monthly_cost + est_cost) > (max_monthly_cost_usd * reserve_ratio):
                return False, "OPENAI_GUARD_MONTHLY_BUDGET"

        return True, "OPENAI_GUARD_OK"

    def _openai_guard_record(
        self,
        cfg: dict[str, Any],
        reason: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        guard_cfg = cfg.get("quota_guard", {}) if isinstance(cfg.get("quota_guard"), dict) else {}
        if not bool(guard_cfg.get("enabled", True)):
            return

        now_utc = datetime.now(UTC)
        now_kst = now_utc.astimezone(KST)
        date_key = now_kst.date().isoformat()
        month_key = now_kst.strftime("%Y-%m")
        state = self._load_guard_state()

        by_date = state.get("requests_by_date", {})
        if not isinstance(by_date, dict):
            by_date = {}
        by_date[date_key] = _to_int(by_date.get(date_key), 0) + 1
        state["requests_by_date"] = by_date

        by_month = state.get("requests_by_month", {})
        if not isinstance(by_month, dict):
            by_month = {}
        by_month[month_key] = _to_int(by_month.get(month_key), 0) + 1
        state["requests_by_month"] = by_month

        token_month = state.get("tokens_by_month", {})
        if not isinstance(token_month, dict):
            token_month = {}
        current_tokens = token_month.get(month_key, {})
        if not isinstance(current_tokens, dict):
            current_tokens = {}
        current_tokens["prompt_tokens"] = _to_int(current_tokens.get("prompt_tokens"), 0) + max(0, int(prompt_tokens))
        current_tokens["completion_tokens"] = _to_int(current_tokens.get("completion_tokens"), 0) + max(0, int(completion_tokens))
        token_month[month_key] = current_tokens
        state["tokens_by_month"] = token_month

        cost_month = state.get("cost_usd_by_month", {})
        if not isinstance(cost_month, dict):
            cost_month = {}
        cost_month[month_key] = round(_to_float(cost_month.get(month_key), 0.0) + max(0.0, cost_usd), 8)
        state["cost_usd_by_month"] = cost_month

        if reason.startswith("OPENAI_HTTP_429"):
            cooldown_minutes = max(1, _to_int(guard_cfg.get("cooldown_minutes_on_http_429"), 360))
            state["block_until_utc"] = (now_utc + timedelta(minutes=cooldown_minutes)).isoformat()

        state["last_reason"] = reason
        state["last_updated_at"] = now_utc.isoformat()
        self._save_guard_state(state)

    def get_guard_status(self, cfg: dict[str, Any]) -> dict[str, Any]:
        guard_cfg = cfg.get("quota_guard", {}) if isinstance(cfg.get("quota_guard"), dict) else {}
        state = self._load_guard_state()
        now_kst = datetime.now(UTC).astimezone(KST)
        date_key = now_kst.date().isoformat()
        month_key = now_kst.strftime("%Y-%m")
        return {
            "enabled": bool(guard_cfg.get("enabled", True)),
            "last_reason": str(state.get("last_reason") or ""),
            "block_until_utc": state.get("block_until_utc"),
            "requests_today": self._state_count(state, "requests_by_date", date_key),
            "requests_this_month": self._state_count(state, "requests_by_month", month_key),
            "cost_usd_this_month": self._state_cost(state, month_key),
            "max_requests_per_day": _to_int(guard_cfg.get("max_requests_per_day"), 0),
            "max_requests_per_month": _to_int(guard_cfg.get("max_requests_per_month"), 0),
            "max_monthly_cost_usd": _to_float(guard_cfg.get("max_monthly_cost_usd"), 0.0),
            "reserve_ratio": _to_float(guard_cfg.get("reserve_ratio"), 0.9),
            "require_paid_opt_in": bool(guard_cfg.get("require_paid_opt_in", True)),
            "paid_opt_in_env": str(guard_cfg.get("paid_opt_in_env", "OPENAI_PAID_ALLOWED")),
        }

    def _request_openai_candidates(self, cfg: dict[str, Any], fallback_symbols: list[str]) -> tuple[list[dict[str, Any]], str]:
        api_key = str(cfg.get("api_key") or "").strip()
        if not api_key:
            return [], "OPENAI_KEY_MISSING"
        if requests is None:
            return [], "REQUESTS_MISSING"

        guard_ok, guard_reason = self._openai_guard_check(cfg)
        if not guard_ok:
            return [], guard_reason

        model = str(cfg.get("model") or "gpt-4.1-mini").strip()
        max_candidates = max(3, int(cfg.get("max_candidates", 8)))
        max_output_tokens = max(300, _to_int(cfg.get("max_output_tokens"), 900))
        user_context = str(cfg.get("user_context") or "").strip()
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        fallback_text = ",".join(fallback_symbols) if fallback_symbols else "-"
        prompt = (
            "Select Korean stock symbols that are likely to surge soon but are not already overheated. "
            "Prioritize lower-priced names over expensive extended names. "
            "Output strict JSON with this schema: "
            "{\"candidates\":[{\"symbol\":\"6-digit\",\"name\":\"name\",\"thesis\":\"one line\","
            "\"risk_note\":\"risk\","
            "\"price_krw\":number,\"momentum_score\":0-100,\"pre_breakout_score\":0-100,\"overheat_score\":0-100}]}. "
            f"Maximum candidates: {max_candidates}. "
            f"Current KST: {now_kst}. Reference symbols: {fallback_text}. "
            f"User context: {user_context if user_context else 'none'}."
        )
        body = {
            "model": model,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": max_output_tokens,
            "messages": [
                {"role": "system", "content": "Korean stock scout for automated trading engine."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            resp = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=20)
        except Exception as exc:
            self._openai_guard_record(cfg, reason="OPENAI_REQUEST_FAIL")
            logger.warning("gpt_scout request failed: %s", exc)
            return [], "OPENAI_REQUEST_FAIL"

        if resp.status_code != 200:
            reason = f"OPENAI_HTTP_{resp.status_code}"
            self._openai_guard_record(cfg, reason=reason)
            logger.warning("gpt_scout response fail status=%s body=%s", resp.status_code, resp.text[:300])
            return [], reason

        try:
            data = resp.json()
        except Exception:
            self._openai_guard_record(cfg, reason="OPENAI_INVALID_JSON")
            return [], "OPENAI_INVALID_JSON"

        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        prompt_tokens = _to_int(usage.get("prompt_tokens"), 0)
        completion_tokens = _to_int(usage.get("completion_tokens"), 0)
        cost_usd = self._cost_usd(prompt_tokens, completion_tokens, cfg)

        choices = data.get("choices", [])
        if not isinstance(choices, list) or not choices:
            self._openai_guard_record(
                cfg,
                reason="OPENAI_EMPTY_CHOICES",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
            )
            return [], "OPENAI_EMPTY_CHOICES"

        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = str(message.get("content", "")).strip()
        parsed = self._extract_json(content)
        candidates = parsed.get("candidates", [])
        if not isinstance(candidates, list):
            self._openai_guard_record(
                cfg,
                reason="OPENAI_BAD_SCHEMA",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
            )
            return [], "OPENAI_BAD_SCHEMA"

        self._openai_guard_record(
            cfg,
            reason="OPENAI_OK",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )
        return [c for c in candidates if isinstance(c, dict)], "OPENAI_OK"

    def get_cached_payload(self) -> dict[str, Any] | None:
        return self._load_json_file()

    def refresh_daily_candidates(self, cfg: dict[str, Any], fallback_symbols: list[str]) -> dict[str, Any]:
        max_candidates = max(3, int(cfg.get("max_candidates", 8)))
        rank_cfg = ScoutRankConfig(
            max_candidates=max_candidates,
            price_cap_krw=max(1.0, _to_float(cfg.get("price_cap_krw"), default=120000.0)),
            prefer_price_krw=max(1.0, _to_float(cfg.get("prefer_price_krw"), default=40000.0)),
            overheat_score_limit=min(100.0, max(1.0, _to_float(cfg.get("overheat_score_limit"), default=75.0))),
        )

        candidates, source = self._request_openai_candidates(cfg, fallback_symbols)
        if not candidates:
            candidates = self._fallback_candidates(fallback_symbols, max_candidates=max_candidates)
            source = f"FALLBACK:{source}"

        ranked = self._rank_candidates(candidates, rank_cfg=rank_cfg)
        now_kst = datetime.now(KST)
        payload = {
            "date_kst": now_kst.date().isoformat(),
            "generated_at": datetime.utcnow().isoformat(),
            "generated_at_kst": now_kst.isoformat(),
            "source": source,
            "symbols": [str(x.get("symbol")) for x in ranked if x.get("symbol")],
            "candidates": ranked,
            "rank_config": {
                "max_candidates": rank_cfg.max_candidates,
                "price_cap_krw": rank_cfg.price_cap_krw,
                "prefer_price_krw": rank_cfg.prefer_price_krw,
                "overheat_score_limit": rank_cfg.overheat_score_limit,
            },
            "openai_guard": self.get_guard_status(cfg),
        }
        self._save_json_file(payload)
        return payload
