from __future__ import annotations

from datetime import datetime, timedelta
import locale
from pathlib import Path
import os
import subprocess


WINDOWS_TASK_NAME = "YouTubeTrendAutomationEvery6Hours"


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False) or "utf-8",
        errors="replace",
        check=False,
    )


def is_windows() -> bool:
    return os.name == "nt"


def install_windows_scheduled_task(
    project_root: Path,
    *,
    hours: int | None = None,
    minutes: int | None = None,
    start_delay_minutes: int = 5,
) -> dict[str, str]:
    """Create or refresh the Windows scheduled task for automatic uploads."""

    if not is_windows():
        return {
            "status": "skipped",
            "message": "Windows scheduled task is only available on Windows.",
        }

    normalized_minutes = max(1, int(minutes)) if minutes is not None else 0
    normalized_hours = max(1, int(hours)) if hours is not None else 0
    if normalized_minutes <= 0 and normalized_hours <= 0:
        normalized_minutes = 5
    task_command = _task_command(project_root)
    if not task_command:
        return {
            "status": "failed",
            "message": "Could not find a runnable scheduler entry point for this installation.",
        }
    start_time = (datetime.now() + timedelta(minutes=start_delay_minutes)).strftime("%H:%M")
    schedule_interval_minutes = normalized_minutes or (normalized_hours * 60)

    _run_schtasks(["schtasks.exe", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"])
    create = _run_schtasks(
        [
            "schtasks.exe",
            "/Create",
            "/TN",
            WINDOWS_TASK_NAME,
            "/SC",
            "MINUTE",
            "/MO",
            str(schedule_interval_minutes),
            "/ST",
            start_time,
            "/TR",
            task_command,
            "/F",
        ]
    )
    if create.returncode != 0:
        message = create.stderr.strip() or create.stdout.strip() or "Failed to install Windows scheduled task."
        return {
            "status": "failed",
            "message": message,
        }
    return {
        "status": "success",
        "message": (
            f"Windows scheduled task updated to every {normalized_minutes} minute(s) starting at {start_time}."
            if normalized_minutes
            else f"Windows scheduled task updated to every {normalized_hours} hour(s) starting at {start_time}."
        ),
    }


def uninstall_windows_scheduled_task() -> dict[str, str]:
    """Remove the legacy local Windows scheduled task if it exists."""

    if not is_windows():
        return {
            "status": "skipped",
            "message": "Windows scheduled task is only available on Windows.",
        }

    result = _run_schtasks(["schtasks.exe", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"])
    if result.returncode == 0:
        return {
            "status": "success",
            "message": f"Removed local Windows scheduled task: {WINDOWS_TASK_NAME}.",
        }

    combined = f"{result.stderr}\n{result.stdout}".strip()
    if "cannot find the file specified" in combined.lower():
        return {
            "status": "missing",
            "message": "Local Windows scheduled task is already absent.",
        }
    return {
        "status": "failed",
        "message": combined or "Failed to remove local Windows scheduled task.",
    }


def query_windows_scheduled_task() -> dict[str, str]:
    """Return a lightweight status summary for the scheduled task."""

    if not is_windows():
        return {
            "status": "unavailable",
            "message": "Not running on Windows.",
        }

    result = _run_schtasks(["schtasks.exe", "/Query", "/TN", WINDOWS_TASK_NAME, "/V", "/FO", "LIST"])
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Windows scheduled task not found."
        return {
            "status": "missing",
            "message": message,
        }

    summary: dict[str, str] = {
        "status": "ready",
        "message": "Windows scheduled task is installed.",
        "raw": result.stdout,
    }
    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        summary[key.strip()] = value.strip()
    return summary


def _task_command(project_root: Path) -> str:
    exe_path = project_root / "YouTubeAutomationStudio.exe"
    if exe_path.exists():
        return f'"{exe_path}" --scheduled-run-once'

    dist_exe_path = project_root / "dist" / "YouTubeAutomationStudio" / "YouTubeAutomationStudio.exe"
    if dist_exe_path.exists():
        return f'"{dist_exe_path}" --scheduled-run-once'

    runner = project_root / "deploy" / "run_scheduled_once.ps1"
    if runner.exists():
        return f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{runner}"'
    return ""
