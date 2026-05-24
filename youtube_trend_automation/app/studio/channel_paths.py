from __future__ import annotations

from pathlib import Path


LEGACY_SHARED_YOUTUBE_TOKEN_RELATIVE_PATH = "./data/youtube-token.json"
LEGACY_SHARED_YOUTUBE_TOKEN_CHANNEL_ID = "news_default"


def default_youtube_token_file(channel_id: str) -> str:
    safe_channel_id = _sanitize_channel_id(channel_id)
    return f"./data/youtube-token-{safe_channel_id}.json"


def stored_youtube_token_file(raw_value: str, channel_id: str) -> str:
    candidate = raw_value.strip()
    if not candidate or _looks_like_legacy_shared_youtube_token(candidate):
        return default_youtube_token_file(channel_id)
    return candidate


def resolve_youtube_token_file(raw_value: str, channel_id: str, project_root: Path) -> str:
    candidate = raw_value.strip()
    if not candidate or is_legacy_shared_youtube_token_path(candidate, project_root):
        candidate = default_youtube_token_file(channel_id)
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    _migrate_legacy_shared_token_if_needed(path, channel_id, project_root)
    return str(path)


def is_legacy_shared_youtube_token_path(raw_value: str, project_root: Path) -> bool:
    candidate = raw_value.strip()
    if not candidate:
        return False
    if _looks_like_legacy_shared_youtube_token(candidate):
        return True

    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    legacy_path = (project_root / "data" / "youtube-token.json").resolve()
    return path == legacy_path


def _looks_like_legacy_shared_youtube_token(raw_value: str) -> bool:
    normalized = raw_value.strip().replace("\\", "/")
    return normalized in {
        "youtube-token.json",
        "./youtube-token.json",
        "data/youtube-token.json",
        "./data/youtube-token.json",
    }


def _migrate_legacy_shared_token_if_needed(target_path: Path, channel_id: str, project_root: Path) -> None:
    if channel_id != LEGACY_SHARED_YOUTUBE_TOKEN_CHANNEL_ID or target_path.exists():
        return

    legacy_path = (project_root / "data" / "youtube-token.json").resolve()
    if not legacy_path.exists() or legacy_path == target_path:
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(legacy_path.read_bytes())


def _sanitize_channel_id(channel_id: str) -> str:
    candidate = channel_id.strip()
    if not candidate:
        return "default"
    sanitized = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in candidate)
    return sanitized or "default"
