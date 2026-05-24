from pathlib import Path

from app.runtime import windows_scheduler
from app.runtime.windows_scheduler import _task_command


def test_task_command_prefers_root_exe(tmp_path: Path) -> None:
    exe = tmp_path / "YouTubeAutomationStudio.exe"
    exe.write_text("", encoding="utf-8")

    command = _task_command(tmp_path)

    assert str(exe) in command
    assert "--scheduled-run-once" in command


def test_task_command_falls_back_to_powershell_runner(tmp_path: Path) -> None:
    runner = tmp_path / "deploy" / "run_scheduled_once.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("", encoding="utf-8")

    command = _task_command(tmp_path)

    assert "powershell.exe" in command
    assert str(runner) in command


def test_uninstall_windows_scheduled_task_skips_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(windows_scheduler, "is_windows", lambda: False)

    result = windows_scheduler.uninstall_windows_scheduled_task()

    assert result["status"] == "skipped"


def test_install_windows_scheduled_task_supports_minute_interval(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "YouTubeAutomationStudio.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(windows_scheduler, "is_windows", lambda: True)

    commands: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **_kwargs):
        commands.append(args)
        return Result()

    monkeypatch.setattr(windows_scheduler.subprocess, "run", fake_run)

    result = windows_scheduler.install_windows_scheduled_task(tmp_path, minutes=5, start_delay_minutes=1)

    assert result["status"] == "success"
    create_cmd = commands[-1]
    assert "/MO" in create_cmd
    assert create_cmd[create_cmd.index("/MO") + 1] == "5"
