from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import AppConfig
from app.models import ArtifactStatus, GeneratedContent
from app.utils.logging import get_logger

LOGGER = get_logger(__name__)


class EdgeTTSProvider:
    """Synthesize Korean speech via edge-tts with safe fallbacks."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def synthesize(self, content: GeneratedContent, run_id: str) -> ArtifactStatus:
        if not self.config.tts.enabled:
            return ArtifactStatus(status="skipped", provider="edge-tts", message="TTS disabled")

        if content.content_format == "longform_story" and content.scenes:
            return self._synthesize_story_segments(content, run_id)
        return self._synthesize_single_track(content.script, self.config.output_audio_dir / f"{run_id}.mp3")

    def _synthesize_story_segments(self, content: GeneratedContent, run_id: str) -> ArtifactStatus:
        output_dir = self.config.output_audio_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        parts = [("hook", content.hook_script or content.script[:500])]
        parts.extend((f"scene_{scene.index:02}", scene.narration) for scene in content.scenes)

        created_segments: list[dict[str, str]] = []
        any_mocked = False
        for label, text in parts:
            output_path = output_dir / f"{label}.mp3"
            result = self._synthesize_single_track(text, output_path)
            if result.status != "created":
                any_mocked = True
            created_segments.append(
                {
                    "label": label,
                    "path": result.path or str(output_path.with_suffix(".mock.txt")),
                    "status": result.status,
                }
            )

        return ArtifactStatus(
            status="mocked" if any_mocked else "created",
            provider="edge-tts",
            path=str(output_dir),
            message="Story narration segments synthesized." if not any_mocked else "Some story narration segments were mocked.",
            extra={"segments": created_segments},
        )

    def _synthesize_single_track(self, text: str, output_path: Path) -> ArtifactStatus:
        fallback_note = output_path.with_suffix(".mock.txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not text.strip():
            fallback_note.write_text("No narration text was available.\n", encoding="utf-8")
            return ArtifactStatus(
                status="mocked",
                provider="edge-tts",
                path=str(fallback_note),
                message="No narration text was available; created mock note.",
            )

        if not self.config.allow_network:
            fallback_note.write_text(
                "edge-tts skipped because YTA_ALLOW_NETWORK=false.\n"
                f"Requested voice: {self.config.tts.voice}\n",
                encoding="utf-8",
            )
            return ArtifactStatus(
                status="mocked",
                provider="edge-tts",
                path=str(fallback_note),
                message="Network disabled; created mock TTS note instead of MP3.",
            )

        try:
            import edge_tts
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("edge-tts import failed: %s", exc)
            fallback_note.write_text(f"edge-tts unavailable: {exc}\n", encoding="utf-8")
            return ArtifactStatus(
                status="mocked",
                provider="edge-tts",
                path=str(fallback_note),
                message="edge-tts unavailable; created mock note.",
            )

        async def _save() -> None:
            communicator = edge_tts.Communicate(
                text,
                voice=self.config.tts.voice,
                rate=self.config.tts.rate,
            )
            await communicator.save(output_path)

        try:
            asyncio.run(_save())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_save())
            loop.close()
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("edge-tts synthesis failed: %s", exc)
            fallback_note.write_text(f"edge-tts failed: {exc}\n", encoding="utf-8")
            return ArtifactStatus(
                status="mocked",
                provider="edge-tts",
                path=str(fallback_note),
                message="edge-tts failed; created mock note.",
            )

        return ArtifactStatus(status="created", provider="edge-tts", path=str(output_path))
