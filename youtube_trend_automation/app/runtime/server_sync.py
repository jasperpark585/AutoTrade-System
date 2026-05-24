from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from dotenv import dotenv_values

from app.studio.channel_paths import resolve_youtube_token_file


SERVICE_NAME = "youtube-trend-bot"
STUDIO_SERVICE_NAME = "youtube-trend-studio"
SERVER_RUNTIME_CACHE_NAME = "server_runtime_status_cache.json"
SERVER_SYNC_STATE_NAME = "server_sync_state.json"


@dataclass(slots=True)
class RemoteDeployTarget:
    host: str = ""
    user: str = "ubuntu"
    path: str = "/opt/youtube-trend-automation"
    key_file: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.path)


def _runtime_file(project_root: Path, name: str) -> Path:
    path = project_root / "data" / "runtime" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_json_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_server_sync_state(project_root: Path) -> dict[str, object]:
    return _read_json_file(_runtime_file(project_root, SERVER_SYNC_STATE_NAME))


def _write_server_sync_state(project_root: Path, payload: dict[str, object]) -> None:
    state = dict(payload)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_file(_runtime_file(project_root, SERVER_SYNC_STATE_NAME), state)


def _mark_pending_server_sync(project_root: Path, message: str) -> None:
    settings_path = project_root / "data" / "studio_settings.json"
    _write_server_sync_state(
        project_root,
        {
            "status": "pending",
            "message": message,
            "settings_path": str(settings_path),
            "settings_mtime": settings_path.stat().st_mtime if settings_path.exists() else None,
        },
    )


def _mark_server_sync_success(project_root: Path, message: str) -> None:
    _write_server_sync_state(
        project_root,
        {
            "status": "success",
            "message": message,
        },
    )


def _cache_runtime_status(project_root: Path, payload: dict[str, object]) -> None:
    cached = dict(payload)
    cached["cached_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json_file(_runtime_file(project_root, SERVER_RUNTIME_CACHE_NAME), cached)


def _load_cached_runtime_status(project_root: Path) -> dict[str, object]:
    return _read_json_file(_runtime_file(project_root, SERVER_RUNTIME_CACHE_NAME))


def load_remote_target(project_root: Path) -> RemoteDeployTarget:
    """Load SSH deployment settings from .env and the current environment."""

    env_file = project_root / ".env"
    values = dotenv_values(env_file) if env_file.exists() else {}
    raw_values = _read_env_pairs(env_file)
    return RemoteDeployTarget(
        host=_env_or_file_value("SSH_DEPLOY_HOST", values, raw_values),
        user=_env_or_file_value("SSH_DEPLOY_USER", values, raw_values, default="ubuntu") or "ubuntu",
        path=_env_or_file_value("SSH_DEPLOY_PATH", values, raw_values, default="/opt/youtube-trend-automation").strip()
        or "/opt/youtube-trend-automation",
        key_file=_env_or_file_value("SSH_KEY_FILE", values, raw_values),
    )


def collect_sync_files(project_root: Path) -> list[Path]:
    """Return project files that must reach the server for settings changes to take effect."""

    settings_file = project_root / "data" / "studio_settings.json"
    files = [settings_file]
    if not settings_file.exists():
        return files

    payload = json.loads(settings_file.read_text(encoding="utf-8-sig"))
    for channel in payload.get("channels", []):
        channel_id = str(channel.get("id", "")).strip()
        for key in ("manual_background_path", "manual_thumbnail_path", "youtube_client_secrets_file"):
            candidate = _project_file_if_safe(project_root, str(channel.get(key, "")).strip())
            if candidate is not None and candidate.exists() and candidate not in files:
                files.append(candidate)

        token_value = str(channel.get("youtube_token_file", "")).strip()
        if channel_id:
            token_candidate = Path(resolve_youtube_token_file(token_value, channel_id, project_root))
            if token_candidate.exists() and token_candidate not in files:
                files.append(token_candidate)
    return files


def _env_or_file_value(
    key: str,
    parsed_values: dict[str, str | None],
    raw_values: dict[str, str],
    *,
    default: str = "",
) -> str:
    return str(os.getenv(key) or parsed_values.get(key) or raw_values.get(key) or default).strip()


def _read_env_pairs(env_file: Path) -> dict[str, str]:
    if not env_file.exists():
        return {}

    pairs: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def _project_file_if_safe(project_root: Path, raw_path: str) -> Path | None:
    if not raw_path:
        return None

    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (project_root / candidate).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError:
        return None
    return candidate


def _is_local_runtime_target(project_root: Path, target: RemoteDeployTarget) -> bool:
    raw_target_path = str(target.path or "").strip()
    if not raw_target_path:
        return False
    try:
        return project_root.resolve() == Path(raw_target_path).expanduser().resolve()
    except OSError:
        return False


def _local_service_status(service_name: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", service_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )
    return (result.stdout or result.stderr).strip() or "unknown"


def _ensure_local_service_running(service_name: str) -> tuple[str, str]:
    status = _local_service_status(service_name)
    if status == "active":
        return status, ""

    restart = subprocess.run(
        ["sudo", "systemctl", "restart", service_name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    refreshed = _local_service_status(service_name)
    message = restart.stderr.strip() or restart.stdout.strip()
    return refreshed, message


def _refresh_local_scheduler_state(project_root: Path) -> None:
    from app.scheduler.service import _load_scheduler_state, _refresh_channel_states, _save_scheduler_state
    from app.studio.store import StudioSettingsStore

    settings = StudioSettingsStore(project_root / "data" / "studio_settings.json").load()
    state = _load_scheduler_state(project_root)
    refreshed = _refresh_channel_states(settings.channels, state, now=datetime.now())
    refreshed["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
    _save_scheduler_state(project_root, refreshed)


def _collect_local_runtime_payload(project_root: Path) -> dict[str, object]:
    payload: dict[str, object] = {}
    payload["service_status"] = _local_service_status(SERVICE_NAME)
    payload["service_ok"] = payload["service_status"] == "active"
    payload["studio_service_status"] = _local_service_status(STUDIO_SERVICE_NAME)
    payload["studio_service_ok"] = payload["studio_service_status"] == "active"

    for name, key in (
        ("data/runtime/scheduler_state.json", "scheduler_state"),
        ("data/runtime/next_run_monitor.json", "next_run_monitor"),
        ("data/runtime/studio_access.json", "studio_access"),
    ):
        path = project_root / name
        if not path.exists():
            continue
        try:
            payload[key] = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # pragma: no cover - defensive
            payload[f"{key}_error"] = str(exc)

    metadata_dir = project_root / "outputs" / "metadata"
    if metadata_dir.exists():
        latest = sorted(metadata_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        latest_by_channel: dict[str, dict[str, object]] = {}
        for latest_file in latest:
            try:
                latest_payload = json.loads(latest_file.read_text(encoding="utf-8-sig"))
            except Exception as exc:  # pragma: no cover - defensive
                payload.setdefault("latest_metadata_error", str(exc))
                continue

            run_payload = {
                "path": str(latest_payload.get("artifacts", {}).get("upload", {}).get("path", "")),
                "metadata_path": str(latest_file),
                "metadata_modified_at": datetime.fromtimestamp(latest_file.stat().st_mtime).isoformat(timespec="seconds"),
                "run_id": latest_payload.get("run_id", ""),
                "created_at": latest_payload.get("created_at", ""),
                "topic": latest_payload.get("topic", {}).get("representative_title", ""),
                "upload": latest_payload.get("artifacts", {}).get("upload", {}),
            }
            if "latest_metadata_path" not in payload:
                payload["latest_metadata_path"] = run_payload["metadata_path"]
                payload["latest_metadata_modified_at"] = run_payload["metadata_modified_at"]
                payload["latest_run_id"] = run_payload["run_id"]
                payload["latest_created_at"] = run_payload["created_at"]
                payload["latest_topic"] = run_payload["topic"]
                payload["latest_upload"] = run_payload["upload"]

            channel_id = str(latest_payload.get("channel", {}).get("id", "")).strip()
            if channel_id and channel_id not in latest_by_channel:
                latest_by_channel[channel_id] = run_payload

        if latest_by_channel:
            payload["latest_runs_by_channel"] = latest_by_channel

    return payload


def _arm_next_run_monitor_local(project_root: Path, next_due_at: str) -> dict[str, object]:
    deadline = _parse_datetime(next_due_at)
    if deadline is None:
        return {
            "status": "failed",
            "message": "The next automatic upload time could not be parsed.",
        }

    command = """
from datetime import datetime, timedelta
import json
from pathlib import Path
import time

root = Path(%(root)r)
state_path = root / 'data/runtime/scheduler_state.json'
report_path = root / 'data/runtime/next_run_monitor.json'
target_due_at = %(next_due_at)r
deadline = datetime.fromisoformat(%(deadline)r)

def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        return {}

def parse_dt(value: str):
    value = str(value or '').strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%%Y-%%m-%%d %%H:%%M:%%S', '%%Y-%%m-%%d %%H:%%M:%%S.%%f'):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None

def latest_run_payload() -> dict:
    metadata_dir = root / 'outputs/metadata'
    if not metadata_dir.exists():
        return {}
    files = sorted(metadata_dir.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return {}
    try:
        payload = json.loads(files[0].read_text(encoding='utf-8-sig'))
    except Exception:
        return {}
    return {
        'path': str(files[0]),
        'topic': payload.get('topic', {}).get('representative_title', ''),
        'created_at': payload.get('created_at', ''),
        'upload': payload.get('artifacts', {}).get('upload', {}),
    }

report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(
    json.dumps(
        {
            'status': 'armed',
            'armed_at': datetime.now().isoformat(timespec='seconds'),
            'target_due_at': target_due_at,
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding='utf-8',
)

target_due = parse_dt(target_due_at)
while datetime.now() <= deadline:
    state = read_json(state_path)
    anchor = parse_dt(str(state.get('last_anchor_at', '')))
    status = str(state.get('last_status', ''))
    if target_due and anchor and anchor >= target_due and status not in ('', 'running', 'inferred', 'bootstrapped'):
        report = {
            'status': 'completed',
            'completed_at': datetime.now().isoformat(timespec='seconds'),
            'target_due_at': target_due_at,
            'scheduler_state': state,
            'latest_run': latest_run_payload(),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        raise SystemExit
    time.sleep(20)

report_path.write_text(
    json.dumps(
        {
            'status': 'timeout',
            'completed_at': datetime.now().isoformat(timespec='seconds'),
            'target_due_at': target_due_at,
            'scheduler_state': read_json(state_path),
            'latest_run': latest_run_payload(),
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding='utf-8',
)
""" % {
        "root": str(project_root),
        "next_due_at": next_due_at,
        "deadline": (deadline + timedelta(hours=1)).isoformat(timespec="seconds"),
    }

    subprocess.Popen(
        [sys.executable, "-c", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(project_root),
        start_new_session=True,
    )
    return {
        "status": "success",
        "message": f"Next automatic upload monitor armed for {next_due_at}.",
        "next_due_at": next_due_at,
    }


def sync_server_settings(project_root: Path) -> dict[str, str]:
    """Upload the current Studio settings to the server and keep the server scheduler alive."""

    target = load_remote_target(project_root)
    if not target.is_configured:
        return {
            "status": "skipped",
            "message": "SSH deployment settings are not configured; server sync skipped.",
        }

    files = [path for path in collect_sync_files(project_root) if path.exists()]
    if not files:
        return {
            "status": "failed",
            "message": "No settings file was found to sync to the server.",
        }

    if _is_local_runtime_target(project_root, target):
        try:
            _refresh_local_scheduler_state(project_root)
            bot_status, bot_message = _ensure_local_service_running(SERVICE_NAME)
            studio_status = _local_service_status(STUDIO_SERVICE_NAME)
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "status": "failed",
                "message": f"Failed to apply settings on the server runtime directly: {exc}",
            }
        status_note = f"{SERVICE_NAME}={bot_status}, {STUDIO_SERVICE_NAME}={studio_status}"
        detail = f" ({bot_message})" if bot_message else ""
        success_message = f"Server runtime settings applied locally. {status_note}{detail}"
        _mark_server_sync_success(project_root, success_message)
        return {
            "status": "success" if bot_status == "active" else "failed",
            "message": success_message,
        }

    ssh_args = _ssh_base_args(target)
    remote_dirs = sorted(
        {
            _remote_dir_for(project_root, target.path, file_path)
            for file_path in files
        }
    )

    mkdir_command = " && ".join(f"mkdir -p {shlex.quote(path)}" for path in remote_dirs)
    mkdir_result = subprocess.run(
        ssh_args + [f"{target.user}@{target.host}", mkdir_command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    if mkdir_result.returncode != 0:
        message = mkdir_result.stderr.strip() or mkdir_result.stdout.strip() or "Failed to prepare server directories."
        _mark_pending_server_sync(project_root, message)
        return {
            "status": "pending",
            "message": f"Local settings were saved, but server sync is pending. {message}",
        }

    scp_args = _scp_base_args(target)
    for file_path in files:
        remote_file = _remote_file_for(project_root, target.path, file_path)
        result = subprocess.run(
            scp_args + [str(file_path), f"{target.user}@{target.host}:{remote_file}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=300,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or f"Failed to upload {file_path.name}."
            _mark_pending_server_sync(project_root, message)
            return {
                "status": "pending",
                "message": f"Local settings were saved, but server sync is pending. {message}",
            }

    service_state_result = subprocess.run(
        ssh_args
        + [
            f"{target.user}@{target.host}",
            (
                f"systemctl is-active {SERVICE_NAME} >/dev/null 2>&1 || sudo systemctl restart {SERVICE_NAME}; "
                f"sudo systemctl restart {STUDIO_SERVICE_NAME} >/dev/null 2>&1 || true; "
                f"BOT_STATUS=$(systemctl is-active {SERVICE_NAME} || true); "
                f"STUDIO_STATUS=$(systemctl is-active {STUDIO_SERVICE_NAME} || true); "
                f"printf 'bot=%s\\nstudio=%s\\n' \"$BOT_STATUS\" \"$STUDIO_STATUS\"; "
                f"[ \"$BOT_STATUS\" = \"active\" ] && [ \"$STUDIO_STATUS\" = \"active\" ]"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    if service_state_result.returncode != 0:
        message = (
            service_state_result.stderr.strip()
            or service_state_result.stdout.strip()
            or "Server service state check failed."
        )
        _mark_pending_server_sync(project_root, message)
        return {
            "status": "pending",
            "message": f"Local settings were saved, but server sync is pending. {message}",
        }

    success_message = (
        f"Server settings synced to {target.host}. "
        f"{SERVICE_NAME} and {STUDIO_SERVICE_NAME} are synced with the latest settings."
    )
    _mark_server_sync_success(project_root, success_message)
    return {
        "status": "success",
        "message": success_message,
    }


def pull_server_settings(project_root: Path) -> dict[str, str]:
    """Pull the current server settings file back to the local runtime when reachable."""

    target = load_remote_target(project_root)
    if not target.is_configured:
        return {
            "status": "skipped",
            "message": "SSH deployment settings are not configured; server settings pull skipped.",
        }

    if _is_local_runtime_target(project_root, target):
        return {
            "status": "success",
            "message": "Local Studio is already using the server runtime settings directly.",
        }

    sync_state = read_server_sync_state(project_root)
    if str(sync_state.get("status") or "").strip() == "pending":
        return {
            "status": "skipped",
            "message": "Skipped pulling server settings because local changes are waiting to sync to the server.",
        }

    command = f"""
cd {shlex.quote(target.path)}
python3 - <<'PY'
import json
from pathlib import Path

settings_path = Path('data/studio_settings.json')
payload = {{
    'settings_exists': settings_path.exists(),
    'settings_text': settings_path.read_text(encoding='utf-8-sig') if settings_path.exists() else '',
}}
print(json.dumps(payload, ensure_ascii=False))
PY
""".strip()

    result = _run_ssh(target, command, timeout=120)
    if result.returncode != 0:
        return {
            "status": "failed",
            "message": result.stderr.strip() or result.stdout.strip() or "Failed to pull server settings.",
        }

    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "failed",
            "message": f"Server settings response was not valid JSON: {exc}",
        }

    if not payload.get("settings_exists"):
        return {
            "status": "failed",
            "message": "Server settings file was not found on the remote runtime.",
        }

    remote_text = str(payload.get("settings_text") or "")
    settings_path = project_root / "data" / "studio_settings.json"
    local_text = settings_path.read_text(encoding="utf-8-sig") if settings_path.exists() else ""
    if remote_text == local_text:
        return {
            "status": "success",
            "message": "Local Studio is already using the latest server settings.",
        }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(remote_text, encoding="utf-8")
    return {
        "status": "success",
        "message": "Pulled the latest server settings into the local Studio runtime.",
    }


def reconcile_server_settings(project_root: Path) -> dict[str, str]:
    """Keep local/server Studio settings aligned with pending local changes taking priority."""

    sync_state = read_server_sync_state(project_root)
    if str(sync_state.get("status") or "").strip() == "pending":
        return sync_server_settings(project_root)
    return pull_server_settings(project_root)


def fetch_server_runtime_status(project_root: Path) -> dict[str, object]:
    """Read the current server automation status over SSH."""

    target = load_remote_target(project_root)
    if not target.is_configured:
        return {
            "status": "skipped",
            "message": "SSH deployment settings are not configured; server status unavailable.",
        }

    if _is_local_runtime_target(project_root, target):
        try:
            _refresh_local_scheduler_state(project_root)
            payload = _collect_local_runtime_payload(project_root)
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "status": "failed",
                "message": f"Failed to read server runtime status locally: {exc}",
            }
        enriched = _enrich_runtime_status(payload)
        enriched["status"] = "success"
        _cache_runtime_status(project_root, enriched)
        return enriched

    command = f"""
cd {shlex.quote(target.path)}
python3 - <<'PY'
from datetime import datetime
import json
from pathlib import Path
import subprocess

root = Path('.')
payload = {{}}

service = subprocess.run(['systemctl', 'is-active', '{SERVICE_NAME}'], capture_output=True, text=True, check=False)
payload['service_status'] = (service.stdout or service.stderr).strip() or 'unknown'
payload['service_ok'] = service.returncode == 0

studio_service = subprocess.run(['systemctl', 'is-active', '{STUDIO_SERVICE_NAME}'], capture_output=True, text=True, check=False)
payload['studio_service_status'] = (studio_service.stdout or studio_service.stderr).strip() or 'unknown'
payload['studio_service_ok'] = studio_service.returncode == 0

for name, key in (
    ('data/runtime/scheduler_state.json', 'scheduler_state'),
    ('data/runtime/next_run_monitor.json', 'next_run_monitor'),
    ('data/runtime/studio_access.json', 'studio_access'),
):
    path = root / name
    if not path.exists():
        continue
    try:
        payload[key] = json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception as exc:
        payload[f'{{key}}_error'] = str(exc)

metadata_dir = root / 'outputs' / 'metadata'
if metadata_dir.exists():
    latest = sorted(metadata_dir.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
    latest_by_channel = {{}}
    for latest_file in latest:
        try:
            latest_payload = json.loads(latest_file.read_text(encoding='utf-8-sig'))
        except Exception as exc:
            if 'latest_metadata_error' not in payload:
                payload['latest_metadata_error'] = str(exc)
            continue

        run_payload = {{
            'path': str(latest_payload.get('artifacts', {{}}).get('upload', {{}}).get('path', '')),
            'metadata_path': str(latest_file),
            'metadata_modified_at': datetime.fromtimestamp(latest_file.stat().st_mtime).isoformat(timespec='seconds'),
            'run_id': latest_payload.get('run_id', ''),
            'created_at': latest_payload.get('created_at', ''),
            'topic': latest_payload.get('topic', {{}}).get('representative_title', ''),
            'upload': latest_payload.get('artifacts', {{}}).get('upload', {{}}),
        }}

        if 'latest_metadata_path' not in payload:
            payload['latest_metadata_path'] = run_payload['metadata_path']
            payload['latest_metadata_modified_at'] = run_payload['metadata_modified_at']
            payload['latest_run_id'] = run_payload['run_id']
            payload['latest_created_at'] = run_payload['created_at']
            payload['latest_topic'] = run_payload['topic']
            payload['latest_upload'] = run_payload['upload']

        channel_id = str(latest_payload.get('channel', {{}}).get('id', '')).strip()
        if channel_id and channel_id not in latest_by_channel:
            latest_by_channel[channel_id] = run_payload

    if latest_by_channel:
        payload['latest_runs_by_channel'] = latest_by_channel

print(json.dumps(payload, ensure_ascii=False))
PY
""".strip()

    result = _run_ssh(target, command, timeout=120)
    if result.returncode != 0:
        cached = _load_cached_runtime_status(project_root)
        if cached:
            cached_payload = dict(cached)
            cached_payload["status"] = "cached"
            cached_payload["message"] = result.stderr.strip() or result.stdout.strip() or "Failed to fetch live server status."
            return cached_payload
        return {
            "status": "failed",
            "message": result.stderr.strip() or result.stdout.strip() or "Failed to fetch server status.",
        }

    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "failed",
            "message": f"Server status response was not valid JSON: {exc}",
        }

    enriched = _enrich_runtime_status(payload)
    enriched["status"] = "success"
    _cache_runtime_status(project_root, enriched)
    return enriched


def arm_next_run_monitor(project_root: Path) -> dict[str, object]:
    """Start a background server-side watcher for the next scheduled run."""

    runtime_status = fetch_server_runtime_status(project_root)
    if runtime_status.get("status") != "success":
        return runtime_status

    next_due_at = str(runtime_status.get("next_due_at") or "").strip()
    if not next_due_at:
        return {
            "status": "failed",
            "message": "Could not determine the next automatic upload time.",
        }

    target = load_remote_target(project_root)
    deadline = _parse_datetime(next_due_at)
    if deadline is None:
        return {
            "status": "failed",
            "message": "The next automatic upload time could not be parsed.",
        }

    if _is_local_runtime_target(project_root, target):
        return _arm_next_run_monitor_local(project_root, next_due_at)

    command = f"""
cd {shlex.quote(target.path)}
nohup ./.venv/bin/python - <<'PY' >/dev/null 2>&1 &
from datetime import datetime, timedelta
import json
from pathlib import Path
import time

root = Path('.')
state_path = root / 'data/runtime/scheduler_state.json'
report_path = root / 'data/runtime/next_run_monitor.json'
target_due_at = {next_due_at!r}
deadline = datetime.fromisoformat({(deadline + timedelta(hours=1)).isoformat(timespec='seconds')!r})


def read_json(path: Path) -> dict:
    if not path.exists():
        return {{}}
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        return {{}}


def parse_dt(value: str) -> datetime | None:
    value = str(value or '').strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def latest_run_payload() -> dict:
    metadata_dir = root / 'outputs/metadata'
    if not metadata_dir.exists():
        return {{}}
    files = sorted(metadata_dir.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return {{}}
    try:
        payload = json.loads(files[0].read_text(encoding='utf-8-sig'))
    except Exception:
        return {{}}
    return {{
        'path': str(files[0]),
        'topic': payload.get('topic', {{}}).get('representative_title', ''),
        'created_at': payload.get('created_at', ''),
        'upload': payload.get('artifacts', {{}}).get('upload', {{}}),
    }}


report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(
    json.dumps(
        {{
            'status': 'armed',
            'armed_at': datetime.now().isoformat(timespec='seconds'),
            'target_due_at': target_due_at,
        }},
        ensure_ascii=False,
        indent=2,
    ),
    encoding='utf-8',
)

target_due = parse_dt(target_due_at)
while datetime.now() <= deadline:
    state = read_json(state_path)
    anchor = parse_dt(str(state.get('last_anchor_at', '')))
    status = str(state.get('last_status', ''))
    if target_due and anchor and anchor >= target_due and status not in ('', 'running', 'inferred', 'bootstrapped'):
        report = {{
            'status': 'completed',
            'completed_at': datetime.now().isoformat(timespec='seconds'),
            'target_due_at': target_due_at,
            'scheduler_state': state,
            'latest_run': latest_run_payload(),
        }}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        raise SystemExit
    time.sleep(20)

report_path.write_text(
    json.dumps(
        {{
            'status': 'timeout',
            'completed_at': datetime.now().isoformat(timespec='seconds'),
            'target_due_at': target_due_at,
            'scheduler_state': read_json(state_path),
            'latest_run': latest_run_payload(),
        }},
        ensure_ascii=False,
        indent=2,
    ),
    encoding='utf-8',
)
PY
""".strip()

    result = _run_ssh(target, command, timeout=120)
    if result.returncode != 0:
        return {
            "status": "failed",
            "message": result.stderr.strip() or result.stdout.strip() or "Failed to arm next-run monitor.",
        }

    return {
        "status": "success",
        "message": f"Next automatic upload monitor armed for {next_due_at}.",
        "next_due_at": next_due_at,
    }


def _ssh_base_args(target: RemoteDeployTarget) -> list[str]:
    args = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=2",
    ]
    if target.key_file:
        args += ["-i", target.key_file]
    return args


def _scp_base_args(target: RemoteDeployTarget) -> list[str]:
    args = [
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=20",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=2",
    ]
    if target.key_file:
        args += ["-i", target.key_file]
    return args


def _remote_dir_for(project_root: Path, remote_root: str, file_path: Path) -> str:
    relative = file_path.resolve().relative_to(project_root.resolve())
    parent = relative.parent.as_posix()
    return remote_root.rstrip("/") if not parent else f"{remote_root.rstrip('/')}/{parent}"


def _remote_file_for(project_root: Path, remote_root: str, file_path: Path) -> str:
    relative = file_path.resolve().relative_to(project_root.resolve()).as_posix()
    return f"{remote_root.rstrip('/')}/{relative}"


def _run_ssh(target: RemoteDeployTarget, command: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _ssh_base_args(target) + [f"{target.user}@{target.host}", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )


def _enrich_runtime_status(payload: dict[str, object], *, now: datetime | None = None) -> dict[str, object]:
    enriched = dict(payload)
    current_time = now or datetime.now()
    scheduler_state = payload.get("scheduler_state") if isinstance(payload.get("scheduler_state"), dict) else {}
    if isinstance(scheduler_state, dict):
        next_due_at = str(scheduler_state.get("next_due_at") or "").strip()
        if not next_due_at:
            anchor = _parse_datetime(str(scheduler_state.get("last_anchor_at") or ""))
            hours = int(scheduler_state.get("schedule_hours") or 0)
            if anchor is not None and hours > 0:
                next_due_at = (anchor + timedelta(hours=hours)).isoformat(timespec="seconds")
        enriched["next_due_at"] = next_due_at
        next_due = _parse_datetime(next_due_at)
        if next_due is not None:
            remaining = int((next_due - current_time).total_seconds())
            enriched["next_due_in_seconds"] = remaining
            enriched["next_due_in_human"] = _format_duration(remaining)
        else:
            enriched["next_due_in_seconds"] = None
            enriched["next_due_in_human"] = "계산 불가"
    else:
        enriched["next_due_at"] = ""
        enriched["next_due_in_seconds"] = None
        enriched["next_due_in_human"] = "계산 불가"

    monitor = payload.get("next_run_monitor") if isinstance(payload.get("next_run_monitor"), dict) else {}
    if isinstance(monitor, dict):
        enriched["monitor_status"] = str(monitor.get("status", "")).strip()
    else:
        enriched["monitor_status"] = ""
    studio_access = payload.get("studio_access") if isinstance(payload.get("studio_access"), dict) else {}
    if isinstance(studio_access, dict):
        enriched["studio_public_url"] = str(studio_access.get("public_url") or "").strip()
        enriched["studio_local_url"] = str(studio_access.get("local_url") or "").strip()
        enriched["studio_lan_url"] = str(studio_access.get("lan_url") or "").strip()
        enriched["studio_tunnel_status"] = str(studio_access.get("tunnel_status") or "").strip()
        enriched["studio_tunnel_error"] = str(studio_access.get("tunnel_error") or "").strip()
    else:
        enriched["studio_public_url"] = ""
        enriched["studio_local_url"] = ""
        enriched["studio_lan_url"] = ""
        enriched["studio_tunnel_status"] = ""
        enriched["studio_tunnel_error"] = ""
    return enriched


def _enrich_runtime_status(payload: dict[str, object], *, now: datetime | None = None) -> dict[str, object]:
    enriched = dict(payload)
    current_time = now or datetime.now()
    scheduler_state = payload.get("scheduler_state") if isinstance(payload.get("scheduler_state"), dict) else {}
    if isinstance(scheduler_state, dict):
        next_due_at = str(scheduler_state.get("next_due_at") or "").strip()
        channel_states = scheduler_state.get("channel_states") if isinstance(scheduler_state.get("channel_states"), dict) else {}
        channel_schedules: list[dict[str, object]] = []

        if isinstance(channel_states, dict):
            next_due_candidates: list[str] = []
            for channel_id, entry in channel_states.items():
                if not isinstance(entry, dict):
                    continue
                channel_next_due = str(entry.get("next_due_at") or "").strip()
                if channel_next_due:
                    next_due_candidates.append(channel_next_due)
                schedule_enabled = bool(entry.get("schedule_enabled", True))
                schedule_mode = str(entry.get("schedule_mode") or "interval")
                daily_upload_times = entry.get("daily_upload_times", []) if isinstance(entry.get("daily_upload_times"), list) else []
                legacy_daily_time = str(entry.get("daily_upload_time") or "").strip()
                if not daily_upload_times and legacy_daily_time:
                    daily_upload_times = [legacy_daily_time]
                if not schedule_enabled:
                    schedule_label = "disabled"
                    channel_next_due = ""
                elif schedule_mode == "daily" and daily_upload_times:
                    schedule_label = f"daily {', '.join(str(item) for item in daily_upload_times)}"
                else:
                    schedule_label = f"every {int(entry.get('schedule_interval_hours') or 6)}h"
                channel_schedules.append(
                    {
                        "channel_id": channel_id,
                        "display_name": str(entry.get("display_name") or channel_id),
                        "schedule_label": schedule_label,
                        "next_due_at": channel_next_due,
                        "last_status": str(entry.get("last_status") or ""),
                        "last_completed_at": str(entry.get("last_completed_at") or ""),
                    }
                )
            if not next_due_at and next_due_candidates:
                next_due_at = min(next_due_candidates)

        if not next_due_at:
            anchor = _parse_datetime(str(scheduler_state.get("last_anchor_at") or ""))
            hours = int(scheduler_state.get("schedule_hours") or 0)
            if anchor is not None and hours > 0:
                next_due_at = (anchor + timedelta(hours=hours)).isoformat(timespec="seconds")

        enriched["next_due_at"] = next_due_at
        next_due = _parse_datetime(next_due_at)
        if next_due is not None:
            remaining = int((next_due - current_time).total_seconds())
            enriched["next_due_in_seconds"] = remaining
            enriched["next_due_in_human"] = _format_duration(remaining)
        else:
            enriched["next_due_in_seconds"] = None
            enriched["next_due_in_human"] = "cannot calculate"
        enriched["channel_schedules"] = channel_schedules
    else:
        enriched["next_due_at"] = ""
        enriched["next_due_in_seconds"] = None
        enriched["next_due_in_human"] = "cannot calculate"
        enriched["channel_schedules"] = []

    monitor = payload.get("next_run_monitor") if isinstance(payload.get("next_run_monitor"), dict) else {}
    if isinstance(monitor, dict):
        enriched["monitor_status"] = str(monitor.get("status", "")).strip()
    else:
        enriched["monitor_status"] = ""
    studio_access = payload.get("studio_access") if isinstance(payload.get("studio_access"), dict) else {}
    if isinstance(studio_access, dict):
        enriched["studio_public_url"] = str(studio_access.get("public_url") or "").strip()
        enriched["studio_local_url"] = str(studio_access.get("local_url") or "").strip()
        enriched["studio_lan_url"] = str(studio_access.get("lan_url") or "").strip()
        enriched["studio_tunnel_status"] = str(studio_access.get("tunnel_status") or "").strip()
        enriched["studio_tunnel_error"] = str(studio_access.get("tunnel_error") or "").strip()
    else:
        enriched["studio_public_url"] = ""
        enriched["studio_local_url"] = ""
        enriched["studio_lan_url"] = ""
        enriched["studio_tunnel_status"] = ""
        enriched["studio_tunnel_error"] = ""
    return enriched


def _parse_datetime(raw: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _format_duration(total_seconds: int | None) -> str:
    if total_seconds is None:
        return "--:--:--"
    seconds = int(total_seconds)
    if seconds <= 0:
        return "due now"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
