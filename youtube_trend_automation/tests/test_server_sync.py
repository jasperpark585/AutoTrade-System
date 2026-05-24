import json
from datetime import datetime
from pathlib import Path

from app.runtime.server_sync import (
    RemoteDeployTarget,
    _enrich_runtime_status,
    _format_duration,
    _runtime_file,
    collect_sync_files,
    fetch_server_runtime_status,
    load_remote_target,
    pull_server_settings,
    read_server_sync_state,
    reconcile_server_settings,
    sync_server_settings,
)


def test_load_remote_target_reads_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SSH_DEPLOY_HOST=1.2.3.4",
                "SSH_DEPLOY_USER=ubuntu",
                "SSH_DEPLOY_PATH=/opt/youtube-trend-automation",
                r"SSH_KEY_FILE=C:\keys\bot.pem",
            ]
        ),
        encoding="utf-8",
    )

    target = load_remote_target(tmp_path)

    assert target.host == "1.2.3.4"
    assert target.user == "ubuntu"
    assert target.path == "/opt/youtube-trend-automation"
    assert target.key_file == "C:\\keys\\bot.pem"


def test_collect_sync_files_includes_manual_assets(tmp_path: Path) -> None:
    settings_dir = tmp_path / "data"
    settings_dir.mkdir(parents=True, exist_ok=True)
    asset = tmp_path / "assets" / "backgrounds" / "manual" / "scene.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"png")
    secrets_file = tmp_path / "secrets" / "client_secret.json"
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "data" / "youtube-token-news_default.json"
    token_file.write_text("{}", encoding="utf-8")

    payload = {
        "channels": [
            {
                "id": "news_default",
                "manual_background_path": "assets/backgrounds/manual/scene.png",
                "manual_thumbnail_path": "",
                "youtube_client_secrets_file": "./secrets/client_secret.json",
                "youtube_token_file": "./data/youtube-token-news_default.json",
            }
        ]
    }
    (settings_dir / "studio_settings.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    files = collect_sync_files(tmp_path)

    assert settings_dir / "studio_settings.json" in files
    assert asset in files
    assert secrets_file in files
    assert token_file in files


def test_load_remote_target_falls_back_to_raw_env_pairs_when_dotenv_values_miss(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "BROKEN_LINE_WITHOUT_EQUALS",
                "SSH_DEPLOY_HOST=9.9.9.9",
                "SSH_DEPLOY_USER=ubuntu",
                "SSH_DEPLOY_PATH=/srv/yta",
                r"SSH_KEY_FILE=C:\keys\yta.pem",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.runtime.server_sync.dotenv_values", lambda _path: {})

    target = load_remote_target(tmp_path)

    assert target.host == "9.9.9.9"
    assert target.user == "ubuntu"
    assert target.path == "/srv/yta"
    assert target.key_file == "C:\\keys\\yta.pem"


def test_enrich_runtime_status_adds_next_due_and_countdown() -> None:
    payload = {
        "scheduler_state": {
            "last_anchor_at": "2026-03-27T19:02:00",
            "schedule_hours": 6,
        }
    }

    enriched = _enrich_runtime_status(payload, now=datetime(2026, 3, 27, 20, 0, 0))

    assert enriched["next_due_at"] == "2026-03-28T01:02:00"
    assert enriched["next_due_in_seconds"] == 18120
    assert enriched["next_due_in_human"] == "05:02:00"


def test_enrich_runtime_status_formats_daily_time_slots_and_disabled_channels() -> None:
    payload = {
        "scheduler_state": {
            "channel_states": {
                "news_default": {
                    "display_name": "NewsTrend",
                    "schedule_enabled": True,
                    "schedule_mode": "daily",
                    "daily_upload_times": ["06:00", "10:00", "14:00", "19:00"],
                    "next_due_at": "2026-03-28T10:00:00",
                },
                "insight_default": {
                    "display_name": "명언이간다",
                    "schedule_enabled": False,
                    "schedule_mode": "interval",
                    "schedule_interval_hours": 6,
                    "next_due_at": "",
                },
            }
        }
    }

    enriched = _enrich_runtime_status(payload, now=datetime(2026, 3, 28, 9, 0, 0))

    schedules = {item["channel_id"]: item for item in enriched["channel_schedules"]}
    assert schedules["news_default"]["schedule_label"] == "daily 06:00, 10:00, 14:00, 19:00"
    assert schedules["insight_default"]["schedule_label"] == "disabled"


def test_enrich_runtime_status_exposes_server_studio_access() -> None:
    payload = {
        "studio_service_status": "active",
        "studio_access": {
            "public_url": "https://studio.example.com",
            "local_url": "http://127.0.0.1:8502",
            "lan_url": "http://10.0.0.10:8502",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    }

    enriched = _enrich_runtime_status(payload, now=datetime(2026, 3, 29, 21, 0, 0))

    assert enriched["studio_public_url"] == "https://studio.example.com"
    assert enriched["studio_local_url"] == "http://127.0.0.1:8502"
    assert enriched["studio_lan_url"] == "http://10.0.0.10:8502"
    assert enriched["studio_tunnel_status"] == "active"


def test_format_duration_handles_past_due() -> None:
    assert _format_duration(-1) == "due now"


def test_sync_server_settings_uses_local_runtime_without_ssh(tmp_path: Path, monkeypatch) -> None:
    settings_dir = tmp_path / "data"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "studio_settings.json").write_text(json.dumps({"channels": []}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        "app.runtime.server_sync.load_remote_target",
        lambda _project_root: RemoteDeployTarget(host="1.2.3.4", user="ubuntu", path=str(tmp_path)),
    )
    refresh_called: dict[str, bool] = {}
    monkeypatch.setattr(
        "app.runtime.server_sync._refresh_local_scheduler_state",
        lambda _project_root: refresh_called.setdefault("called", True),
    )
    monkeypatch.setattr(
        "app.runtime.server_sync._ensure_local_service_running",
        lambda _service_name: ("active", ""),
    )
    monkeypatch.setattr("app.runtime.server_sync._local_service_status", lambda _service_name: "active")

    result = sync_server_settings(tmp_path)

    assert result["status"] == "success"
    assert refresh_called["called"] is True
    assert "applied locally" in result["message"]


def test_fetch_server_runtime_status_uses_local_runtime_without_ssh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime.server_sync.load_remote_target",
        lambda _project_root: RemoteDeployTarget(host="1.2.3.4", user="ubuntu", path=str(tmp_path)),
    )
    monkeypatch.setattr("app.runtime.server_sync._refresh_local_scheduler_state", lambda _project_root: None)
    monkeypatch.setattr(
        "app.runtime.server_sync._collect_local_runtime_payload",
        lambda _project_root: {
            "service_status": "active",
            "studio_service_status": "active",
            "scheduler_state": {
                "channel_states": {
                    "news_default": {
                        "display_name": "NewsTrend",
                        "schedule_enabled": True,
                        "schedule_mode": "daily",
                        "daily_upload_times": ["20:15"],
                        "next_due_at": "2026-03-30T20:15:00",
                    }
                }
            },
        },
    )

    result = fetch_server_runtime_status(tmp_path)

    assert result["status"] == "success"
    assert result["next_due_at"] == "2026-03-30T20:15:00"
    assert result["channel_schedules"][0]["schedule_label"] == "daily 20:15"


def test_fetch_server_runtime_status_returns_cached_payload_when_ssh_fails(tmp_path: Path, monkeypatch) -> None:
    cache_path = _runtime_file(tmp_path, "server_runtime_status_cache.json")
    cache_path.write_text(
        json.dumps(
            {
                "status": "success",
                "studio_public_url": "https://cached.example.com",
                "service_status": "active",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.runtime.server_sync.load_remote_target",
        lambda _project_root: RemoteDeployTarget(host="1.2.3.4", user="ubuntu", path="/srv/yta"),
    )

    class Result:
        returncode = 1
        stdout = ""
        stderr = "timeout"

    monkeypatch.setattr("app.runtime.server_sync._run_ssh", lambda *_args, **_kwargs: Result())

    result = fetch_server_runtime_status(tmp_path)

    assert result["status"] == "cached"
    assert result["studio_public_url"] == "https://cached.example.com"
    assert "timeout" in result["message"]


def test_sync_server_settings_marks_pending_when_remote_sync_fails(tmp_path: Path, monkeypatch) -> None:
    settings_dir = tmp_path / "data"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "studio_settings.json").write_text(json.dumps({"channels": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "app.runtime.server_sync.load_remote_target",
        lambda _project_root: RemoteDeployTarget(host="1.2.3.4", user="ubuntu", path="/srv/yta"),
    )

    class Result:
        returncode = 1
        stdout = ""
        stderr = "ssh timeout"

    monkeypatch.setattr("app.runtime.server_sync.subprocess.run", lambda *_args, **_kwargs: Result())

    result = sync_server_settings(tmp_path)
    sync_state = read_server_sync_state(tmp_path)

    assert result["status"] == "pending"
    assert sync_state["status"] == "pending"
    assert "ssh timeout" in str(sync_state["message"])


def test_pull_server_settings_updates_local_settings_when_remote_differs(tmp_path: Path, monkeypatch) -> None:
    settings_path = tmp_path / "data" / "studio_settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"active_channel_id": "news_default"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "app.runtime.server_sync.load_remote_target",
        lambda _project_root: RemoteDeployTarget(host="1.2.3.4", user="ubuntu", path="/srv/yta"),
    )

    remote_payload = {
        "settings_exists": True,
        "settings_text": json.dumps({"active_channel_id": "welfare_default"}, ensure_ascii=False),
    }

    class Result:
        returncode = 0
        stdout = json.dumps(remote_payload, ensure_ascii=False)
        stderr = ""

    monkeypatch.setattr("app.runtime.server_sync._run_ssh", lambda *_args, **_kwargs: Result())

    result = pull_server_settings(tmp_path)

    assert result["status"] == "success"
    loaded = json.loads(settings_path.read_text(encoding="utf-8"))
    assert loaded["active_channel_id"] == "welfare_default"


def test_reconcile_server_settings_prefers_pending_local_sync(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime.server_sync.read_server_sync_state",
        lambda _project_root: {"status": "pending"},
    )
    monkeypatch.setattr(
        "app.runtime.server_sync.sync_server_settings",
        lambda _project_root: {"status": "pending", "message": "pending push"},
    )
    monkeypatch.setattr(
        "app.runtime.server_sync.pull_server_settings",
        lambda _project_root: {"status": "success", "message": "pulled"},
    )

    result = reconcile_server_settings(tmp_path)

    assert result["status"] == "pending"
    assert result["message"] == "pending push"
