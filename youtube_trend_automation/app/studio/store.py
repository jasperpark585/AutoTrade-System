from __future__ import annotations

import json
from pathlib import Path

from app.studio.channel_paths import default_youtube_token_file, stored_youtube_token_file
from app.studio.models import (
    AutomationSettings,
    ChannelProfile,
    DEFAULT_CAPTION_CERTIFICATION,
    DEFAULT_TUNNEL_NAME,
    RemoteAccessSettings,
    StudioSettings,
)
from app.studio.presets import preset_by_key


DEFAULT_CHANNEL_SPECS = (
    {
        "channel_id": "news_default",
        "display_name": "NewsTrend",
        "preset_key": "economy_news",
        "youtube_channel_id": "UCtMnPbVMIJP09T-d_NHLkbQ",
        "youtube_channel_title": "NewsTrend",
        "privacy_status": "public",
    },
    {
        "channel_id": "welfare_default",
        "display_name": "복지정보채널",
        "preset_key": "welfare_news",
        "youtube_channel_id": "UCVSxWJTq7iuesgIyxrO8HTA",
        "youtube_channel_title": "복지정보채널",
        "privacy_status": "public",
    },
    {
        "channel_id": "insight_default",
        "display_name": "명언이간다",
        "preset_key": "quotes_daily",
        "youtube_channel_id": "UCWsjdJoJcr-Szv4EZabE8OQ",
        "youtube_channel_title": "명언이간다",
        "privacy_status": "public",
    },
    {
        "channel_id": "story_default",
        "display_name": "황금시간의기록",
        "preset_key": "senior_story_longform",
        "youtube_channel_id": "UCqBGwk38wrT5ac0If2HD1Hw",
        "youtube_channel_title": "황금시간의기록",
        "privacy_status": "public",
    },
)

DEFAULT_SHORTS_UPLOAD_TIMES = ["06:00", "10:00", "14:00", "19:00"]


class StudioSettingsStore:
    """Load and persist editable studio settings."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> StudioSettings:
        if not self.path.exists():
            settings = self.default_settings()
            self.save(settings)
            return settings

        payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        automation_payload = payload.get("automation", {})
        remote_payload = payload.get("remote_access", {})
        default_interval_hours = max(1, int(automation_payload.get("schedule_hours", 6)))

        channels = [
            self._channel_from_dict(item, default_interval_hours=default_interval_hours)
            for item in payload.get("channels", [])
            if isinstance(item, dict)
        ]
        channels = self._merge_missing_default_channels(channels, default_interval_hours=default_interval_hours)
        if not channels:
            settings = self.default_settings()
            self.save(settings)
            return settings

        return StudioSettings(
            version=max(5, int(payload.get("version", 5))),
            active_channel_id=str(payload.get("active_channel_id", channels[0].id)),
            automation=AutomationSettings(schedule_hours=default_interval_hours),
            remote_access=RemoteAccessSettings(
                enabled=bool(remote_payload.get("enabled", True)),
                mode=self._normalize_remote_mode(str(remote_payload.get("mode", "quick"))),
                tunnel_name=str(remote_payload.get("tunnel_name", DEFAULT_TUNNEL_NAME)).strip() or DEFAULT_TUNNEL_NAME,
                hostname=str(remote_payload.get("hostname", "")).strip(),
                tunnel_token=str(remote_payload.get("tunnel_token", "")).strip(),
            ),
            channels=channels,
        )

    def save(self, settings: StudioSettings) -> None:
        self.path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def active_channel(self) -> ChannelProfile:
        settings = self.load()
        for channel in settings.channels:
            if channel.id == settings.active_channel_id:
                return channel
        return settings.channels[0]

    @staticmethod
    def default_settings() -> StudioSettings:
        default_channels = StudioSettingsStore._default_channels(default_interval_hours=6)
        return StudioSettings(
            version=5,
            active_channel_id=default_channels[0].id,
            channels=default_channels,
            automation=AutomationSettings(schedule_hours=6),
            remote_access=RemoteAccessSettings(
                enabled=True,
                mode="quick",
                tunnel_name=DEFAULT_TUNNEL_NAME,
            ),
        )

    @staticmethod
    def _default_channels(*, default_interval_hours: int) -> list[ChannelProfile]:
        return [
            StudioSettingsStore._profile_from_preset(default_interval_hours=default_interval_hours, **spec)
            for spec in DEFAULT_CHANNEL_SPECS
        ]

    @staticmethod
    def _merge_missing_default_channels(
        channels: list[ChannelProfile],
        *,
        default_interval_hours: int,
    ) -> list[ChannelProfile]:
        if not channels:
            return StudioSettingsStore._default_channels(default_interval_hours=default_interval_hours)

        existing_by_id = {channel.id: channel for channel in channels}
        merged: list[ChannelProfile] = []
        for default in StudioSettingsStore._default_channels(default_interval_hours=default_interval_hours):
            merged.append(existing_by_id.pop(default.id, default))
        merged.extend(existing_by_id.values())
        return merged

    @staticmethod
    def _profile_from_preset(
        channel_id: str,
        display_name: str,
        preset_key: str,
        youtube_channel_id: str,
        youtube_channel_title: str,
        privacy_status: str,
        *,
        default_interval_hours: int,
    ) -> ChannelProfile:
        preset = preset_by_key(preset_key)
        ai_text_enabled = preset.key in {"senior_story_longform", "economy_news"}
        ai_image_enabled = preset.key == "senior_story_longform"
        schedule_interval_hours = 168 if preset.key == "senior_story_longform" else max(1, int(default_interval_hours))
        daily_upload_times = DEFAULT_SHORTS_UPLOAD_TIMES if preset.key in {"economy_news", "welfare_news"} else []
        return ChannelProfile(
            id=channel_id,
            display_name=display_name,
            preset_key=preset.key,
            enabled=True,
            auto_generate=True,
            auto_render=True,
            auto_upload=True,
            channel_group=preset.group,
            youtube_channel_id=youtube_channel_id,
            youtube_channel_title=youtube_channel_title,
            privacy_status=privacy_status,
            category_id=preset.category_id,
            title_prefix=preset.title_prefix,
            title_suffix=preset.title_suffix,
            call_to_action=preset.call_to_action,
            visual_style=preset.visual_style,
            content_duration_seconds=preset.content_duration_seconds,
            topic_include_keywords=list(preset.topic_include_keywords),
            topic_exclude_keywords=list(preset.topic_exclude_keywords),
            caption_certification_hint=DEFAULT_CAPTION_CERTIFICATION,
            use_ai_text=ai_text_enabled,
            use_ai_images=ai_image_enabled,
            use_ai_metadata=ai_text_enabled,
            youtube_client_secrets_file="./secrets/client_secret.json",
            youtube_token_file=default_youtube_token_file(channel_id),
            schedule_enabled=preset.key != "quotes_daily",
            schedule_interval_hours=schedule_interval_hours,
            daily_upload_time=daily_upload_times[0] if len(daily_upload_times) == 1 else "",
            daily_upload_times=list(daily_upload_times),
            story_scene_count=max(1, int(preset.scene_count or 7)),
            hook_duration_seconds=max(20, int(preset.hook_duration_seconds or 40)),
            story_images_per_scene=1,
            burn_in_subtitles=True,
            background_music_volume=18,
            hook_motion_template="dramatic_push",
        )

    @staticmethod
    def _channel_from_dict(payload: dict[str, object], *, default_interval_hours: int) -> ChannelProfile:
        channel_id = str(payload.get("id", "channel"))
        preset_key = str(payload.get("preset_key", "economy_news"))
        preset = preset_by_key(preset_key)
        ai_text_enabled = preset.key == "senior_story_longform"
        ai_image_enabled = preset.key == "senior_story_longform"
        fallback_interval_hours = 168 if preset.key == "senior_story_longform" else default_interval_hours
        default_daily_times = DEFAULT_SHORTS_UPLOAD_TIMES if preset.key in {"economy_news", "welfare_news"} else []
        schedule_enabled = bool(payload.get("schedule_enabled", preset.key != "quotes_daily"))
        raw_daily_times = payload.get("daily_upload_times", [])
        if isinstance(raw_daily_times, list):
            daily_upload_times = [str(item).strip() for item in raw_daily_times if str(item).strip()]
        else:
            daily_upload_times = []
        legacy_daily_time = str(payload.get("daily_upload_time", "")).strip()
        if not daily_upload_times and legacy_daily_time:
            daily_upload_times = [legacy_daily_time]
        if not daily_upload_times and preset.key in {"economy_news", "welfare_news"}:
            daily_upload_times = list(default_daily_times)
        return ChannelProfile(
            id=channel_id,
            display_name=str(payload.get("display_name", StudioSettingsStore._default_display_name(channel_id, preset.key))).strip()
            or StudioSettingsStore._default_display_name(channel_id, preset.key),
            preset_key=preset.key,
            enabled=bool(payload.get("enabled", True)),
            auto_generate=bool(payload.get("auto_generate", True)),
            auto_render=bool(payload.get("auto_render", True)),
            auto_upload=bool(payload.get("auto_upload", True)),
            channel_group=str(payload.get("channel_group", preset.group)).strip() or preset.group,
            youtube_channel_id=str(payload.get("youtube_channel_id", "")).strip(),
            youtube_channel_title=str(payload.get("youtube_channel_title", "")).strip(),
            privacy_status=str(payload.get("privacy_status", "private")).strip() or "private",
            category_id=str(payload.get("category_id", preset.category_id)).strip() or preset.category_id,
            default_language=str(payload.get("default_language", "ko")).strip() or "ko",
            default_audio_language=str(payload.get("default_audio_language", "ko-KR")).strip() or "ko-KR",
            made_for_kids=bool(payload.get("made_for_kids", False)),
            altered_content_mode=str(payload.get("altered_content_mode", "auto")).strip() or "auto",
            manual_contains_synthetic=bool(payload.get("manual_contains_synthetic", False)),
            caption_certification_hint=str(
                payload.get("caption_certification_hint", DEFAULT_CAPTION_CERTIFICATION)
            ).strip()
            or DEFAULT_CAPTION_CERTIFICATION,
            use_ai_text=bool(payload.get("use_ai_text", ai_text_enabled)),
            use_ai_images=ai_image_enabled and bool(payload.get("use_ai_images", ai_image_enabled)),
            use_ai_metadata=bool(payload.get("use_ai_metadata", ai_text_enabled)),
            title_prefix=str(payload.get("title_prefix", preset.title_prefix)).strip(),
            title_suffix=str(payload.get("title_suffix", preset.title_suffix)).strip(),
            call_to_action=str(payload.get("call_to_action", preset.call_to_action)).strip(),
            extra_instructions=str(payload.get("extra_instructions", "")).strip(),
            topic_include_keywords=list(payload.get("topic_include_keywords", list(preset.topic_include_keywords))),
            topic_exclude_keywords=list(payload.get("topic_exclude_keywords", list(preset.topic_exclude_keywords))),
            content_duration_seconds=max(45, int(payload.get("content_duration_seconds", preset.content_duration_seconds))),
            visual_style=str(payload.get("visual_style", preset.visual_style)).strip() or preset.visual_style,
            manual_title=str(payload.get("manual_title", "")).strip(),
            manual_description=str(payload.get("manual_description", "")).strip(),
            manual_thumbnail_path=str(payload.get("manual_thumbnail_path", "")).strip(),
            manual_background_path=str(payload.get("manual_background_path", "")).strip(),
            manual_thumbnail_prompt=str(payload.get("manual_thumbnail_prompt", "")).strip(),
            manual_background_prompt=str(payload.get("manual_background_prompt", "")).strip(),
            youtube_client_secrets_file=str(payload.get("youtube_client_secrets_file", "./secrets/client_secret.json")).strip()
            or "./secrets/client_secret.json",
            youtube_token_file=stored_youtube_token_file(str(payload.get("youtube_token_file", "")), channel_id),
            schedule_enabled=schedule_enabled,
            schedule_interval_hours=max(1, int(payload.get("schedule_interval_hours", fallback_interval_hours))),
            daily_upload_time=daily_upload_times[0] if len(daily_upload_times) == 1 else legacy_daily_time,
            daily_upload_times=daily_upload_times,
            story_scene_count=max(1, int(payload.get("story_scene_count", preset.scene_count or 7))),
            hook_duration_seconds=max(20, int(payload.get("hook_duration_seconds", preset.hook_duration_seconds or 40))),
            story_images_per_scene=max(1, int(payload.get("story_images_per_scene", 1))),
            burn_in_subtitles=bool(payload.get("burn_in_subtitles", True)),
            background_music_path=str(payload.get("background_music_path", "")).strip(),
            background_music_volume=max(0, min(100, int(payload.get("background_music_volume", 18)))),
            hook_motion_template=str(payload.get("hook_motion_template", "dramatic_push")).strip() or "dramatic_push",
        )

    @staticmethod
    def _normalize_remote_mode(value: str) -> str:
        normalized = value.strip().lower()
        return normalized if normalized in {"quick", "named"} else "quick"

    @staticmethod
    def _default_display_name(channel_id: str, preset_key: str) -> str:
        for spec in DEFAULT_CHANNEL_SPECS:
            if spec["channel_id"] == channel_id:
                return str(spec["display_name"])
        return preset_by_key(preset_key).label
