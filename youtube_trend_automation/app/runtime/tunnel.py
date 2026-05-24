from __future__ import annotations

from contextlib import AbstractContextManager
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from app.runtime.control import read_studio_access, write_studio_access


PUBLIC_URL_PATTERN = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com", re.IGNORECASE)


def resolve_cloudflared_binary(code_root: Path) -> Path | None:
    override = os.getenv("YTA_CLOUDFLARED_BIN", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.exists():
            return path

    candidates = [
        code_root / "vendor" / "cloudflared" / "cloudflared",
        code_root / "vendor" / "cloudflared" / "cloudflared.exe",
        code_root / "_internal" / "vendor" / "cloudflared" / "cloudflared",
        code_root / "_internal" / "vendor" / "cloudflared" / "cloudflared.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    which = shutil.which("cloudflared")
    return Path(which) if which else None


class CloudflareTunnel(AbstractContextManager["CloudflareTunnel"]):
    """Start either a quick tunnel or a named tunnel for the Studio UI."""

    def __init__(
        self,
        project_root: Path,
        *,
        code_root: Path,
        local_url: str,
        logger: logging.Logger,
        enabled: bool = True,
        mode: str = "quick",
        tunnel_name: str = "",
        hostname: str = "",
        tunnel_token: str = "",
    ) -> None:
        self.project_root = project_root
        self.code_root = code_root
        self.local_url = local_url
        self.logger = logger
        self.enabled = enabled
        self.mode = self._normalize_mode(mode)
        self.tunnel_name = tunnel_name.strip()
        self.hostname = hostname.strip()
        self.tunnel_token = tunnel_token.strip()
        self.binary = resolve_cloudflared_binary(code_root)
        self.public_url = ""
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._stop_event = threading.Event()
        self._home_dir = self.project_root / "data" / "runtime" / "cloudflared_home"
        self._named_connection_established = False

    def start(self) -> None:
        if not self.enabled:
            self._update_access(tunnel_mode=self.mode, tunnel_status="disabled")
            return
        if self.binary is None:
            self._update_access(
                tunnel_mode=self.mode,
                tunnel_status="unavailable",
                tunnel_error="cloudflared binary not found",
            )
            self.logger.warning("Cloudflare tunnel disabled: cloudflared binary not found.")
            return

        self._thread = threading.Thread(target=self._run, name="cloudflare-tunnel", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def __enter__(self) -> CloudflareTunnel:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def _run(self) -> None:
        if not self._wait_for_health():
            self._update_access(
                tunnel_mode=self.mode,
                tunnel_status="failed",
                tunnel_error="Studio server health check timed out",
            )
            self.logger.warning("Cloudflare tunnel startup skipped: local Studio server did not become healthy in time.")
            return

        self._home_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(self._home_dir)
        env["USERPROFILE"] = str(self._home_dir)

        if self.mode == "named":
            command = self._named_tunnel_command()
            if not command:
                self.logger.warning("Named tunnel mode requested but hostname or token is missing. Falling back to quick tunnel.")
                self._update_access(
                    tunnel_mode="named",
                    tunnel_status="fallback",
                    tunnel_error="Named tunnel requires both hostname and tunnel token. Falling back to quick tunnel.",
                )
                self.mode = "quick"
                command = self._quick_tunnel_command()
            else:
                self.public_url = f"https://{self.hostname}"
        else:
            command = self._quick_tunnel_command()

        self.logger.info("Starting Cloudflare tunnel with %s in %s mode", self.binary, self.mode)
        self._update_access(
            public_url=self.public_url,
            tunnel_mode=self.mode,
            tunnel_status="starting",
            tunnel_error="",
        )

        self._process = subprocess.Popen(
            command,
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        assert self._process.stdout is not None
        for raw_line in self._process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            self.logger.info("cloudflared | %s", line)
            self._handle_output_line(line)
            if self._stop_event.is_set():
                break

        if self._stop_event.is_set():
            return

        return_code = self._process.wait()
        if return_code != 0 and not self.public_url:
            message = f"cloudflared exited with code {return_code}"
            self._update_access(tunnel_mode=self.mode, tunnel_status="failed", tunnel_error=message)
            self.logger.warning(message)

    def _handle_output_line(self, line: str) -> None:
        match = PUBLIC_URL_PATTERN.search(line)
        if match:
            self.public_url = match.group(0)
            self._update_access(
                public_url=self.public_url,
                tunnel_mode=self.mode,
                tunnel_status="active",
                tunnel_error="",
            )
            return

        if "Registered tunnel connection" in line and self.mode == "named" and self.public_url:
            self._named_connection_established = True
            if self._remote_url_ready(self.public_url):
                self._update_access(
                    public_url=self.public_url,
                    tunnel_mode=self.mode,
                    tunnel_status="active",
                    tunnel_error="",
                )
            else:
                self._update_access(
                    public_url=self.public_url,
                    tunnel_mode=self.mode,
                    tunnel_status="starting",
                    tunnel_error="Named tunnel connected but public hostname has not responded yet.",
                )
            return

        lowered = line.lower()
        if not self.public_url and ("error" in lowered or "failed" in lowered):
            self._update_access(tunnel_mode=self.mode, tunnel_status="failed", tunnel_error=line)

    def _quick_tunnel_command(self) -> list[str]:
        return [
            str(self.binary),
            "tunnel",
            "--url",
            self.local_url,
            "--protocol",
            "http2",
        ]

    def _named_tunnel_command(self) -> list[str] | None:
        if not self.hostname or not self.tunnel_token:
            return None
        return [
            str(self.binary),
            "tunnel",
            "--url",
            self.local_url,
            "--protocol",
            "http2",
            "--hostname",
            self.hostname,
            "run",
            "--token",
            self.tunnel_token,
        ]

    def _wait_for_health(self, timeout_seconds: int = 60) -> bool:
        deadline = time.time() + timeout_seconds
        health_url = f"{self.local_url}/_stcore/health"
        while time.time() < deadline and not self._stop_event.is_set():
            try:
                with urlopen(health_url, timeout=3) as response:
                    if response.status == 200:
                        return True
            except URLError:
                time.sleep(0.5)
            except OSError:
                time.sleep(0.5)
        return False

    def _remote_url_ready(self, url: str, timeout_seconds: int = 15) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline and not self._stop_event.is_set():
            try:
                with urlopen(url, timeout=5) as response:
                    if 200 <= response.status < 500:
                        return True
            except URLError:
                time.sleep(1)
            except OSError:
                time.sleep(1)
        return False

    def _update_access(self, **updates: Any) -> None:
        payload = read_studio_access(self.project_root)
        payload.update(updates)
        write_studio_access(self.project_root, payload)

    @staticmethod
    def _normalize_mode(value: str) -> str:
        normalized = value.strip().lower()
        return normalized if normalized in {"quick", "named"} else "quick"
