import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_file: str = "logs/autotrade.log") -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(formatter)

    root.addHandler(stream_handler)
    root.addHandler(file_handler)
