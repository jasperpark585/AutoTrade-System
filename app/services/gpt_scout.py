from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)
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


@dataclass
class ScoutRankConfig:
    max_candidates: int = 8
    price_cap_krw: float = 120000.0
    prefer_price_krw: float = 40000.0
    overheat_score_limit: float = 75.0


class GPTScout:
    def __init__(self, snapshot_path: str = "data/gpt_candidates.json") -> None:
        self.snapshot_path = Path(snapshot_path)

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

    def get_cached_payload(self) -> dict[str, Any] | None:
        return self._load_json_file()

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

    def _request_openai_candidates(self, cfg: dict[str, Any], fallback_symbols: list[str]) -> tuple[list[dict[str, Any]], str]:
        api_key = str(cfg.get("api_key") or "").strip()
        if not api_key:
            return [], "OPENAI_KEY_MISSING"
        if requests is None:
            return [], "REQUESTS_MISSING"

        model = str(cfg.get("model") or "gpt-4.1-mini").strip()
        max_candidates = max(3, int(cfg.get("max_candidates", 8)))
        user_context = str(cfg.get("user_context") or "").strip()
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        fallback_text = ",".join(fallback_symbols) if fallback_symbols else "-"
        prompt = (
            "당신은 한국 주식 단타/스윙 스카우트 리서처다. "
            "이미 급등한 고가 종목보다, 당일 급등 가능성이 있는 저가 종목을 우선 추천해라. "
            "아래 JSON 스키마로만 응답하라: "
            "{\"candidates\":[{\"symbol\":\"6자리종목코드\",\"name\":\"종목명\",\"thesis\":\"한줄근거\","
            "\"risk_note\":\"리스크\","
            "\"price_krw\":숫자,\"momentum_score\":0~100,\"pre_breakout_score\":0~100,\"overheat_score\":0~100}]}"
            f" 후보 개수는 최대 {max_candidates}개. "
            f"현재 시각(KST): {now_kst}. 참고 종목코드: {fallback_text}. "
            f"추가 운용 컨텍스트: {user_context if user_context else '없음'}."
        )
        body = {
            "model": model,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "한국 주식 자동매매 엔진용 종목 후보 생성기"},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            resp = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=20)
        except Exception as exc:
            logger.warning("gpt_scout request failed: %s", exc)
            return [], "OPENAI_REQUEST_FAIL"

        if resp.status_code != 200:
            logger.warning("gpt_scout response fail status=%s body=%s", resp.status_code, resp.text[:300])
            return [], f"OPENAI_HTTP_{resp.status_code}"

        try:
            data = resp.json()
        except Exception:
            return [], "OPENAI_INVALID_JSON"
        choices = data.get("choices", [])
        if not isinstance(choices, list) or not choices:
            return [], "OPENAI_EMPTY_CHOICES"
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = str(message.get("content", "")).strip()
        parsed = self._extract_json(content)
        candidates = parsed.get("candidates", [])
        if not isinstance(candidates, list):
            return [], "OPENAI_BAD_SCHEMA"
        return [c for c in candidates if isinstance(c, dict)], "OPENAI_OK"

    @staticmethod
    def _fallback_candidates(symbols: list[str], max_candidates: int) -> list[dict[str, Any]]:
        fallback: list[dict[str, Any]] = []
        for idx, symbol in enumerate(symbols[:max_candidates]):
            fallback.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "thesis": "기본 감시종목 fallback",
                    "risk_note": "AI 응답 실패 시 기본 목록 사용",
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
        }
        self._save_json_file(payload)
        return payload
