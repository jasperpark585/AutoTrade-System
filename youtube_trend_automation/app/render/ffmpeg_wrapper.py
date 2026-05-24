from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

from app.models import ArtifactStatus


class FFmpegWrapper:
    """Thin wrapper around the ffmpeg executable."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.binary = self._resolve_binary()

    def run(self, args: list[str], cwd: Path) -> ArtifactStatus:
        command = [self.binary, *args]
        process = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            return ArtifactStatus(
                status="failed",
                provider="ffmpeg",
                message=process.stderr.strip() or "ffmpeg failed",
                extra={"stdout": process.stdout, "stderr": process.stderr, "returncode": process.returncode},
            )
        return ArtifactStatus(
            status="created",
            provider="ffmpeg",
            extra={"stdout": process.stdout, "stderr": process.stderr, "returncode": process.returncode},
        )

    def is_available(self) -> bool:
        return self.binary is not None

    def available_encoders(self) -> set[str]:
        if not self.binary:
            return set()
        process = subprocess.run(
            [self.binary, "-hide_banner", "-encoders"],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            return set()

        encoders: set[str] = set()
        for line in process.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith(("V", "A", "S")):
                encoders.add(parts[1])
        return encoders

    def _resolve_binary(self) -> str | None:
        env_value = os.getenv("FFMPEG_BIN")
        if env_value and Path(env_value).exists():
            return env_value

        system_binary = shutil.which("ffmpeg")
        if system_binary:
            return system_binary

        candidates = [
            self.project_root / "ffmpeg.exe",
            self.project_root / "ffmpeg" / "bin" / "ffmpeg.exe",
            self.project_root / "ffmpeg" / "ffmpeg.exe",
        ]

        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            candidates.extend(sorted(Path(local_app_data).glob("CapCut/Apps/*/ffmpeg.exe")))

        for candidate in candidates:
            if Path(candidate).exists():
                return str(candidate)
        return None
