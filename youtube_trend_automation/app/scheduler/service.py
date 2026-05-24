from __future__ import annotations

from datetime import datetime, timedelta
import errno
import json
from pathlib import Path
import shutil

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import AppConfig, load_config
from app.models import PipelineResult
from app.pipeline import Pipeline
from app.runtime.control import is_studio_session_active, scheduler_pause_message
from app.storage.repository import StorageRepository
from app.studio.models import ChannelProfile
from app.studio.store import StudioSettingsStore
from app.utils.logging import get_logger

LOGGER = get_logger(__name__)
MIN_RUNTIME_FREE_BYTES = 256 * 1024 * 1024


class SchedulerService:
    """Run the automation pipeline on a fixed heartbeat for per-channel schedules."""

    def __init__(self, config: AppConfig, pipeline: Pipeline) -> None:
        self.config = config
        self.pipeline = pipeline

    def start(self) -> None:
        """Start the blocking scheduler."""

        scheduler = BlockingScheduler(timezone=self.config.timezone)
        scheduler.add_job(
            self._run_job,
            "cron",
            minute="*",
            id="scheduler_tick",
            coalesce=True,
            max_instances=1,
        )
        LOGGER.info("Starting scheduler heartbeat for per-channel automation rules")
        scheduler.start()

    def _run_job(self) -> None:
        runtime_config = load_config(self.config.project_root)
        if is_studio_session_active(runtime_config.project_root):
            LOGGER.info(scheduler_pause_message(runtime_config.project_root))
            return

        result = run_scheduled_channels(runtime_config.project_root)
        if str(result.get("status") or "") != "skipped":
            LOGGER.info("Scheduled run completed: %s", result)


def run_scheduled_channels(
    project_root: Path,
    *,
    force_all: bool = False,
    now: datetime | None = None,
) -> dict[str, object]:
    """Run due channels once, or force every configured channel when requested."""

    current_time = now or datetime.now()
    _ensure_runtime_headroom(project_root)
    settings = StudioSettingsStore(project_root / "data" / "studio_settings.json").load()
    state = _load_scheduler_state(project_root)
    state = _refresh_channel_states(project_root, settings.channels, state, now=current_time)

    due_channels = [
        channel
        for channel in settings.channels
        if _channel_allows_scheduled_run(channel) and (force_all or _channel_is_due(state, channel.id, current_time))
    ]

    for channel in settings.channels:
        if channel in due_channels:
            continue
        disabled_reason = _channel_skip_reason(channel)
        if disabled_reason:
            LOGGER.info("[SKIP] %s: %s", channel.display_name, disabled_reason)

    if not due_channels:
        state["last_checked_at"] = _isoformat(current_time)
        _save_scheduler_state(project_root, state)
        return _build_summary(status="skipped", channel_results=[], state=state, warnings=[])

    started_at = _isoformat(current_time)
    state["last_started_at"] = started_at
    state["last_anchor_at"] = started_at
    state["last_status"] = "running"
    state["last_error"] = ""
    _save_scheduler_state(project_root, state)

    channel_results: list[dict[str, object]] = []
    warnings: list[str] = []

    for channel in due_channels:
        channel_started_at = datetime.now()
        _mark_channel_running(state, channel, channel_started_at)
        _save_scheduler_state(project_root, state)

        channel_config = load_config(project_root, channel_id=channel.id)
        try:
            result = Pipeline(channel_config).run_once()
        except Exception as exc:
            LOGGER.exception("Scheduled channel run failed channel_id=%s", channel.id)
            channel_finished_at = datetime.now()
            _finish_channel_state(
                state,
                channel,
                started_at=channel_started_at,
                finished_at=channel_finished_at,
                status="failed",
                error=str(exc),
            )
            warnings.append(f"{channel.display_name}: {exc}")
            channel_results.append(
                _scheduled_exception_entry(channel, state["channel_states"][channel.id], str(exc))
            )
            _save_scheduler_state(project_root, state)
            continue

        channel_finished_at = datetime.now()
        _finish_channel_state(
            state,
            channel,
            started_at=channel_started_at,
            finished_at=channel_finished_at,
            status=result.status,
            error=_result_error(result),
        )
        channel_results.append(_scheduled_channel_entry(channel, state["channel_states"][channel.id], result))
        if result.status != "success":
            warnings.extend(
                f"{channel.display_name}: {warning}" for warning in (result.warnings or [result.status])
            )
        _save_scheduler_state(project_root, state)

    state = _refresh_channel_states(project_root, settings.channels, state, now=datetime.now())
    summary_status = _overall_status(channel_results)
    summary = _build_summary(status=summary_status, channel_results=channel_results, state=state, warnings=warnings)
    state["last_completed_at"] = _now_iso()
    state["last_status"] = summary_status
    state["last_error"] = _summary_error_message(summary) if summary_status != "success" else ""
    state["last_result"] = summary
    _save_scheduler_state(project_root, state)
    return summary


def _build_summary(
    *,
    status: str,
    channel_results: list[dict[str, object]],
    state: dict[str, object],
    warnings: list[str],
) -> dict[str, object]:
    success_count = sum(1 for item in channel_results if str(item.get("status") or "") == "success")
    failed_count = sum(1 for item in channel_results if str(item.get("status") or "") != "success")
    return {
        "mode": "scheduled-run",
        "status": status,
        "warnings": warnings,
        "details": {
            "channel_results": channel_results,
            "channel_count": len((state.get("channel_states") if isinstance(state.get("channel_states"), dict) else {}) or {}),
            "success_count": success_count,
            "failed_count": failed_count,
            "next_due_at": str(state.get("next_due_at") or ""),
            "channel_schedules": _channel_schedule_rows_from_state(state),
        },
    }


def _scheduled_channel_entry(
    channel: ChannelProfile,
    channel_state: dict[str, object],
    result: PipelineResult,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "channel_id": channel.id,
        "display_name": channel.display_name,
        "preset_key": channel.preset_key,
        "status": result.status,
        "selected_topic": result.selected_topic,
        "metadata_path": result.metadata_path,
        "warnings": result.warnings,
        "next_due_at": str(channel_state.get("next_due_at") or ""),
    }
    if result.upload is not None:
        entry["upload"] = result.upload.to_dict()
    return entry


def _scheduled_exception_entry(
    channel: ChannelProfile,
    channel_state: dict[str, object],
    error: str,
) -> dict[str, object]:
    return {
        "channel_id": channel.id,
        "display_name": channel.display_name,
        "preset_key": channel.preset_key,
        "status": "failed",
        "error": error,
        "warnings": [error],
        "next_due_at": str(channel_state.get("next_due_at") or ""),
    }


def _refresh_channel_states(
    project_root: Path,
    channels: list[ChannelProfile],
    state: dict[str, object],
    *,
    now: datetime,
) -> dict[str, object]:
    existing = state.get("channel_states") if isinstance(state.get("channel_states"), dict) else {}
    refreshed: dict[str, dict[str, object]] = {}

    for channel in channels:
        entry = dict(existing.get(channel.id) or {})
        previous_enabled = bool(entry.get("schedule_enabled", True))
        previous_mode = str(entry.get("schedule_mode") or "")
        previous_interval = int(entry.get("schedule_interval_hours") or _interval_hours(channel))
        previous_daily_times = _normalized_daily_upload_times(
            entry.get("daily_upload_times", []),
            legacy_raw=str(entry.get("daily_upload_time") or "").strip(),
        )

        schedule_enabled = _schedule_enabled(channel)
        schedule_mode = _schedule_mode(channel)
        interval_hours = _interval_hours(channel)
        daily_upload_times = _channel_daily_upload_times(channel)
        schedule_changed = (
            previous_enabled != schedule_enabled
            or
            previous_mode != schedule_mode
            or previous_interval != interval_hours
            or previous_daily_times != daily_upload_times
        )

        entry["channel_id"] = channel.id
        entry["display_name"] = channel.display_name
        entry["preset_key"] = channel.preset_key
        entry["schedule_enabled"] = schedule_enabled
        entry["schedule_mode"] = schedule_mode
        entry["schedule_interval_hours"] = interval_hours
        entry["daily_upload_time"] = daily_upload_times[0] if len(daily_upload_times) == 1 else ""
        entry["daily_upload_times"] = daily_upload_times

        if not schedule_enabled:
            entry["next_due_at"] = ""
            entry["last_status"] = str(entry.get("last_status") or "disabled")
        elif schedule_mode == "daily":
            next_due = _parse_iso_like_timestamp(str(entry.get("next_due_at") or "")) if not schedule_changed else None
            if next_due is None:
                entry["next_due_at"] = _next_daily_due_iso(daily_upload_times, now)
        else:
            anchor = _parse_iso_like_timestamp(str(entry.get("last_anchor_at") or ""))
            if anchor is None:
                anchor = _infer_channel_anchor(project_root, channel)
            if anchor is None:
                anchor = now
                entry.setdefault("last_status", "bootstrapped")
                entry["last_anchor_at"] = _isoformat(anchor)
            if (
                str(entry.get("last_status") or "").strip() == "bootstrapped"
                and not str(entry.get("last_completed_at") or "").strip()
            ):
                entry["next_due_at"] = _isoformat(now)
            elif schedule_changed or not str(entry.get("next_due_at") or "").strip():
                entry["next_due_at"] = _next_due_from_anchor_iso(anchor, interval_hours)

        refreshed[channel.id] = entry

    state["channel_states"] = refreshed
    state["next_due_at"] = _earliest_next_due(refreshed)
    return state


def _mark_channel_running(state: dict[str, object], channel: ChannelProfile, started_at: datetime) -> None:
    channel_states = state.get("channel_states")
    if not isinstance(channel_states, dict):
        channel_states = {}
        state["channel_states"] = channel_states

    entry = dict(channel_states.get(channel.id) or {})
    entry["channel_id"] = channel.id
    entry["display_name"] = channel.display_name
    entry["preset_key"] = channel.preset_key
    entry["schedule_enabled"] = _schedule_enabled(channel)
    entry["last_started_at"] = _isoformat(started_at)
    entry["last_status"] = "running"
    entry["last_error"] = ""
    channel_states[channel.id] = entry


def _finish_channel_state(
    state: dict[str, object],
    channel: ChannelProfile,
    *,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error: str,
) -> None:
    channel_states = state.get("channel_states")
    if not isinstance(channel_states, dict):
        channel_states = {}
        state["channel_states"] = channel_states

    entry = dict(channel_states.get(channel.id) or {})
    entry["channel_id"] = channel.id
    entry["display_name"] = channel.display_name
    entry["preset_key"] = channel.preset_key
    entry["last_anchor_at"] = _isoformat(started_at)
    entry["last_started_at"] = _isoformat(started_at)
    entry["last_completed_at"] = _isoformat(finished_at)
    entry["last_status"] = status
    entry["last_error"] = error
    entry["schedule_enabled"] = _schedule_enabled(channel)
    entry["schedule_mode"] = _schedule_mode(channel)
    entry["schedule_interval_hours"] = _interval_hours(channel)
    daily_upload_times = _channel_daily_upload_times(channel)
    entry["daily_upload_time"] = daily_upload_times[0] if len(daily_upload_times) == 1 else ""
    entry["daily_upload_times"] = daily_upload_times
    entry["next_due_at"] = _next_due_for_channel(channel, started_at=started_at)
    channel_states[channel.id] = entry
    state["next_due_at"] = _earliest_next_due(channel_states)


def _channel_is_due(state: dict[str, object], channel_id: str, now: datetime) -> bool:
    channel_states = state.get("channel_states")
    if not isinstance(channel_states, dict):
        return False
    entry = channel_states.get(channel_id)
    if not isinstance(entry, dict):
        return False
    next_due = _parse_iso_like_timestamp(str(entry.get("next_due_at") or ""))
    if next_due is None:
        return False
    return now >= next_due


def _channel_schedule_rows_from_state(state: dict[str, object]) -> list[dict[str, object]]:
    channel_states = state.get("channel_states")
    if not isinstance(channel_states, dict):
        return []

    rows: list[dict[str, object]] = []
    for channel_id, entry in channel_states.items():
        if not isinstance(entry, dict):
            continue
        if not bool(entry.get("schedule_enabled", True)):
            schedule_label = "자동 업로드 꺼짐"
            next_due_at = ""
        else:
            schedule_mode = str(entry.get("schedule_mode") or "interval")
            daily_times = _normalized_daily_upload_times(
                entry.get("daily_upload_times", []),
                legacy_raw=str(entry.get("daily_upload_time") or "").strip(),
            )
            schedule_label = (
                f"매일 {', '.join(daily_times)}"
                if schedule_mode == "daily" and daily_times
                else f"{int(entry.get('schedule_interval_hours') or 6)}시간마다"
            )
            next_due_at = str(entry.get("next_due_at") or "")
        rows.append(
            {
                "channel_id": channel_id,
                "display_name": str(entry.get("display_name") or channel_id),
                "preset_key": str(entry.get("preset_key") or ""),
                "schedule_label": schedule_label,
                "next_due_at": next_due_at,
                "last_status": str(entry.get("last_status") or ""),
                "last_completed_at": str(entry.get("last_completed_at") or ""),
            }
        )
    return rows


def _earliest_next_due(channel_states: dict[str, object]) -> str:
    due_times: list[datetime] = []
    for entry in channel_states.values():
        if not isinstance(entry, dict):
            continue
        next_due = _parse_iso_like_timestamp(str(entry.get("next_due_at") or ""))
        if next_due is not None:
            due_times.append(next_due)
    if not due_times:
        return ""
    return min(due_times).isoformat(timespec="seconds")


def _next_due_for_channel(channel: ChannelProfile, *, started_at: datetime) -> str:
    if not _schedule_enabled(channel):
        return ""
    if _schedule_mode(channel) == "daily":
        return _next_daily_due_iso(_channel_daily_upload_times(channel), started_at)
    return _next_due_from_anchor_iso(started_at, _interval_hours(channel))


def _next_due_from_anchor_iso(anchor: datetime, schedule_hours: int) -> str:
    return (anchor + timedelta(hours=max(1, int(schedule_hours)))).isoformat(timespec="seconds")


def _next_daily_due_iso(raw_times: list[str], reference: datetime) -> str:
    normalized_times = _normalized_daily_upload_times(raw_times)
    if not normalized_times:
        return ""
    candidates: list[datetime] = []
    for normalized in normalized_times:
        hour, minute = (int(part) for part in normalized.split(":", 1))
        candidate = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= reference:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates).isoformat(timespec="seconds")


def _schedule_mode(channel: ChannelProfile) -> str:
    return "daily" if _channel_daily_upload_times(channel) else "interval"


def _schedule_enabled(channel: ChannelProfile) -> bool:
    return bool(getattr(channel, "schedule_enabled", True)) and _channel_allows_scheduled_run(channel)


def _channel_enabled(channel: ChannelProfile) -> bool:
    return bool(getattr(channel, "enabled", True))


def _channel_allows_scheduled_run(channel: ChannelProfile) -> bool:
    return (
        _channel_enabled(channel)
        and bool(getattr(channel, "auto_generate", True))
        and bool(getattr(channel, "auto_render", True))
        and bool(getattr(channel, "auto_upload", True))
    )


def _channel_skip_reason(channel: ChannelProfile) -> str:
    if not _channel_enabled(channel):
        return f"channel disabled: {channel.id}"
    if not bool(getattr(channel, "auto_generate", True)):
        return f"generation disabled: {channel.id}"
    if not bool(getattr(channel, "auto_render", True)):
        return f"render disabled: {channel.id}"
    if not bool(getattr(channel, "auto_upload", True)):
        return f"upload disabled: {channel.id}"
    if not bool(getattr(channel, "schedule_enabled", True)):
        return f"schedule disabled: {channel.id}"
    return ""


def _interval_hours(channel: ChannelProfile) -> int:
    return max(1, int(getattr(channel, "schedule_interval_hours", 6) or 6))


def _channel_daily_upload_times(channel: ChannelProfile) -> list[str]:
    return _normalized_daily_upload_times(
        getattr(channel, "daily_upload_times", []),
        legacy_raw=str(getattr(channel, "daily_upload_time", "") or "").strip(),
    )


def _normalized_daily_upload_times(raw_values: object, *, legacy_raw: str = "") -> list[str]:
    candidates: list[str] = []
    if isinstance(raw_values, list):
        candidates.extend(str(item or "").strip() for item in raw_values)
    elif isinstance(raw_values, str):
        candidates.extend(part.strip() for part in raw_values.replace(",", "\n").splitlines())
    if legacy_raw:
        candidates.append(str(legacy_raw).strip())

    normalized: list[str] = []
    for value in candidates:
        if not value:
            continue
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError:
            continue
        formatted = parsed.strftime("%H:%M")
        if formatted not in normalized:
            normalized.append(formatted)
    return sorted(normalized)


def _overall_status(channel_results: list[dict[str, object]]) -> str:
    if not channel_results:
        return "skipped"
    success_count = sum(1 for item in channel_results if str(item.get("status") or "") == "success")
    failed_count = len(channel_results) - success_count
    if failed_count and success_count:
        return "partial_failed"
    if failed_count:
        return "failed"
    return "success"


def _result_error(result: PipelineResult) -> str:
    if result.status == "success":
        return ""
    if result.warnings:
        return result.warnings[0]
    return result.status


def _summary_error_message(summary: dict[str, object]) -> str:
    details = summary.get("details") if isinstance(summary.get("details"), dict) else {}
    channel_results = details.get("channel_results") if isinstance(details, dict) else []
    messages: list[str] = []
    if isinstance(channel_results, list):
        for item in channel_results:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "success") == "success":
                continue
            label = str(item.get("display_name") or item.get("channel_id") or "channel")
            error = str(item.get("error") or "").strip()
            if error:
                messages.append(f"{label}: {error}")
                continue
            warnings = item.get("warnings")
            if isinstance(warnings, list) and warnings:
                messages.append(f"{label}: {warnings[0]}")
            else:
                messages.append(f"{label}: {item.get('status', 'failed')}")
    return "; ".join(messages)


def scheduler_state_path(project_root: Path) -> Path:
    path = project_root / "data" / "runtime" / "scheduler_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_scheduler_state(project_root: Path) -> dict[str, object]:
    path = scheduler_state_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_scheduler_state(project_root: Path, payload: dict[str, object]) -> None:
    path = scheduler_state_path(project_root)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        path.write_text(serialized, encoding="utf-8")
        return
    except OSError as exc:
        if exc.errno != errno.ENOSPC:
            raise
        LOGGER.warning("Scheduler state save failed due to low disk space; pruning runtime outputs and retrying.")
        _ensure_runtime_headroom(project_root, force=True)
        path.write_text(serialized, encoding="utf-8")


def _ensure_runtime_headroom(project_root: Path, *, force: bool = False) -> None:
    usage = shutil.disk_usage(project_root)
    if not force and usage.free >= MIN_RUNTIME_FREE_BYTES:
        return
    config = load_config(project_root)
    StorageRepository(config).prune_outputs()


def _infer_channel_anchor(project_root: Path, channel: ChannelProfile) -> datetime | None:
    config = load_config(project_root, channel_id=channel.id)
    latest_uploaded = StorageRepository(config).latest_uploaded_run(channel_id=channel.id)
    if not latest_uploaded:
        latest_uploaded = StorageRepository(config).latest_run(channel_id=channel.id)
    if not latest_uploaded:
        return None

    candidates = [
        str(latest_uploaded.get("created_at") or "").strip(),
        str(latest_uploaded.get("result", {}).get("details", {}).get("uploaded_at") or "").strip()
        if isinstance(latest_uploaded.get("result"), dict)
        else "",
        str(latest_uploaded.get("artifacts", {}).get("upload", {}).get("extra", {}).get("published_at") or "").strip()
        if isinstance(latest_uploaded.get("artifacts"), dict)
        else "",
    ]
    for raw in candidates:
        parsed = _parse_iso_like_timestamp(raw)
        if parsed is not None:
            return parsed
    return None


def _ensure_scheduler_anchor(project_root: Path, schedule_hours: int, state: dict[str, object]) -> dict[str, object]:
    if state.get("last_anchor_at"):
        if state.get("schedule_hours") != schedule_hours or not state.get("next_due_at"):
            state["schedule_hours"] = schedule_hours
            state["next_due_at"] = _next_due_iso(str(state["last_anchor_at"]), schedule_hours)
            _save_scheduler_state(project_root, state)
        return state

    inferred_anchor = _infer_anchor_from_log(project_root / "logs" / "systemd.log")
    if inferred_anchor:
        state = {
            **state,
            "last_anchor_at": inferred_anchor,
            "last_status": str(state.get("last_status") or "inferred"),
            "next_due_at": _next_due_iso(inferred_anchor, schedule_hours),
            "schedule_hours": schedule_hours,
        }
    else:
        state = {
            **state,
            "last_anchor_at": _now_iso(),
            "last_status": str(state.get("last_status") or "bootstrapped"),
            "next_due_at": _next_due_iso(_now_iso(), schedule_hours),
            "schedule_hours": schedule_hours,
        }
    _save_scheduler_state(project_root, state)
    return state


def _infer_anchor_from_log(log_path: Path) -> str:
    if not log_path.exists():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""

    for line in reversed(lines):
        if 'Running job "SchedulerService._run_job' not in line:
            continue
        timestamp = line.split(" | ", 1)[0].strip()
        if _parse_iso_like_timestamp(timestamp) is not None:
            return timestamp
    return ""


def _is_due(state: dict[str, object], schedule_hours: int, *, now: datetime | None = None) -> bool:
    anchor = _parse_iso_like_timestamp(str(state.get("last_anchor_at") or ""))
    if anchor is None:
        return False
    current_time = now or datetime.now()
    return current_time >= anchor + timedelta(hours=max(1, schedule_hours))


def _parse_iso_like_timestamp(raw: str) -> datetime | None:
    value = str(raw).strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _next_due_iso(anchor_raw: str, schedule_hours: int) -> str:
    anchor = _parse_iso_like_timestamp(anchor_raw)
    if anchor is None:
        return ""
    return (anchor + timedelta(hours=max(1, schedule_hours))).isoformat(timespec="seconds")
