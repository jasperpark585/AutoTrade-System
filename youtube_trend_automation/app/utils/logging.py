from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(logs_dir: Path, level: str = "INFO") -> None:
    """Configure console and file logging."""

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "youtube_trend_automation.log"
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger."""

    return logging.getLogger(name)

