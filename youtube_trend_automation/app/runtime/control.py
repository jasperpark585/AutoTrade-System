from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
import threading
from typing import Any

from kakao import KakaoNotifier


STUDIO_LOCK_TTL_SECONDS = 90
SCHEDULER_PAUSE_SOURCES = {"studio"}
LOGGER = logging.getLogger(__name__)


def runtime_dir(project_root: Path) -> Path:
    path = project_root / "data" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def studio_lock_path(project_root: Path) -> Path:
    return runtime_dir(project_root) / "studio_session.json"


def studio_access_path(project_root: Path) -> Path:
    return runtime_dir(project_root) / "studio_access.json"


def studio_access_text_path(project_root: Path) -> Path:
    return runtime_dir(project_root) / "studio_access.txt"


def studio_access_notice_path(project_root: Path) -> Path:
    return runtime_dir(project_root) / "studio_access_notice.json"


def studio_update_marker_path(project_root: Path) -> Path:
    return runtime_dir(project_root) / "studio_update_marker.json"


def _mirror_paths_from_env(name: str) -> list[Path]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip()]


def read_studio_session(project_root: Path) -> dict[str, Any]:
    path = studio_lock_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_studio_access(project_root: Path) -> dict[str, Any]:
    path = studio_access_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_studio_session(project_root: Path, *, port: int | None = None, source: str = "studio") -> None:
    payload = {
        "source": source,
        "port": port,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    studio_lock_path(project_root).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_studio_session(project_root: Path) -> None:
    path = studio_lock_path(project_root)
    if path.exists():
        path.unlink()


def write_studio_access(project_root: Path, payload: dict[str, Any]) -> None:
    path = studio_access_path(project_root)
    previous_payload = read_studio_access(project_root)
    normalized = dict(payload)
    normalized["updated_at"] = datetime.now(timezone.utc).isoformat()
    text_lines = []
    labels = {
        "local_url": "Local URL",
        "lan_url": "LAN URL",
        "public_url": "Public URL",
        "tunnel_status": "Tunnel Status",
        "tunnel_error": "Tunnel Error",
    }
    for key in ("local_url", "lan_url", "public_url", "tunnel_status", "tunnel_error"):
        value = normalized.get(key)
        if value:
            text_lines.append(f"{labels[key]}: {value}")

    json_text = json.dumps(normalized, ensure_ascii=False, indent=2)
    access_text = "\n".join(text_lines).strip()

    json_targets = [path, *_mirror_paths_from_env("YTA_STUDIO_ACCESS_JSON_MIRRORS")]
    text_targets = [studio_access_text_path(project_root), *_mirror_paths_from_env("YTA_STUDIO_ACCESS_TEXT_MIRRORS")]

    for target in json_targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json_text, encoding="utf-8")
    for target in text_targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(access_text, encoding="utf-8")

    _notify_studio_access_if_update_pending(project_root, previous_payload, normalized)


def clear_studio_access(project_root: Path) -> None:
    paths = [
        studio_access_path(project_root),
        studio_access_text_path(project_root),
        *_mirror_paths_from_env("YTA_STUDIO_ACCESS_JSON_MIRRORS"),
        *_mirror_paths_from_env("YTA_STUDIO_ACCESS_TEXT_MIRRORS"),
    ]
    for path in paths:
        if path.exists():
            path.unlink()


def _read_notice_state(project_root: Path) -> dict[str, Any]:
    path = studio_access_notice_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_notice_state(project_root: Path, payload: dict[str, Any]) -> None:
    path = studio_access_notice_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_update_marker(project_root: Path) -> dict[str, Any]:
    path = studio_update_marker_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def mark_server_update(project_root: Path, *, deploy_id: str, source: str = "deploy") -> dict[str, Any]:
    normalized_deploy_id = str(deploy_id or "").strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "deploy_id": normalized_deploy_id,
        "source": str(source or "deploy").strip() or "deploy",
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    path = studio_update_marker_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _build_studio_access_message(current_payload: dict[str, Any]) -> tuple[str, str]:
    current_url = str(current_payload.get("public_url") or "").strip()
    tunnel_mode = str(current_payload.get("tunnel_mode") or "quick").strip() or "quick"
    tunnel_status = str(current_payload.get("tunnel_status") or "active").strip() or "active"
    message = (
        "[YouTube Control Studio]\n"
        "최신 원격 접속 주소가 준비되었습니다.\n"
        f"{current_url}\n"
        f"모드: {tunnel_mode}\n"
        f"상태: {tunnel_status}"
    )
    return message, current_url


def send_current_studio_access_notice(project_root: Path, *, reason: str = "manual") -> dict[str, Any]:
    current_payload = read_studio_access(project_root)
    current_url = str(current_payload.get("public_url") or "").strip()
    if not current_url:
        return {"status": "failed", "message": "No current Studio public URL is available."}

    notifier = KakaoNotifier()
    if not notifier.token:
        return {"status": "failed", "message": "Kakao access token is not configured."}

    marker = _read_update_marker(project_root)
    message, web_url = _build_studio_access_message(current_payload)
    try:
        sent = notifier.send(message, web_url=web_url)
    except Exception as exc:  # pragma: no cover - defensive path
        LOGGER.warning("Kakao Studio URL notification failed: %s", exc)
        return {"status": "failed", "message": str(exc)}
    if not sent:
        return {"status": "failed", "message": "Kakao notification send returned false."}

    notice_payload = {
        "last_public_url": current_url,
        "tunnel_mode": str(current_payload.get("tunnel_mode") or "quick").strip() or "quick",
        "tunnel_status": str(current_payload.get("tunnel_status") or "active").strip() or "active",
        "last_notified_deploy_id": str(marker.get("deploy_id") or "").strip(),
        "reason": reason,
        "notified_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_notice_state(project_root, notice_payload)
    return {"status": "success", "public_url": current_url, "notice_state": notice_payload}


def _notify_studio_access_if_update_pending(project_root: Path, previous_payload: dict[str, Any], current_payload: dict[str, Any]) -> None:
    del previous_payload

    current_url = str(current_payload.get("public_url") or "").strip()
    if not current_url:
        return

    marker = _read_update_marker(project_root)
    deploy_id = str(marker.get("deploy_id") or "").strip()
    if not deploy_id:
        return

    state = _read_notice_state(project_root)
    if str(state.get("last_notified_deploy_id") or "").strip() == deploy_id:
        return

    notifier = KakaoNotifier()
    if not notifier.token:
        return

    tunnel_mode = str(current_payload.get("tunnel_mode") or "quick").strip() or "quick"
    tunnel_status = str(current_payload.get("tunnel_status") or "active").strip() or "active"
    message, web_url = _build_studio_access_message(current_payload)
    try:
        sent = notifier.send(message, web_url=web_url)
    except Exception as exc:  # pragma: no cover - defensive path
        LOGGER.warning("Kakao Studio URL notification failed: %s", exc)
        return
    if sent:
        _write_notice_state(
            project_root,
            {
                "last_public_url": current_url,
                "tunnel_mode": tunnel_mode,
                "tunnel_status": tunnel_status,
                "last_notified_deploy_id": deploy_id,
                "reason": "server-update",
                "notified_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            ip = sockaddr[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return ""


def is_studio_session_active(project_root: Path, *, ttl_seconds: int = STUDIO_LOCK_TTL_SECONDS) -> bool:
    payload = read_studio_session(project_root)
    updated_at = payload.get("updated_at")
    if not updated_at:
        return False
    source = str(payload.get("source") or "studio").strip().lower()
    try:
        normalized = str(updated_at).strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        last_seen = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last_seen).total_seconds()
    if age <= ttl_seconds:
        return source in SCHEDULER_PAUSE_SOURCES
    clear_studio_session(project_root)
    return False


class StudioSessionHeartbeat(AbstractContextManager["StudioSessionHeartbeat"]):
    """Keep a studio session lock alive while the UI process is running."""

    def __init__(
        self,
        project_root: Path,
        *,
        port: int | None = None,
        interval_seconds: int = 10,
        source: str = "studio",
    ) -> None:
        self.project_root = project_root
        self.port = port
        self.interval_seconds = interval_seconds
        self.source = str(source or "studio").strip() or "studio"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        write_studio_session(self.project_root, port=self.port, source=self.source)
        self._thread = threading.Thread(target=self._run, name="studio-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval_seconds)
        clear_studio_session(self.project_root)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            write_studio_session(self.project_root, port=self.port, source=self.source)

    def __enter__(self) -> StudioSessionHeartbeat:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def scheduler_pause_message(project_root: Path) -> str:
    payload = read_studio_session(project_root)
    if not payload:
        return ""
    port = payload.get("port")
    suffix = f" localhost:{port}" if port else ""
    return f"Studio UI is active{suffix}; scheduled automation is paused."
