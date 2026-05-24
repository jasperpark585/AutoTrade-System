from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_CAPTION_CERTIFICATION = "이 콘텐츠는 미국 텔레비전에서 방영된 적이 없습니다."
DEFAULT_TUNNEL_NAME = "youtube-automation-studio"


@dataclass(slots=True)
class ChannelProfile:
    """Editable per-channel rules for generation and upload."""

    id: str
    display_name: str
    preset_key: str
    enabled: bool = True
    auto_generate: bool = True
    auto_render: bool = True
    auto_upload: bool = True
    channel_group: str = "news"
    youtube_channel_id: str = ""
    youtube_channel_title: str = ""
    privacy_status: str = "private"
    category_id: str = "25"
    default_language: str = "ko"
    default_audio_language: str = "ko-KR"
    made_for_kids: bool = False
    altered_content_mode: str = "auto"
    manual_contains_synthetic: bool = False
    caption_certification_hint: str = DEFAULT_CAPTION_CERTIFICATION
    use_ai_text: bool = True
    use_ai_images: bool = True
    use_ai_metadata: bool = True
    title_prefix: str = ""
    title_suffix: str = ""
    call_to_action: str = ""
    extra_instructions: str = ""
    topic_include_keywords: list[str] = field(default_factory=list)
    topic_exclude_keywords: list[str] = field(default_factory=list)
    content_duration_seconds: int = 170
    visual_style: str = "premium_news_graphic"
    manual_title: str = ""
    manual_description: str = ""
    manual_thumbnail_path: str = ""
    manual_background_path: str = ""
    manual_thumbnail_prompt: str = ""
    manual_background_prompt: str = ""
    youtube_client_secrets_file: str = ""
    youtube_token_file: str = ""
    schedule_enabled: bool = True
    schedule_interval_hours: int = 6
    daily_upload_time: str = ""
    daily_upload_times: list[str] = field(default_factory=list)
    story_scene_count: int = 7
    hook_duration_seconds: int = 40
    story_images_per_scene: int = 3
    burn_in_subtitles: bool = True
    background_music_path: str = ""
    background_music_volume: int = 18
    hook_motion_template: str = "dramatic_push"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AutomationSettings:
    """Global automation defaults and remote-runtime options."""

    schedule_hours: int = 6

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RemoteAccessSettings:
    """Global remote-access settings for the Studio UI."""

    enabled: bool = True
    mode: str = "quick"
    tunnel_name: str = DEFAULT_TUNNEL_NAME
    hostname: str = ""
    tunnel_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StudioSettings:
    """Persistent studio-wide settings."""

    version: int = 5
    active_channel_id: str = "news_default"
    channels: list[ChannelProfile] = field(default_factory=list)
    automation: AutomationSettings = field(default_factory=AutomationSettings)
    remote_access: RemoteAccessSettings = field(default_factory=RemoteAccessSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "active_channel_id": self.active_channel_id,
            "automation": self.automation.to_dict(),
            "remote_access": self.remote_access.to_dict(),
            "channels": [channel.to_dict() for channel in self.channels],
        }
