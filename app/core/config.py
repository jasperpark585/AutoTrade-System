from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

import yaml


CONFIG_PATH = Path("strategy.yaml")


@dataclass
class ConfigManager:
    path: Path = CONFIG_PATH

    def __post_init__(self) -> None:
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            with self.path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f)

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            with self.path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
