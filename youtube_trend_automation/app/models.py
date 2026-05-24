from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class TopicCandidate:
    """Raw topic candidate collected from a source."""

    title: str
    source: str
    url: str | None = None
    published_at: str | None = None
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RankedTopic:
    """Normalized topic after scoring and grouping."""

    normalized_topic: str
    representative_title: str
    score: float
    sources: list[str]
    mentions: list[str]
    keywords: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopicDetail:
    """Supporting detail collected for a selected topic."""

    title: str
    summary: str
    source: str
    url: str | None = None
    published_at: str | None = None


@dataclass(slots=True)
class StoryScene:
    """A longform story scene with its narration and visual prompt."""

    index: int
    title: str
    summary: str
    narration: str
    image_prompt: str
    duration_seconds: float = 0.0
    visual_hint: str = ""


@dataclass(slots=True)
class GeneratedContent:
    """Final copy generated for a channel-specific video."""

    topic: RankedTopic
    video_title: str
    script: str
    description: str
    tags: list[str]
    segments: list[str]
    content_format: str = "short"
    detail_points: list[str] = field(default_factory=list)
    estimated_duration_seconds: int = 0
    preset_key: str = ""
    background_prompt: str = ""
    thumbnail_prompt: str = ""
    contains_synthetic_media: bool = False
    altered_content_reason: str = ""
    thumbnail_text: str = ""
    hook_title: str = ""
    hook_script: str = ""
    hook_duration_seconds: float = 0.0
    hook_image_prompt: str = ""
    scenes: list[StoryScene] = field(default_factory=list)


@dataclass(slots=True)
class ArtifactStatus:
    """Status for generated files or external actions."""

    status: str
    provider: str
    path: str | None = None
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PipelineResult:
    """Outcome of a pipeline execution."""

    mode: str
    status: str
    selected_topic: str | None = None
    metadata_path: str | None = None
    audio: ArtifactStatus | None = None
    subtitles: ArtifactStatus | None = None
    video: ArtifactStatus | None = None
    background: ArtifactStatus | None = None
    thumbnail: ArtifactStatus | None = None
    upload: ArtifactStatus | None = None
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
