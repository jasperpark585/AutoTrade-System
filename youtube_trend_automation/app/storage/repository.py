from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
from typing import Any

from app.config import AppConfig
from app.models import GeneratedContent, PipelineResult, RankedTopic, TopicDetail
from app.utils.text import normalize_text, similarity


class StorageRepository:
    """Persist processed topics and pipeline run metadata."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.history_path = config.data_dir / "processed_topics.json"
        self.metadata_dir = config.output_metadata_dir
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

    def is_duplicate(self, topic: RankedTopic) -> bool:
        """Check whether the exact same normalized topic was already processed for the active channel."""

        history = self._read_history()
        current_channel_id = self._current_channel_id()
        for item in history["topics"]:
            stored_channel_id = item.get("channel_id", "")
            if current_channel_id and stored_channel_id and stored_channel_id != current_channel_id:
                continue
            existing = item.get("normalized_topic", "")
            if existing == topic.normalized_topic:
                return True
        return False

    def is_recently_redundant(self, topic: RankedTopic, *, limit: int = 5) -> bool:
        """Block near-repeat topics from the most recent channel history."""

        history = self._read_history()
        current_channel_id = self._current_channel_id()
        recent_items = [
            item
            for item in reversed(history["topics"])
            if not current_channel_id
            or not item.get("channel_id")
            or item.get("channel_id") == current_channel_id
        ][: max(1, limit)]
        if not recent_items:
            return False

        current_normalized = normalize_text(topic.normalized_topic or topic.representative_title)
        current_signals = self._topic_signals(
            representative_title=topic.representative_title,
            keywords=topic.keywords,
            fallback=current_normalized,
        )

        for item in recent_items:
            existing_normalized = normalize_text(
                str(item.get("normalized_topic") or item.get("representative_title") or "")
            )
            if not existing_normalized:
                continue
            if similarity(existing_normalized, current_normalized) >= 0.68:
                return True

            existing_signals = self._topic_signals(
                representative_title=str(item.get("representative_title", "")),
                keywords=item.get("keywords", []),
                fallback=existing_normalized,
            )
            if len(current_signals & existing_signals) >= 2:
                return True
        return False

    def mark_processed(self, topic: RankedTopic, video_title: str, *, thumbnail_text: str = "") -> None:
        """Store a processed topic in history."""

        history = self._read_history()
        history["topics"].append(
            {
                "channel_id": self._current_channel_id(),
                "preset_key": self.config.active_channel.preset_key if self.config.active_channel else "",
                "normalized_topic": normalize_text(topic.normalized_topic),
                "representative_title": topic.representative_title,
                "keywords": list(topic.keywords[:6]),
                "video_title": video_title,
                "thumbnail_text": str(thumbnail_text or "").strip(),
                "processed_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._write_json(self.history_path, history)

    def recent_processed(self, *, limit: int = 8) -> list[dict[str, Any]]:
        """Return recent processed history entries for the active channel."""

        history = self._read_history()
        current_channel_id = self._current_channel_id()
        recent_items = [
            item
            for item in reversed(history["topics"])
            if not current_channel_id
            or not item.get("channel_id")
            or item.get("channel_id") == current_channel_id
        ]
        return recent_items[: max(1, limit)]

    def save_run(
        self,
        run_id: str,
        topic: RankedTopic,
        content: GeneratedContent,
        metadata: dict[str, Any],
        result: PipelineResult,
        details_collected: list[TopicDetail] | None = None,
    ) -> Path:
        """Persist a pipeline run summary to the metadata directory."""

        payload = {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "channel": {
                "id": self._current_channel_id(),
                "display_name": self.config.active_channel.display_name if self.config.active_channel else "",
                "preset_key": self.config.active_channel.preset_key if self.config.active_channel else "",
            },
            "topic": {
                "normalized_topic": topic.normalized_topic,
                "representative_title": topic.representative_title,
                "score": topic.score,
                "sources": topic.sources,
                "mentions": topic.mentions,
                "keywords": topic.keywords,
            },
            "details_collected": [
                {
                    "title": item.title,
                    "summary": item.summary,
                    "source": item.source,
                    "url": item.url,
                    "published_at": item.published_at,
                }
                for item in details_collected or []
            ],
            "content": {
                "video_title": content.video_title,
                "script": content.script,
                "description": content.description,
                "tags": content.tags,
                "segments": content.segments,
                "content_format": content.content_format,
                "detail_points": content.detail_points,
                "estimated_duration_seconds": content.estimated_duration_seconds,
                "preset_key": content.preset_key,
                "background_prompt": content.background_prompt,
                "thumbnail_prompt": content.thumbnail_prompt,
                "contains_synthetic_media": content.contains_synthetic_media,
                "altered_content_reason": content.altered_content_reason,
                "thumbnail_text": content.thumbnail_text,
                "hook_title": content.hook_title,
                "hook_script": content.hook_script,
                "hook_image_prompt": content.hook_image_prompt,
                "scenes": [
                    {
                        "index": scene.index,
                        "title": scene.title,
                        "summary": scene.summary,
                        "narration": scene.narration,
                        "image_prompt": scene.image_prompt,
                        "duration_seconds": scene.duration_seconds,
                        "visual_hint": scene.visual_hint,
                    }
                    for scene in content.scenes
                ],
            },
            "artifacts": metadata,
            "result": result.to_dict(),
        }
        path = self.metadata_dir / f"{run_id}.json"
        self._write_json(path, payload)
        return path

    def latest_run(self, channel_id: str | None = None) -> dict[str, Any] | None:
        """Load the most recent run metadata, optionally filtered by channel."""

        files = sorted(self.metadata_dir.glob("*.json"))
        if not files:
            return None

        for path in reversed(files):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if channel_id is None or payload.get("channel", {}).get("id") == channel_id:
                return payload
        return None

    def latest_uploaded_run(self, channel_id: str | None = None) -> dict[str, Any] | None:
        """Load the most recent run that contains a successful YouTube upload artifact."""

        files = sorted(self.metadata_dir.glob("*.json"))
        if not files:
            return None

        for path in reversed(files):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if channel_id is not None and payload.get("channel", {}).get("id") != channel_id:
                continue
            upload = payload.get("artifacts", {}).get("upload", {}) if isinstance(payload.get("artifacts", {}), dict) else {}
            if not isinstance(upload, dict):
                continue
            if str(upload.get("status", "")).strip() != "created":
                continue
            extra = upload.get("extra", {}) if isinstance(upload.get("extra", {}), dict) else {}
            if str(extra.get("video_id", "")).strip() or str(upload.get("path", "")).strip():
                return payload
        return None

    def list_runs(self, *, limit: int = 20, channel_id: str | None = None) -> list[dict[str, Any]]:
        """List recent runs for UI views."""

        files = sorted(self.metadata_dir.glob("*.json"), reverse=True)
        items: list[dict[str, Any]] = []
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if channel_id is None or payload.get("channel", {}).get("id") == channel_id:
                items.append(payload)
            if len(items) >= limit:
                break
        return items

    def prune_outputs(self) -> None:
        """Keep output directories compact enough for long-running server automation."""

        cutoff = datetime.now() - timedelta(hours=12)
        self._prune_directory(self.config.output_videos_dir, cutoff=cutoff, keep_recent=6)
        self._prune_directory(self.config.output_audio_dir, cutoff=cutoff, keep_recent=8)
        self._prune_directory(self.config.output_backgrounds_dir, cutoff=cutoff, keep_recent=10)
        self._prune_directory(self.config.output_thumbnails_dir, cutoff=cutoff, keep_recent=16)
        self._prune_directory(self.config.output_subtitles_dir, cutoff=cutoff, keep_recent=16)
        # Metadata is tiny and needed to recover weekly channel anchors after redeploys.
        metadata_cutoff = datetime.now() - timedelta(days=45)
        self._prune_directory(self.config.output_metadata_dir, cutoff=metadata_cutoff, keep_recent=120)
        cache_cutoff = datetime.now() - timedelta(days=14)
        self._prune_directory(self.config.openai_text_cache_dir, cutoff=cache_cutoff, keep_recent=48)
        self._prune_directory(self.config.openai_image_cache_dir, cutoff=cache_cutoff, keep_recent=64)

        self._enforce_size_limit(self.config.output_videos_dir, max_bytes=220 * 1024 * 1024, keep_recent=0)
        self._enforce_size_limit(self.config.output_audio_dir, max_bytes=96 * 1024 * 1024, keep_recent=0)
        self._enforce_size_limit(self.config.output_backgrounds_dir, max_bytes=64 * 1024 * 1024, keep_recent=2)
        self._enforce_size_limit(self.config.output_thumbnails_dir, max_bytes=32 * 1024 * 1024, keep_recent=4)
        self._enforce_size_limit(self.config.output_subtitles_dir, max_bytes=16 * 1024 * 1024, keep_recent=4)
        self._enforce_size_limit(self.config.output_metadata_dir, max_bytes=64 * 1024 * 1024, keep_recent=24)
        self._enforce_size_limit(self.config.openai_text_cache_dir, max_bytes=24 * 1024 * 1024, keep_recent=24)
        self._enforce_size_limit(self.config.openai_image_cache_dir, max_bytes=192 * 1024 * 1024, keep_recent=24)

    def _current_channel_id(self) -> str:
        return self.config.active_channel.id if self.config.active_channel else ""

    def _read_history(self) -> dict[str, Any]:
        if not self.history_path.exists():
            return {"topics": []}
        return json.loads(self.history_path.read_text(encoding="utf-8"))

    @staticmethod
    def _topic_signals(
        *,
        representative_title: str,
        keywords: Any,
        fallback: str,
    ) -> set[str]:
        stopwords = {
            "오늘",
            "이번",
            "지금",
            "한국",
            "정부",
            "정책",
            "뉴스",
            "브리핑",
            "쇼츠",
            "이슈",
            "핵심",
            "대상",
            "혜택",
            "지원",
            "지원금",
            "복지",
            "신청",
            "지급",
            "확인",
            "가능",
            "발표",
            "논의",
        }
        source = " ".join(
            [
                str(representative_title or ""),
                *[str(item) for item in keywords if str(item).strip()],
                str(fallback or ""),
            ]
        )
        tokens = []
        for raw in normalize_text(source).split():
            token = raw.strip()
            if len(token) < 2:
                continue
            if token in stopwords:
                continue
            tokens.append(token)
        return set(tokens)

    @staticmethod
    def _prune_directory(directory: Path, *, cutoff: datetime, keep_recent: int) -> None:
        if not directory.exists():
            return

        entries = sorted(directory.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
        for index, entry in enumerate(entries):
            if entry.is_file() and entry.stat().st_size == 0:
                StorageRepository._safe_unlink(entry)
                continue
            if index < keep_recent:
                continue
            modified_at = datetime.fromtimestamp(entry.stat().st_mtime)
            if modified_at >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                StorageRepository._safe_unlink(entry)

    @staticmethod
    def _enforce_size_limit(directory: Path, *, max_bytes: int, keep_recent: int) -> None:
        if not directory.exists() or max_bytes <= 0:
            return

        entries = sorted(
            [entry for entry in directory.iterdir() if entry.exists()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        protected = {entry.resolve() for entry in entries[:keep_recent]}
        total_size = sum(entry.stat().st_size for entry in entries if entry.is_file())
        if total_size <= max_bytes:
            return

        for entry in reversed(entries):
            if not entry.exists() or entry.resolve() in protected:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                total_size -= entry.stat().st_size
                StorageRepository._safe_unlink(entry)
            if total_size <= max_bytes:
                break

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            return

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
