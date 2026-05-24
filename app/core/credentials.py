from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.database import Database

CREDENTIAL_STATE_KEY = "broker_credentials"
DEFAULT_CREDENTIAL_KEY_PATH = Path("data/credential.key")


@dataclass(frozen=True)
class BrokerCredentials:
    appkey: str
    appsecret: str
    account_no: str
    base_url: str = "https://openapi.koreainvestment.com:9443"
    is_paper: bool = False

    def validate(self) -> list[str]:
        missing: list[str] = []
        if not self.appkey.strip():
            missing.append("KIS AppKey")
        if not self.appsecret.strip():
            missing.append("KIS AppSecret")
        if not self.account_no.strip():
            missing.append("KIS account number")
        normalized_account = self.account_no.replace("-", "").strip()
        if normalized_account and (len(normalized_account) < 10 or not normalized_account.isdigit()):
            missing.append("KIS account number format")
        return missing

    def masked(self) -> dict[str, Any]:
        return {
            "configured": not self.validate(),
            "appkey": mask_secret(self.appkey),
            "appsecret": mask_secret(self.appsecret),
            "account_no": mask_account_no(self.account_no),
            "base_url": self.base_url,
            "is_paper": self.is_paper,
        }


def mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if len(text) < 8:
        return "***" if text else ""
    return f"{text[:4]}...{text[-4:]}"


def mask_account_no(value: str) -> str:
    text = str(value or "").strip()
    digits = text.replace("-", "")
    if len(digits) < 6:
        return "***" if digits else ""
    suffix = digits[-2:]
    return f"{digits[:2]}******-{suffix}"


class CredentialStore:
    def __init__(self, db: Database, key_path: Path = DEFAULT_CREDENTIAL_KEY_PATH) -> None:
        self.db = db
        self.key_path = key_path

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            raw = self.key_path.read_bytes()
            if raw:
                return raw
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32)
        self.key_path.write_bytes(key)
        try:
            os.chmod(self.key_path, 0o600)
        except Exception:
            pass
        return key

    def _xor_stream(self, data: bytes, key: bytes) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < len(data):
            block = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest()
            out.extend(block)
            counter += 1
        return bytes(b ^ k for b, k in zip(data, out))

    def _encrypt(self, payload: dict[str, Any]) -> str:
        key = self._load_or_create_key()
        plaintext = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ciphertext = self._xor_stream(plaintext, key)
        digest = hmac.new(key, ciphertext, hashlib.sha256).digest()
        envelope = {
            "v": 1,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "hmac": base64.b64encode(digest).decode("ascii"),
        }
        return json.dumps(envelope, sort_keys=True)

    def _decrypt(self, envelope_text: str) -> dict[str, Any]:
        key = self._load_or_create_key()
        envelope = json.loads(envelope_text)
        ciphertext = base64.b64decode(str(envelope.get("ciphertext") or ""))
        expected = base64.b64decode(str(envelope.get("hmac") or ""))
        actual = hmac.new(key, ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, actual):
            raise ValueError("stored broker credentials failed integrity check")
        plaintext = self._xor_stream(ciphertext, key)
        payload = json.loads(plaintext.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def save(self, credentials: BrokerCredentials) -> None:
        missing = credentials.validate()
        if missing:
            raise ValueError(f"Invalid broker credentials: {', '.join(missing)}")
        self.db.set_engine_state(CREDENTIAL_STATE_KEY, self._encrypt(asdict(credentials)))

    def load(self) -> BrokerCredentials:
        raw = self.db.get_engine_state(CREDENTIAL_STATE_KEY)
        if not raw:
            return BrokerCredentials(appkey="", appsecret="", account_no="")
        payload = self._decrypt(raw)
        return BrokerCredentials(
            appkey=str(payload.get("appkey") or ""),
            appsecret=str(payload.get("appsecret") or ""),
            account_no=str(payload.get("account_no") or ""),
            base_url=str(payload.get("base_url") or "https://openapi.koreainvestment.com:9443"),
            is_paper=bool(payload.get("is_paper", False)),
        )

    def summary(self) -> dict[str, Any]:
        return self.load().masked()
