from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from app.runtime.control import (
    clear_studio_access,
    clear_studio_session,
    is_studio_session_active,
    mark_server_update,
    read_studio_access,
    send_current_studio_access_notice,
    studio_access_notice_path,
    scheduler_pause_message,
    studio_access_text_path,
    studio_lock_path,
    write_studio_access,
    write_studio_session,
)


def test_studio_session_lock_reports_active(tmp_path: Path) -> None:
    write_studio_session(tmp_path, port=8502)

    assert is_studio_session_active(tmp_path) is True
    assert "8502" in scheduler_pause_message(tmp_path)

    clear_studio_session(tmp_path)
    assert is_studio_session_active(tmp_path) is False


def test_studio_server_session_does_not_pause_scheduler(tmp_path: Path) -> None:
    write_studio_session(tmp_path, port=8502, source="studio-server")

    assert is_studio_session_active(tmp_path) is False
    assert "8502" in scheduler_pause_message(tmp_path)

    clear_studio_session(tmp_path)
    assert is_studio_session_active(tmp_path) is False


def test_stale_studio_session_is_cleared(tmp_path: Path) -> None:
    payload = {
        "source": "studio",
        "port": 8502,
        "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
    }
    path = studio_lock_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert is_studio_session_active(tmp_path) is False
    assert not path.exists()


def test_studio_session_supports_zulu_timestamp(tmp_path: Path) -> None:
    payload = {
        "source": "studio",
        "port": 8501,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    path = studio_lock_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert is_studio_session_active(tmp_path) is True


def test_studio_session_supports_utf8_bom(tmp_path: Path) -> None:
    payload = {
        "source": "studio",
        "port": 8501,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = studio_lock_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

    assert is_studio_session_active(tmp_path) is True


def test_studio_access_roundtrip_writes_json_and_text(tmp_path: Path) -> None:
    payload = {
        "local_url": "http://127.0.0.1:8501",
        "lan_url": "http://192.168.0.10:8501",
        "public_url": "https://example.trycloudflare.com",
        "tunnel_status": "active",
        "tunnel_error": "",
    }

    write_studio_access(tmp_path, payload)
    loaded = read_studio_access(tmp_path)

    assert loaded["local_url"] == payload["local_url"]
    assert loaded["public_url"] == payload["public_url"]
    assert "updated_at" in loaded
    assert "Public URL: https://example.trycloudflare.com" in studio_access_text_path(tmp_path).read_text(encoding="utf-8")

    clear_studio_access(tmp_path)
    assert read_studio_access(tmp_path) == {}


def test_studio_access_mirrors_to_external_targets(tmp_path: Path, monkeypatch) -> None:
    mirrored_json = tmp_path / "mirror" / "StudioAccess.json"
    mirrored_text = tmp_path / "mirror" / "StudioAccess.txt"
    monkeypatch.setenv("YTA_STUDIO_ACCESS_JSON_MIRRORS", str(mirrored_json))
    monkeypatch.setenv("YTA_STUDIO_ACCESS_TEXT_MIRRORS", str(mirrored_text))

    write_studio_access(
        tmp_path,
        {
            "local_url": "http://127.0.0.1:8501",
            "lan_url": "http://192.168.0.10:8501",
            "public_url": "https://example.trycloudflare.com",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    )

    assert mirrored_json.exists()
    assert mirrored_text.exists()
    assert "Public URL: https://example.trycloudflare.com" in mirrored_text.read_text(encoding="utf-8")

    clear_studio_access(tmp_path)
    assert not mirrored_json.exists()
    assert not mirrored_text.exists()


def test_studio_access_notifies_kakao_only_for_new_server_updates(tmp_path: Path, monkeypatch) -> None:
    sent_messages: list[tuple[str, str]] = []

    class DummyNotifier:
        def __init__(self, token: str | None = None) -> None:
            self.token = "token"

        def send(self, message: str, *, web_url: str = "https://example.com") -> bool:
            sent_messages.append((message, web_url))
            return True

    monkeypatch.setenv("KAKAO_ACCESS_TOKEN", "token")
    monkeypatch.setattr("app.runtime.control.KakaoNotifier", DummyNotifier)

    write_studio_access(
        tmp_path,
        {
            "local_url": "http://127.0.0.1:8501",
            "lan_url": "http://192.168.0.10:8501",
            "public_url": "https://first.trycloudflare.com",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    )
    assert sent_messages == []

    mark_server_update(tmp_path, deploy_id="deploy-1")
    write_studio_access(
        tmp_path,
        {
            "local_url": "http://127.0.0.1:8501",
            "lan_url": "http://192.168.0.10:8501",
            "public_url": "https://first.trycloudflare.com",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    )
    write_studio_access(
        tmp_path,
        {
            "local_url": "http://127.0.0.1:8501",
            "lan_url": "http://192.168.0.10:8501",
            "public_url": "https://first.trycloudflare.com",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    )
    write_studio_access(
        tmp_path,
        {
            "local_url": "http://127.0.0.1:8501",
            "lan_url": "http://192.168.0.10:8501",
            "public_url": "https://second.trycloudflare.com",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    )
    assert len(sent_messages) == 1

    mark_server_update(tmp_path, deploy_id="deploy-2")
    write_studio_access(
        tmp_path,
        {
            "local_url": "http://127.0.0.1:8501",
            "lan_url": "http://192.168.0.10:8501",
            "public_url": "https://second.trycloudflare.com",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    )

    assert len(sent_messages) == 2
    assert sent_messages[0][1] == "https://first.trycloudflare.com"
    assert sent_messages[1][1] == "https://second.trycloudflare.com"
    assert studio_access_notice_path(tmp_path).exists()


def test_send_current_studio_access_notice_updates_notice_state(tmp_path: Path, monkeypatch) -> None:
    sent_messages: list[tuple[str, str]] = []

    class DummyNotifier:
        def __init__(self, token: str | None = None) -> None:
            self.token = "token"

        def send(self, message: str, *, web_url: str = "https://example.com") -> bool:
            sent_messages.append((message, web_url))
            return True

    monkeypatch.setattr("app.runtime.control.KakaoNotifier", DummyNotifier)
    mark_server_update(tmp_path, deploy_id="deploy-manual")
    write_studio_access(
        tmp_path,
        {
            "local_url": "http://127.0.0.1:8501",
            "lan_url": "http://192.168.0.10:8501",
            "public_url": "https://manual.trycloudflare.com",
            "tunnel_status": "active",
            "tunnel_error": "",
        },
    )

    result = send_current_studio_access_notice(tmp_path, reason="manual-test")

    assert result["status"] == "success"
    assert len(sent_messages) == 2
    state = json.loads(studio_access_notice_path(tmp_path).read_text(encoding="utf-8"))
    assert state["last_public_url"] == "https://manual.trycloudflare.com"
    assert state["last_notified_deploy_id"] == "deploy-manual"
    assert state["reason"] == "manual-test"
