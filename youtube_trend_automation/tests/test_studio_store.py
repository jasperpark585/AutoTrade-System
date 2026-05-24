import json
from pathlib import Path

from app.config import load_config
from app.studio.models import RemoteAccessSettings
from app.studio.store import StudioSettingsStore


def test_studio_store_creates_default_channels_and_global_settings(tmp_path: Path) -> None:
    store = StudioSettingsStore(tmp_path / "data" / "studio_settings.json")
    settings = store.load()

    assert settings.channels
    assert settings.active_channel_id == settings.channels[0].id
    assert len(settings.channels) == 4
    assert settings.channels[0].display_name == "NewsTrend"
    assert settings.channels[1].display_name == "복지정보채널"
    assert settings.channels[2].display_name == "명언이간다"
    assert settings.channels[3].display_name == "황금시간의기록"
    assert settings.channels[0].youtube_token_file.endswith("youtube-token-news_default.json")
    assert settings.channels[1].youtube_token_file.endswith("youtube-token-welfare_default.json")
    assert settings.channels[2].youtube_token_file.endswith("youtube-token-insight_default.json")
    assert settings.channels[3].youtube_token_file.endswith("youtube-token-story_default.json")
    assert settings.channels[0].use_ai_text is True
    assert settings.channels[0].use_ai_metadata is True
    assert settings.channels[0].enabled is True
    assert settings.channels[0].auto_upload is True
    assert settings.channels[1].use_ai_images is False
    assert settings.channels[1].enabled is True
    assert settings.channels[2].use_ai_metadata is False
    assert settings.channels[2].auto_generate is True
    assert settings.channels[3].use_ai_text is True
    assert settings.channels[3].story_scene_count == 7
    assert settings.channels[3].hook_duration_seconds == 40
    assert settings.channels[3].story_images_per_scene == 1
    assert settings.channels[3].burn_in_subtitles is True
    assert settings.channels[3].background_music_volume == 18
    assert settings.channels[0].schedule_enabled is True
    assert settings.channels[0].daily_upload_times == ["06:00", "10:00", "14:00", "19:00"]
    assert settings.channels[1].daily_upload_times == ["06:00", "10:00", "14:00", "19:00"]
    assert settings.channels[2].schedule_enabled is False
    assert settings.channels[3].schedule_interval_hours == 168
    assert settings.automation.schedule_hours == 6
    assert settings.remote_access.mode == "quick"


def test_studio_store_persists_remote_access_and_scheduler(tmp_path: Path) -> None:
    store = StudioSettingsStore(tmp_path / "data" / "studio_settings.json")
    settings = store.load()
    settings.automation.schedule_hours = 12
    settings.remote_access = RemoteAccessSettings(
        enabled=True,
        mode="named",
        tunnel_name="studio-fixed",
        hostname="studio.example.com",
        tunnel_token="token-value",
    )
    store.save(settings)

    loaded = store.load()
    assert loaded.automation.schedule_hours == 12
    assert loaded.remote_access.mode == "named"
    assert loaded.remote_access.hostname == "studio.example.com"
    assert loaded.remote_access.tunnel_token == "token-value"


def test_load_config_uses_studio_schedule_hours(tmp_path: Path) -> None:
    store = StudioSettingsStore(tmp_path / "data" / "studio_settings.json")
    settings = store.load()
    settings.automation.schedule_hours = 9
    store.save(settings)

    config = load_config(tmp_path)

    assert config.scheduler.hours == 9
    assert config.youtube.token_file.endswith("youtube-token-news_default.json")


def test_load_config_resolves_channel_specific_token_file(tmp_path: Path) -> None:
    store = StudioSettingsStore(tmp_path / "data" / "studio_settings.json")
    settings = store.load()
    settings.active_channel_id = "insight_default"
    store.save(settings)

    config = load_config(tmp_path)

    assert config.active_channel is not None
    assert config.active_channel.id == "insight_default"
    assert config.youtube.token_file.endswith("youtube-token-insight_default.json")


def test_load_config_applies_story_channel_longform_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")

    assert config.active_channel is not None
    assert config.active_channel.id == "story_default"
    assert config.generation.channel_name == "황금시간의기록"
    assert config.generation.content_format == "longform_story"
    assert config.generation.story_scene_count == 7
    assert config.generation.hook_duration_seconds == 40
    assert (config.render.width, config.render.height) == (1280, 720)
    assert config.tts.rate == "-8%"
    assert config.youtube.token_file.endswith("youtube-token-story_default.json")
    assert config.active_channel.schedule_interval_hours == 168


def test_load_config_keeps_shorts_channels_vertical(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="insight_default")

    assert config.active_channel is not None
    assert config.active_channel.id == "insight_default"
    assert config.generation.content_format == "short"
    assert (config.render.width, config.render.height) == (1080, 1920)
    assert config.ai.text_model == "gpt-5-mini"
    assert config.ai.use_image_generation is False


def test_load_config_disables_render_and_upload_for_disabled_channel(tmp_path: Path) -> None:
    store = StudioSettingsStore(tmp_path / "data" / "studio_settings.json")
    settings = store.load()
    welfare = next(channel for channel in settings.channels if channel.id == "welfare_default")
    welfare.enabled = False
    welfare.auto_generate = False
    welfare.auto_render = False
    welfare.auto_upload = False
    store.save(settings)

    config = load_config(tmp_path, channel_id="welfare_default")

    assert config.active_channel is not None
    assert config.active_channel.enabled is False
    assert config.render.enabled is False
    assert config.youtube.enabled is False


def test_studio_store_migrates_legacy_shared_token_file(tmp_path: Path) -> None:
    payload = {
        "active_channel_id": "insight_default",
        "automation": {"schedule_hours": 6},
        "remote_access": {"enabled": True, "mode": "quick", "tunnel_name": "youtube-automation-studio"},
        "channels": [
            {
                "id": "insight_default",
                "display_name": "quotes",
                "preset_key": "quotes_daily",
                "youtube_token_file": "./data/youtube-token.json",
            }
        ],
    }
    settings_path = tmp_path / "data" / "studio_settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = StudioSettingsStore(settings_path).load()

    insight_channel = next(channel for channel in loaded.channels if channel.id == "insight_default")
    assert insight_channel.youtube_token_file.endswith("youtube-token-insight_default.json")


def test_load_config_copies_legacy_shared_token_to_news_channel_file(tmp_path: Path) -> None:
    legacy_token = tmp_path / "data" / "youtube-token.json"
    legacy_token.parent.mkdir(parents=True, exist_ok=True)
    legacy_token.write_text('{"token":"legacy"}', encoding="utf-8")

    config = load_config(tmp_path, channel_id="news_default")

    copied_token = Path(config.youtube.token_file)
    assert copied_token.name == "youtube-token-news_default.json"
    assert copied_token.exists()
    assert copied_token.read_text(encoding="utf-8") == '{"token":"legacy"}'
