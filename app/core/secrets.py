from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


logger = logging.getLogger(__name__)


class SecretStore:
    """Encrypted local secret storage for EC2 single-instance operation."""

    def __init__(self, file_path: str = "data/secrets.enc") -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._derive_key())

    @staticmethod
    def _derive_key() -> bytes:
        master = os.getenv("AUTOTRADE_MASTER_PASSPHRASE")
        if not master:
            raise RuntimeError("AUTOTRADE_MASTER_PASSPHRASE is required for secret encryption.")
        digest = hashlib.sha256(master.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def save(self, payload: dict[str, Any]) -> None:
        blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        token = self._fernet.encrypt(blob)
        self.file_path.write_bytes(token)
        logger.info("Encrypted secrets saved.")

    def load(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {}
        token = self.file_path.read_bytes()
        data = self._fernet.decrypt(token)
        return json.loads(data.decode("utf-8"))

    def masked_view(self) -> dict[str, str]:
        secrets = self.load()
        return {k: self._mask(v) for k, v in secrets.items()}

    @staticmethod
    def _mask(value: Any) -> str:
        text = str(value)
        if len(text) <= 4:
            return "*" * len(text)
        return f"{text[:2]}{'*' * (len(text) - 4)}{text[-2:]}"
