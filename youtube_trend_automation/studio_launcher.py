from __future__ import annotations

from contextlib import closing
import argparse
import logging
import os
from pathlib import Path
import runpy
import shutil
import socket
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


def _code_root() -> Path:
    if getattr(sys, "frozen", False):
        internal_root = getattr(sys, "_MEIPASS", "")
        if internal_root:
            return Path(internal_root).resolve()
        candidate = Path(sys.executable).resolve().parent / "_internal"
        if candidate.exists():
            return candidate
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _runtime_root(code_root: Path) -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        dev_candidate = exe_dir.parent.parent
        if (dev_candidate / "main.py").exists() and (dev_candidate / "studio_app.py").exists():
            return dev_candidate
        return exe_dir
    return code_root


CODE_ROOT = _code_root()
RUNTIME_ROOT = _runtime_root(CODE_ROOT)
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from app.config import load_config
from app.runtime.control import (
    StudioSessionHeartbeat,
    clear_studio_access,
    detect_lan_ip,
    scheduler_pause_message,
    write_studio_access,
)
from app.scheduler.service import run_scheduled_channels
from app.runtime.tunnel import CloudflareTunnel
from app.runtime.windows_scheduler import uninstall_windows_scheduled_task
from app.studio.store import StudioSettingsStore
from app.utils.logging import configure_logging


def _setup_logging(runtime_root: Path) -> logging.Logger:
    log_dir = runtime_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "studio_launcher.log"

    logger = logging.getLogger("studio_launcher")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def _sync_runtime_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if relative.parts[:1] == ("runtime",):
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def _prepare_runtime_root() -> None:
    for folder_name in ("app", "assets", "configs", "data", "deploy", "docs", "vendor"):
        _sync_runtime_tree(CODE_ROOT / folder_name, RUNTIME_ROOT / folder_name)
    for folder_name in ("logs", "outputs", "secrets"):
        (RUNTIME_ROOT / folder_name).mkdir(parents=True, exist_ok=True)
    for file_name in ("README.md", ".env.example", "main.py", "studio_app.py"):
        source = CODE_ROOT / file_name
        target = RUNTIME_ROOT / file_name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scheduled-run-once", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--listen-host", default="")
    parser.add_argument("--session-source", default="studio")
    return parser.parse_known_args(argv)[0]


def _find_free_port(start_port: int = 8501, host: str = "0.0.0.0") -> int:
    for port in range(start_port, start_port + 100):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError("No available port found for Studio UI.")


def _open_browser_when_ready(url: str, health_url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    if sys.platform.startswith("win"):
                        os.startfile(url)  # type: ignore[attr-defined]
                    else:
                        webbrowser.open_new(url)
                    return
        except URLError:
            time.sleep(0.5)
        except OSError:
            time.sleep(0.5)


def _build_access_payload(local_url: str, lan_url: str) -> dict[str, str]:
    payload = {
        "local_url": local_url,
        "lan_url": lan_url,
        "public_url": "",
        "tunnel_mode": "quick",
        "tunnel_status": "pending",
        "tunnel_error": "",
    }
    if not lan_url:
        payload["lan_url"] = ""
    return payload


def _streamlit_argv(app_path: Path, listen_host: str, local_host: str, port: int) -> list[str]:
    return [
        "streamlit",
        "run",
        str(app_path),
        "--server.headless=true",
        f"--server.address={listen_host}",
        f"--server.port={port}",
        f"--browser.serverAddress={local_host}",
        f"--browser.serverPort={port}",
        "--browser.gatherUsageStats=false",
        "--global.developmentMode=false",
    ]


def _disable_local_scheduler(logger: logging.Logger) -> None:
    result = uninstall_windows_scheduled_task()
    logger.info("Local scheduler disable result: %s", result)


def _run_scheduled_once(logger: logging.Logger) -> int:
    config = load_config(RUNTIME_ROOT)
    configure_logging(config.logs_dir, config.log_level)
    if config.project_root.exists() and (config.project_root / "data" / "runtime" / "studio_session.json").exists():
        from app.runtime.control import is_studio_session_active

        if is_studio_session_active(config.project_root):
            logger.info(scheduler_pause_message(config.project_root))
            return 0

    result = run_scheduled_channels(RUNTIME_ROOT)
    logger.info("EXE scheduled run completed: %s", result)
    return 0 if result.get("status") in {"success", "skipped"} else 1


def _apply_frozen_access_mirrors() -> None:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        os.environ["YTA_STUDIO_ACCESS_JSON_MIRRORS"] = str(exe_dir / "StudioAccess.json")
        os.environ["YTA_STUDIO_ACCESS_TEXT_MIRRORS"] = str(exe_dir / "StudioAccess.txt")


def main(argv: list[str] | None = None) -> int:
    logger = _setup_logging(RUNTIME_ROOT)
    args = _parse_args(argv)

    try:
        _prepare_runtime_root()
        _apply_frozen_access_mirrors()

        if args.scheduled_run_once:
            return _run_scheduled_once(logger)

        if os.name == "nt":
            _disable_local_scheduler(logger)

        app_path = CODE_ROOT / "studio_app.py"
        if not app_path.exists():
            logger.error("studio_app.py not found: %s", app_path)
            return 1

        clear_studio_access(RUNTIME_ROOT)

        settings = StudioSettingsStore(RUNTIME_ROOT / "data" / "studio_settings.json").load()
        listen_host = str(args.listen_host or os.getenv("YTA_STUDIO_LISTEN_HOST", "0.0.0.0")).strip() or "0.0.0.0"
        local_host = "127.0.0.1"
        port = int(args.port) if int(args.port or 0) > 0 else _find_free_port(host=listen_host)
        local_url = f"http://{local_host}:{port}"
        lan_ip = detect_lan_ip() if listen_host == "0.0.0.0" else ""
        lan_url = f"http://{lan_ip}:{port}" if lan_ip else ""
        health_url = f"{local_url}/_stcore/health"

        os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
        os.environ["STREAMLIT_SERVER_ADDRESS"] = listen_host
        os.environ["STREAMLIT_SERVER_PORT"] = str(port)
        os.environ["STREAMLIT_BROWSER_SERVER_ADDRESS"] = local_host
        os.environ["STREAMLIT_BROWSER_SERVER_PORT"] = str(port)
        os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
        os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
        os.environ["YTA_RUNTIME_ROOT"] = str(RUNTIME_ROOT)
        os.chdir(CODE_ROOT)
        sys.argv = _streamlit_argv(app_path, listen_host, local_host, port)

        write_studio_access(RUNTIME_ROOT, _build_access_payload(local_url, lan_url))
        logger.info(
            "Studio launcher starting. code_root=%s runtime_root=%s local_url=%s lan_url=%s",
            CODE_ROOT,
            RUNTIME_ROOT,
            local_url,
            lan_url or "-",
        )

        if not args.no_browser:
            browser_thread = threading.Thread(
                target=_open_browser_when_ready,
                args=(local_url, health_url),
                name="studio-browser-open",
                daemon=True,
            )
            browser_thread.start()

        tunnel = CloudflareTunnel(
            RUNTIME_ROOT,
            code_root=CODE_ROOT,
            local_url=local_url,
            logger=logger,
            enabled=settings.remote_access.enabled,
            mode=settings.remote_access.mode,
            tunnel_name=settings.remote_access.tunnel_name,
            hostname=settings.remote_access.hostname,
            tunnel_token=settings.remote_access.tunnel_token,
        )

        with StudioSessionHeartbeat(RUNTIME_ROOT, port=port, source=str(args.session_source or "studio").strip() or "studio"), tunnel:
            logger.info("Studio session heartbeat active.")
            runpy.run_module("streamlit", run_name="__main__")
        logger.info("Studio launcher exited normally.")
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        logger.info("Studio launcher exited via SystemExit: %s", code)
        return code
    except Exception:
        logger.exception("Studio launcher crashed.")
        return 1
    finally:
        if not args.scheduled_run_once:
            clear_studio_access(RUNTIME_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
