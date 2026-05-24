from __future__ import annotations

import re

from app.config import AppConfig
from app.models import ArtifactStatus, GeneratedContent


class SubtitleBuilder:
    """Build an SRT file from generated segments or longform scenes."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def build(self, content: GeneratedContent, run_id: str) -> ArtifactStatus:
        output_path = self.config.output_subtitles_dir / f"{run_id}.srt"

        if content.content_format == "longform_story" and content.scenes:
            entries = self._story_entries(content)
        else:
            entries = self._short_entries(content)

        lines: list[str] = []
        start_seconds = 0.0
        for index, (segment, duration_seconds) in enumerate(entries, start=1):
            end_seconds = start_seconds + max(2.0, float(duration_seconds))
            lines.extend(
                [
                    str(index),
                    f"{self._format_time(start_seconds)} --> {self._format_time(end_seconds)}",
                    segment,
                    "",
                ]
            )
            start_seconds = end_seconds

        output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return ArtifactStatus(status="created", provider="srt", path=str(output_path))

    def _short_entries(self, content: GeneratedContent) -> list[tuple[str, float]]:
        segments = content.segments or self._split_script(content.script)
        duration = float(content.estimated_duration_seconds or self.config.generation.target_duration_seconds)
        segment_duration = max(2.5, duration / max(len(segments), 1))
        return [(segment, segment_duration) for segment in segments]

    def _story_entries(self, content: GeneratedContent) -> list[tuple[str, float]]:
        entries: list[tuple[str, float]] = []
        hook_duration = max(
            20.0,
            float(content.hook_duration_seconds or self._estimate_seconds(content.hook_script)),
        )
        hook_text = content.hook_script.strip() or content.script[:300]
        if hook_text:
            entries.extend(
                self._timed_story_captions(
                    hook_text,
                    total_duration=hook_duration,
                    minimum=2,
                    maximum=5,
                )
            )

        for scene in content.scenes:
            duration = float(scene.duration_seconds or self._estimate_seconds(scene.narration))
            entries.extend(
                self._timed_story_captions(
                    scene.narration or scene.summary or scene.title,
                    total_duration=duration,
                    minimum=4,
                    maximum=12,
                )
            )
        return entries

    def _timed_story_captions(
        self,
        text: str,
        *,
        total_duration: float,
        minimum: int,
        maximum: int,
    ) -> list[tuple[str, float]]:
        captions = self._story_captions(
            text,
            max_entries=max(minimum, min(maximum, max(1, int(float(total_duration) / 28)))),
        )
        if not captions:
            captions = [self._compact_story_caption(text)]
        base_duration = max(2.0, float(total_duration) / max(len(captions), 1))
        durations = [base_duration for _ in captions]
        return list(zip(captions, durations))

    def _story_captions(self, text: str, *, max_entries: int) -> list[str]:
        sentences = self._story_sentences(text)
        if not sentences:
            return []
        if len(sentences) <= max_entries:
            selected = sentences
        else:
            selected = []
            last_index = -1
            for position in range(max_entries):
                index = round(position * (len(sentences) - 1) / max(max_entries - 1, 1))
                if index != last_index:
                    selected.append(sentences[index])
                    last_index = index

        captions: list[str] = []
        for sentence in selected:
            compact = self._compact_story_caption(sentence)
            if compact and compact != (captions[-1] if captions else ""):
                captions.append(compact)
        return captions

    @staticmethod
    def _story_sentences(text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return []

        sentences = [
            piece.strip()
            for piece in re.split(r"(?<=[.!?])\s+|[\r\n]+", normalized)
            if piece.strip()
        ]
        if len(sentences) == 1:
            sentences = [
                piece.strip()
                for piece in re.split(r",\s+| 그리고 | 하지만 | 그런데 | 그래서 ", normalized)
                if piece.strip()
            ]
        return sentences

    @staticmethod
    def _compact_story_caption(text: str) -> str:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if not compact:
            return ""
        if len(compact) > 44:
            trimmed = compact[:44].rsplit(" ", 1)[0].strip() or compact[:44]
            compact = trimmed.rstrip(",. ") + "…"
        if len(compact) > 22:
            split_at = compact.rfind(" ", 0, 22)
            if split_at >= 10:
                first = compact[:split_at].strip()
                second = compact[split_at + 1 :].strip()
                if second:
                    compact = f"{first}\n{second}"
        return compact

    @staticmethod
    def _split_script(script: str) -> list[str]:
        pieces = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+|(?<=[가-힣])\n+", script) if piece.strip()]
        return pieces or [script]

    @staticmethod
    def _estimate_seconds(text: str) -> float:
        compact = re.sub(r"\s+", "", text)
        return max(20.0, len(compact) / 4.4)

    @staticmethod
    def _format_time(seconds: float) -> str:
        total_milliseconds = max(0, int(round(float(seconds) * 1000)))
        hours, remainder = divmod(total_milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, milliseconds = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"
