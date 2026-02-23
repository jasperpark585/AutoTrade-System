from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


CONFIG_PATH = Path("strategy.yaml")


@dataclass
class ConfigManager:
    path: Path = CONFIG_PATH

    def __post_init__(self) -> None:
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PyYAML is required to load strategy.yaml") from exc

        with self._lock:
            with self.path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f)

    def save(self, data: dict[str, Any]) -> None:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PyYAML is required to save strategy.yaml") from exc

        with self._lock:
            with self.path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
