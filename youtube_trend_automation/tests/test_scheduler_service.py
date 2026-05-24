from datetime import datetime
import json
from datetime import datetime
import errno
from pathlib import Path

from app.models import PipelineResult
from app.scheduler.service import (
    _refresh_channel_states,
    _ensure_scheduler_anchor,
    _infer_anchor_from_log,
    _is_due,
    _next_daily_due_iso,
    _normalized_daily_upload_times,
    _save_scheduler_state,
    run_scheduled_channels,
    scheduler_state_path,
)
from app.studio.store import StudioSettingsStore


def test_infer_anchor_from_scheduler_log(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "systemd.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "2026-03-27 07:04:29 | INFO | app.scheduler.service | Starting scheduler heartbeat with 6-hour interval",
                '2026-03-27 13:04:29 | INFO | apscheduler.executors.default | Running job "SchedulerService._run_job (trigger: cron[minute=\'*\'], next run at: 2026-03-27 13:05:00 KST)"',
            ]
        ),
        encoding="utf-8",
    )

    assert _infer_anchor_from_log(log_path) == "2026-03-27 13:04:29"


def test_ensure_scheduler_anchor_bootstraps_from_log(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "systemd.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        '2026-03-27 13:04:29 | INFO | apscheduler.executors.default | Running job "SchedulerService._run_job"',
        encoding="utf-8",
    )

    state = _ensure_scheduler_anchor(tmp_path, 6, {})

    assert state["last_anchor_at"] == "2026-03-27 13:04:29"
    assert state["next_due_at"] == "2026-03-27T19:04:29"
    persisted = json.loads(scheduler_state_path(tmp_path).read_text(encoding="utf-8"))
    assert persisted["schedule_hours"] == 6


def test_is_due_respects_anchor_and_hours() -> None:
    state = {"last_anchor_at": "2026-03-27 13:04:29"}

    assert _is_due(state, 6, now=datetime(2026, 3, 27, 19, 4, 29)) is True
    assert _is_due(state, 6, now=datetime(2026, 3, 27, 19, 4, 28)) is False


def test_next_daily_due_uses_multiple_time_slots() -> None:
    next_due = _next_daily_due_iso(["06:00", "10:00", "14:00", "19:00"], datetime(2026, 3, 27, 10, 5, 0))

    assert next_due == "2026-03-27T14:00:00"


def test_normalized_daily_upload_times_sorts_and_dedupes() -> None:
    values = _normalized_daily_upload_times(["19:00", "06:00", "10:00", "06:00"], legacy_raw="14:00")

    assert values == ["06:00", "10:00", "14:00", "19:00"]


def test_run_scheduled_channels_iterates_all_channels(tmp_path: Path, monkeypatch) -> None:
    class FakePipeline:
        def __init__(self, config) -> None:
            self.config = config

        def run_once(self) -> PipelineResult:
            if self.config.active_channel.id in {"insight_default", "story_default"}:
                return PipelineResult(
                    mode="run-once",
                    status="failed",
                    selected_topic="failed topic",
                    warnings=["upload failed"],
                )
            return PipelineResult(
                mode="run-once",
                status="success",
                selected_topic="news topic",
            )

    monkeypatch.setattr("app.scheduler.service.Pipeline", FakePipeline)

    summary = run_scheduled_channels(tmp_path, force_all=True)

    assert summary["status"] == "partial_failed"
    details = summary["details"]
    assert details["channel_count"] == 4
    assert details["success_count"] == 2
    assert details["failed_count"] == 2
    channel_results = details["channel_results"]
    assert [item["channel_id"] for item in channel_results] == [
        "news_default",
        "welfare_default",
        "insight_default",
        "story_default",
    ]
    assert [item["status"] for item in channel_results] == ["success", "success", "failed", "failed"]


def test_run_scheduled_channels_skips_disabled_channels_even_when_forced(tmp_path: Path, monkeypatch) -> None:
    store = StudioSettingsStore(tmp_path / "data" / "studio_settings.json")
    settings = store.load()
    for channel in settings.channels:
        if channel.id != "news_default":
            channel.enabled = False
            channel.auto_generate = False
            channel.auto_render = False
            channel.auto_upload = False
            channel.schedule_enabled = False
    store.save(settings)

    class FakePipeline:
        def __init__(self, config) -> None:
            self.config = config

        def run_once(self) -> PipelineResult:
            return PipelineResult(
                mode="run-once",
                status="success",
                selected_topic=self.config.active_channel.id,
            )

    monkeypatch.setattr("app.scheduler.service.Pipeline", FakePipeline)

    summary = run_scheduled_channels(tmp_path, force_all=True)

    assert summary["status"] == "success"
    channel_results = summary["details"]["channel_results"]
    assert [item["channel_id"] for item in channel_results] == ["news_default"]


def test_interval_channel_bootstrapped_without_completion_becomes_due_now(tmp_path: Path) -> None:
    settings = StudioSettingsStore(tmp_path / "data" / "studio_settings.json").load()
    story_channel = next(channel for channel in settings.channels if channel.id == "story_default")
    state = {
        "channel_states": {
            "story_default": {
                "channel_id": "story_default",
                "schedule_enabled": True,
                "schedule_mode": "interval",
                "schedule_interval_hours": 168,
                "last_status": "bootstrapped",
                "last_anchor_at": "2026-04-05T09:55:00",
                "next_due_at": "2026-04-12T09:55:00",
            }
        }
    }

    refreshed = _refresh_channel_states(tmp_path, [story_channel], state, now=datetime(2026, 4, 5, 21, 0, 0))
    story_state = refreshed["channel_states"]["story_default"]

    assert story_state["next_due_at"] == "2026-04-05T21:00:00"


def test_save_scheduler_state_retries_after_enospc(tmp_path: Path, monkeypatch) -> None:
    path = scheduler_state_path(tmp_path)
    calls = {"count": 0, "recovered": 0}
    original_write_text = Path.write_text

    def fake_write_text(self: Path, data: str, encoding: str = "utf-8", **kwargs):
        if self == path and calls["count"] == 0:
            calls["count"] += 1
            raise OSError(errno.ENOSPC, "No space left on device")
        calls["count"] += 1
        return original_write_text(self, data, encoding=encoding, **kwargs)

    monkeypatch.setattr("app.scheduler.service._ensure_runtime_headroom", lambda *args, **kwargs: calls.__setitem__("recovered", calls["recovered"] + 1))
    monkeypatch.setattr(Path, "write_text", fake_write_text)

    _save_scheduler_state(tmp_path, {"status": "ok"})

    assert calls["recovered"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "ok"
