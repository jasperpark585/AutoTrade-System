from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

CONFIG_PATH = Path("strategy.yaml")
ENV_PATH = Path(".env")
TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def _normalize_mode(raw: str | None) -> str:
    mode = (raw or "DRY-RUN").strip().upper()
    if mode in {"DRY-RUN", "DRY", "DRYRUN", "DRY_RUN"}:
        return "DRY-RUN"
    if mode == "LIVE":
        return "LIVE"
    raise ValueError("mode must be DRY-RUN or LIVE")


def _load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_key = key.strip()
        if not env_key:
            continue
        env_value = value.strip().strip("'").strip('"')
        os.environ.setdefault(env_key, env_value)


@dataclass
class ConfigManager:
    path: Path = CONFIG_PATH

    def __post_init__(self) -> None:
        self._lock = RLock()
        _load_dotenv_file(ENV_PATH)

    def _read_yaml(self) -> dict[str, Any]:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PyYAML is required to load strategy.yaml") from exc

        with self.path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("strategy.yaml root must be a mapping")
        return data

    def _validate(self, data: dict[str, Any]) -> None:
        required = ("risk_limits", "stages", "scoring_weights")
        for key in required:
            if key not in data or not isinstance(data.get(key), dict):
                raise ValueError(f"strategy.yaml missing required mapping: {key}")

    def load(self) -> dict[str, Any]:
        with self._lock:
            data = self._read_yaml()
            data["mode"] = _normalize_mode(os.getenv("MODE", str(data.get("mode", "DRY-RUN"))))

            scan_env = os.getenv("SCAN_INTERVAL_SECONDS")
            if scan_env:
                data["scan_interval_seconds"] = int(scan_env)

            self._validate(data)
            return data

    def runtime_flags(self, config: dict[str, Any]) -> dict[str, Any]:
        mode = _normalize_mode(str(config.get("mode", "DRY-RUN")))
        explicit_live = _env_bool("LIVE", default=False)
        env_dry = _env_bool("DRY_RUN", default=(mode != "LIVE"))
        mock_order = _env_bool("KIS_MOCK_ORDER", default=False)

        block_reasons: list[str] = []
        if mode != "LIVE":
            block_reasons.append("strategy.yaml mode is not LIVE.")
        if not explicit_live:
            block_reasons.append("environment LIVE=true is required.")
        if env_dry:
            block_reasons.append("environment DRY_RUN=false is required.")
        if mock_order:
            block_reasons.append("environment KIS_MOCK_ORDER=false is required.")

        live_order_enabled = mode == "LIVE" and explicit_live and (not env_dry) and (not mock_order)
        return {
            "mode": mode,
            "explicit_live": explicit_live,
            "env_dry_run": env_dry,
            "dry_run": not live_order_enabled,
            "mock_order": mock_order,
            "live_order_enabled": live_order_enabled,
            "live_block_reasons": [] if live_order_enabled else block_reasons,
        }

    def save(self, data: dict[str, Any]) -> None:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PyYAML is required to save strategy.yaml") from exc

        data = dict(data)
        data["mode"] = _normalize_mode(str(data.get("mode", "DRY-RUN")))
        self._validate(data)
        with self._lock:
            with self.path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
