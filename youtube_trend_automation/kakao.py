from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from dotenv import dotenv_values
import requests

logger = logging.getLogger(__name__)

KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def _project_root(project_root: Path | None = None) -> Path:
    return (project_root or Path(__file__).resolve().parent).resolve()


def _runtime_token_path(project_root: Path | None = None) -> Path:
    root = _project_root(project_root)
    path = root / "data" / "runtime" / "kakao_oauth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_runtime_tokens(project_root: Path | None = None) -> dict[str, Any]:
    path = _runtime_token_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_runtime_tokens(project_root: Path | None, payload: dict[str, Any]) -> None:
    path = _runtime_token_path(project_root)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _dotenv_path(project_root: Path | None = None) -> Path:
    return _project_root(project_root) / ".env"


def _upsert_env_values(project_root: Path | None, updates: dict[str, str]) -> None:
    env_path = _dotenv_path(project_root)
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    new_lines: list[str] = []
    for line in existing_lines:
        if "=" not in line or line.lstrip().startswith("#"):
            new_lines.append(line)
            continue
        key, _value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in remaining:
            new_lines.append(f"{normalized_key}={remaining.pop(normalized_key)}")
        else:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def _dotenv_values(project_root: Path | None = None) -> dict[str, Any]:
    root = _project_root(project_root)
    candidates = [root / ".env", Path.cwd() / ".env"]
    merged: dict[str, Any] = {}
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not candidate.exists():
            continue
        seen.add(resolved)
        try:
            merged.update({key: value for key, value in dotenv_values(candidate).items() if value})
        except Exception:
            continue
    return merged


def _pick_config_value(*keys: str, project_root: Path | None = None, runtime_payload: dict[str, Any] | None = None) -> str:
    runtime_payload = runtime_payload or {}
    for key in keys:
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
        runtime_value = str(runtime_payload.get(key) or "").strip()
        if runtime_value:
            return runtime_value
        dotenv_value = str(_dotenv_values(project_root).get(key) or "").strip()
        if dotenv_value:
            return dotenv_value
    return ""


class KakaoNotifier:
    def __init__(self, token: str | None = None, *, project_root: Path | None = None):
        self.project_root = _project_root(project_root)
        runtime_payload = _read_runtime_tokens(self.project_root)
        self.rest_api_key = _pick_config_value("KAKAO_REST_API_KEY", project_root=self.project_root, runtime_payload=runtime_payload)
        self.client_secret = _pick_config_value("KAKAO_CLIENT_SECRET", project_root=self.project_root, runtime_payload=runtime_payload)
        self.redirect_uri = _pick_config_value("KAKAO_REDIRECT_URI", project_root=self.project_root, runtime_payload=runtime_payload)
        self.refresh_token = _pick_config_value("KAKAO_REFRESH_TOKEN", project_root=self.project_root, runtime_payload=runtime_payload)
        self.token = (
            (token or "").strip()
            or _pick_config_value("YTA_KAKAO_ACCESS_TOKEN", "KAKAO_ACCESS_TOKEN", project_root=self.project_root, runtime_payload=runtime_payload)
        )

    def authorization_url(self) -> str:
        if not self.rest_api_key or not self.redirect_uri:
            raise ValueError("KAKAO_REST_API_KEY and KAKAO_REDIRECT_URI must be configured.")
        query = urlencode(
            {
                "client_id": self.rest_api_key,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "talk_message",
            }
        )
        return f"https://kauth.kakao.com/oauth/authorize?{query}"

    def exchange_authorization_code(self, code: str) -> dict[str, Any]:
        if not self.rest_api_key or not self.redirect_uri:
            raise ValueError("KAKAO_REST_API_KEY and KAKAO_REDIRECT_URI must be configured.")
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError("Authorization code is required.")

        payload = {
            "grant_type": "authorization_code",
            "client_id": self.rest_api_key,
            "redirect_uri": self.redirect_uri,
            "code": normalized_code,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        response = requests.post(KAKAO_TOKEN_URL, data=payload, timeout=10)
        if response.status_code != 200 and self.client_secret and self._is_bad_client_credentials(response.text):
            fallback_payload = dict(payload)
            fallback_payload.pop("client_secret", None)
            response = requests.post(KAKAO_TOKEN_URL, data=fallback_payload, timeout=10)
        if response.status_code != 200:
            raise RuntimeError(f"Kakao token exchange failed: {response.text}")
        data = response.json()
        access_token = str(data.get("access_token") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"Kakao token exchange returned no access token: {data}")

        self.token = access_token
        if refresh_token:
            self.refresh_token = refresh_token

        saved_payload = {
            "KAKAO_ACCESS_TOKEN": self.token,
            "KAKAO_REFRESH_TOKEN": self.refresh_token,
            "KAKAO_REST_API_KEY": self.rest_api_key,
            "KAKAO_REDIRECT_URI": self.redirect_uri,
        }
        _write_runtime_tokens(self.project_root, saved_payload)
        _upsert_env_values(self.project_root, {key: value for key, value in saved_payload.items() if value})
        return data

    def run_local_auth_flow(self, *, timeout_seconds: int = 180, open_browser: bool = True) -> dict[str, Any]:
        if not self.rest_api_key or not self.redirect_uri:
            raise ValueError("KAKAO_REST_API_KEY and KAKAO_REDIRECT_URI must be configured.")

        parsed = urlparse(self.redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        callback_path = parsed.path or "/"
        received: dict[str, str] = {}
        ready = threading.Event()

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # type: ignore[override]
                current = urlparse(self.path)
                if current.path != callback_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(current.query)
                code = str((query.get("code") or [""])[0]).strip()
                error = str((query.get("error") or [""])[0]).strip()
                if code:
                    received["code"] = code
                    body = "카카오 인증이 완료되었습니다. 이 창은 닫아도 됩니다."
                    self.send_response(200)
                else:
                    received["error"] = error or "missing authorization code"
                    body = f"카카오 인증이 실패했습니다: {received['error']}"
                    self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))
                ready.set()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A003
                return

        server = ThreadingHTTPServer((host, port), CallbackHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="kakao-auth-callback", daemon=True)
        thread.start()
        try:
            if open_browser:
                webbrowser.open_new(self.authorization_url())
            if not ready.wait(timeout_seconds):
                raise TimeoutError(f"Kakao authorization timed out after {timeout_seconds} seconds.")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        if received.get("error"):
            raise RuntimeError(f"Kakao authorization returned an error: {received['error']}")
        code = str(received.get("code") or "").strip()
        if not code:
            raise RuntimeError("Kakao authorization completed without an authorization code.")
        return self.exchange_authorization_code(code)

    def send(self, message: str, *, web_url: str = "https://example.com") -> bool:
        if not self.token and not self._refresh_access_token():
            logger.info("Kakao token missing; message skipped.")
            return False

        ok, payload = self._send_memo(message, web_url=web_url)
        if ok:
            return True
        if self._is_invalid_token_payload(payload) and self._refresh_access_token():
            ok, payload = self._send_memo(message, web_url=web_url)
            if ok:
                return True
        logger.error("Kakao notify failed: %s", payload)
        return False

    def _send_memo(self, message: str, *, web_url: str) -> tuple[bool, str]:
        if not self.token:
            return False, "missing access token"
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = {
            "template_object": json.dumps(
                {
                    "object_type": "text",
                    "text": message,
                    "link": {"web_url": web_url},
                },
                ensure_ascii=False,
            )
        }
        try:
            response = requests.post(KAKAO_MEMO_URL, headers=headers, data=payload, timeout=5)
            if response.status_code == 200:
                return True, response.text
            return False, response.text
        except Exception as exc:
            return False, str(exc)

    def _refresh_access_token(self) -> bool:
        if not self.rest_api_key or not self.refresh_token:
            return False

        payload = {
            "grant_type": "refresh_token",
            "client_id": self.rest_api_key,
            "refresh_token": self.refresh_token,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        try:
            response = requests.post(KAKAO_TOKEN_URL, data=payload, timeout=5)
            if response.status_code != 200 and self.client_secret and self._is_bad_client_credentials(response.text):
                fallback_payload = dict(payload)
                fallback_payload.pop("client_secret", None)
                response = requests.post(KAKAO_TOKEN_URL, data=fallback_payload, timeout=5)
            if response.status_code != 200:
                logger.error("Kakao token refresh failed: %s", response.text)
                return False
            data = response.json()
        except Exception as exc:
            logger.error("Kakao token refresh error: %s", exc)
            return False

        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            logger.error("Kakao token refresh returned no access token: %s", data)
            return False

        self.token = access_token
        refreshed_refresh_token = str(data.get("refresh_token") or "").strip()
        if refreshed_refresh_token:
            self.refresh_token = refreshed_refresh_token

        _write_runtime_tokens(
            self.project_root,
            {
                "KAKAO_ACCESS_TOKEN": self.token,
                "KAKAO_REFRESH_TOKEN": self.refresh_token,
                "KAKAO_REST_API_KEY": self.rest_api_key,
                "KAKAO_REDIRECT_URI": self.redirect_uri,
            },
        )
        _upsert_env_values(
            self.project_root,
            {
                "KAKAO_ACCESS_TOKEN": self.token,
                "KAKAO_REFRESH_TOKEN": self.refresh_token,
                "KAKAO_REST_API_KEY": self.rest_api_key,
                "KAKAO_REDIRECT_URI": self.redirect_uri,
            },
        )
        return True

    @staticmethod
    def _is_invalid_token_payload(payload: str) -> bool:
        lowered = payload.lower()
        return "this access token does not exist" in lowered or '"code":-401' in lowered

    @staticmethod
    def _is_bad_client_credentials(payload: str) -> bool:
        lowered = payload.lower()
        return "bad client credentials" in lowered or "koe010" in lowered
