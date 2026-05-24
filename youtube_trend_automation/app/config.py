from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv
import yaml

from app.studio.channel_paths import resolve_youtube_token_file
from app.studio.models import ChannelProfile, DEFAULT_CAPTION_CERTIFICATION
from app.studio.presets import preset_by_key
from app.studio.store import StudioSettingsStore

SHORTS_RENDER_SIZE = (1080, 1920)
LONGFORM_RENDER_SIZE = (1280, 720)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(value: str, root: Path) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    return str(path)


def _env_path(name: str, default: str, root: Path) -> str:
    return _resolve_path(os.getenv(name, default).strip(), root)


@dataclass(slots=True)
class CollectionConfig:
    google_trends_limit: int = 10
    google_trends_geo: str = "KR"
    naver_sections: list[int] = field(default_factory=lambda: [100, 101, 102, 103, 104, 105])
    fallback_topics: list[str] = field(
        default_factory=lambda: [
            "AI 에이전트",
            "국내 증시",
            "반도체 주가",
            "서울 부동산",
            "건강보험 개편",
            "KBO 하이라이트",
        ]
    )


@dataclass(slots=True)
class ScoringConfig:
    top_k: int = 5
    duplicate_similarity_threshold: float = 0.82
    source_weights: dict[str, float] = field(
        default_factory=lambda: {
            "google_trends": 1.35,
            "naver_news": 1.15,
            "fallback": 0.85,
            "quotes": 1.0,
            "poems": 1.0,
            "stories": 1.05,
        }
    )


@dataclass(slots=True)
class GenerationConfig:
    language: str = "ko"
    max_tags: int = 12
    script_sections: int = 12
    title_prefix: str = "[오늘의 브리핑]"
    title_suffix: str = "핵심 요약"
    channel_name: str = "오늘의 브리핑"
    call_to_action: str = "매일 중요한 이슈를 빠르게 받고 싶다면 구독과 알림 설정을 눌러주세요."
    description_include_score: bool = False
    description_include_sources: bool = False
    description_include_generation_note: bool = False
    generation_note: str = "이 영상은 공개 가능한 데이터와 AI 생성 워크플로를 바탕으로 자동 제작되었습니다."
    target_duration_seconds: int = 180
    content_format: str = "short"
    story_scene_count: int = 7
    hook_duration_seconds: int = 40
    audience_hint: str = ""


@dataclass(slots=True)
class AIConfig:
    enabled: bool = True
    text_model: str = "gpt-5"
    short_text_model: str = "gpt-5-mini"
    story_text_model: str = "gpt-5"
    image_model: str = "gpt-5"
    use_text_generation: bool = True
    use_image_generation: bool = True


@dataclass(slots=True)
class TTSConfig:
    enabled: bool = True
    provider: str = "edge-tts"
    voice: str = "ko-KR-SunHiNeural"
    rate: str = "+0%"


@dataclass(slots=True)
class RenderConfig:
    enabled: bool = True
    width: int = 1080
    height: int = 1920
    fps: int = 30
    default_duration_seconds: int = 180


@dataclass(slots=True)
class ThumbnailConfig:
    enabled: bool = True
    width: int = 1280
    height: int = 720
    label: str = "TREND NOW"
    footer: str = "AUTO YOUTUBE STUDIO"


@dataclass(slots=True)
class YouTubeConfig:
    enabled: bool = False
    privacy_status: str = "private"
    category_id: str = "25"
    client_secrets_file: str = ""
    token_file: str = ""
    default_language: str = "ko"
    default_audio_language: str = "ko-KR"
    made_for_kids: bool = False
    altered_content_mode: str = "auto"
    manual_contains_synthetic: bool = False
    contains_synthetic_media: bool = False
    caption_certification_hint: str = DEFAULT_CAPTION_CERTIFICATION


@dataclass(slots=True)
class SchedulerConfig:
    hours: int = 6


@dataclass(slots=True)
class StudioConfig:
    settings_file: str = "data/studio_settings.json"


@dataclass(slots=True)
class AppConfig:
    project_root: Path
    config_path: Path
    env_path: Path
    timezone: str = "Asia/Seoul"
    log_level: str = "INFO"
    allow_network: bool = False
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    thumbnail: ThumbnailConfig = field(default_factory=ThumbnailConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    studio: StudioConfig = field(default_factory=StudioConfig)
    active_channel: ChannelProfile | None = None

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def outputs_dir(self) -> Path:
        return self.project_root / "outputs"

    @property
    def output_audio_dir(self) -> Path:
        return self.outputs_dir / "audio"

    @property
    def output_subtitles_dir(self) -> Path:
        return self.outputs_dir / "subtitles"

    @property
    def output_videos_dir(self) -> Path:
        return self.outputs_dir / "videos"

    @property
    def output_metadata_dir(self) -> Path:
        return self.outputs_dir / "metadata"

    @property
    def output_thumbnails_dir(self) -> Path:
        return self.outputs_dir / "thumbnails"

    @property
    def output_backgrounds_dir(self) -> Path:
        return self.outputs_dir / "backgrounds"

    @property
    def openai_cache_dir(self) -> Path:
        return self.data_dir / "openai_cache"

    @property
    def openai_text_cache_dir(self) -> Path:
        return self.openai_cache_dir / "text"

    @property
    def openai_image_cache_dir(self) -> Path:
        return self.openai_cache_dir / "images"

    @property
    def backgrounds_dir(self) -> Path:
        return self.project_root / "assets" / "backgrounds"

    @property
    def fonts_dir(self) -> Path:
        return self.project_root / "assets" / "fonts"

    @property
    def music_dir(self) -> Path:
        return self.project_root / "assets" / "music"

    @property
    def prompt_dir(self) -> Path:
        return self.project_root / "configs" / "prompts"

    @property
    def studio_settings_path(self) -> Path:
        return Path(self.studio.settings_file)

    def prepare_directories(self) -> None:
        for path in (
            self.data_dir,
            self.logs_dir,
            self.output_audio_dir,
            self.output_subtitles_dir,
            self.output_videos_dir,
            self.output_metadata_dir,
            self.output_thumbnails_dir,
            self.output_backgrounds_dir,
            self.openai_text_cache_dir,
            self.openai_image_cache_dir,
            self.backgrounds_dir,
            self.fonts_dir,
            self.music_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _fixed_render_size_for_channel(active_channel: ChannelProfile, content_format: str) -> tuple[int, int]:
    if content_format == "longform_story" or active_channel.channel_group == "story":
        return LONGFORM_RENDER_SIZE
    return SHORTS_RENDER_SIZE


def load_config(project_root: Path | None = None, *, channel_id: str | None = None) -> AppConfig:
    """Load YAML configuration, environment variables, and studio settings."""

    root = project_root or Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    config_path = root / "configs" / "config.yaml"

    load_dotenv(env_path, override=False)
    raw = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    app_data = raw.get("app", {})
    collection_data = raw.get("collection", {})
    scoring_data = raw.get("scoring", {})
    generation_data = raw.get("generation", {})
    ai_data = raw.get("ai", {})
    tts_data = raw.get("tts", {})
    render_data = raw.get("render", {})
    thumbnail_data = raw.get("thumbnail", {})
    youtube_data = raw.get("youtube", {})
    scheduler_data = raw.get("scheduler", {})
    studio_data = raw.get("studio", {})

    config = AppConfig(
        project_root=root,
        config_path=config_path,
        env_path=env_path,
        timezone=str(app_data.get("timezone", "Asia/Seoul")),
        log_level=os.getenv("APP_LOG_LEVEL", str(app_data.get("log_level", "INFO"))),
        allow_network=_env_bool("YTA_ALLOW_NETWORK", bool(app_data.get("allow_network", False))),
        collection=CollectionConfig(
            google_trends_limit=int(os.getenv("YTA_GOOGLE_TRENDS_LIMIT", collection_data.get("google_trends_limit", 10))),
            google_trends_geo=os.getenv("YTA_GOOGLE_TRENDS_GEO", str(collection_data.get("google_trends_geo", "KR"))),
            naver_sections=list(collection_data.get("naver_sections", CollectionConfig().naver_sections)),
            fallback_topics=list(collection_data.get("fallback_topics", CollectionConfig().fallback_topics)),
        ),
        scoring=ScoringConfig(
            top_k=int(os.getenv("YTA_TOP_K", scoring_data.get("top_k", 5))),
            duplicate_similarity_threshold=float(
                scoring_data.get("duplicate_similarity_threshold", 0.82)
            ),
            source_weights=dict(scoring_data.get("source_weights", ScoringConfig().source_weights)),
        ),
        generation=GenerationConfig(
            language=str(generation_data.get("language", "ko")),
            max_tags=int(generation_data.get("max_tags", 12)),
            script_sections=int(generation_data.get("script_sections", 12)),
            title_prefix=os.getenv("YTA_TITLE_PREFIX", str(generation_data.get("title_prefix", GenerationConfig().title_prefix))),
            title_suffix=os.getenv("YTA_TITLE_SUFFIX", str(generation_data.get("title_suffix", GenerationConfig().title_suffix))),
            channel_name=os.getenv("YTA_CHANNEL_NAME", str(generation_data.get("channel_name", GenerationConfig().channel_name))),
            call_to_action=os.getenv("YTA_CALL_TO_ACTION", str(generation_data.get("call_to_action", GenerationConfig().call_to_action))),
            description_include_score=_env_bool(
                "YTA_DESCRIPTION_INCLUDE_SCORE",
                bool(generation_data.get("description_include_score", False)),
            ),
            description_include_sources=_env_bool(
                "YTA_DESCRIPTION_INCLUDE_SOURCES",
                bool(generation_data.get("description_include_sources", False)),
            ),
            description_include_generation_note=_env_bool(
                "YTA_DESCRIPTION_INCLUDE_GENERATION_NOTE",
                bool(generation_data.get("description_include_generation_note", False)),
            ),
            generation_note=os.getenv("YTA_GENERATION_NOTE", str(generation_data.get("generation_note", GenerationConfig().generation_note))),
            target_duration_seconds=int(
                os.getenv("YTA_TARGET_DURATION_SECONDS", generation_data.get("target_duration_seconds", 180))
            ),
            content_format=str(generation_data.get("content_format", "short")),
            story_scene_count=int(generation_data.get("story_scene_count", 7)),
            hook_duration_seconds=int(generation_data.get("hook_duration_seconds", 40)),
            audience_hint=str(generation_data.get("audience_hint", "")),
        ),
        ai=AIConfig(
            enabled=_env_bool("YTA_AI_ENABLED", bool(ai_data.get("enabled", True))),
            short_text_model=os.getenv(
                "YTA_OPENAI_SHORT_TEXT_MODEL",
                str(ai_data.get("short_text_model", "gpt-5-mini")),
            ),
            story_text_model=os.getenv(
                "YTA_OPENAI_STORY_TEXT_MODEL",
                str(ai_data.get("story_text_model", "gpt-5")),
            ),
            text_model=os.getenv("YTA_OPENAI_TEXT_MODEL", str(ai_data.get("text_model", "gpt-5"))),
            image_model=os.getenv("YTA_OPENAI_IMAGE_MODEL", str(ai_data.get("image_model", "gpt-5"))),
            use_text_generation=_env_bool("YTA_USE_AI_TEXT", bool(ai_data.get("use_text_generation", True))),
            use_image_generation=_env_bool("YTA_USE_AI_IMAGES", bool(ai_data.get("use_image_generation", True))),
        ),
        tts=TTSConfig(
            enabled=_env_bool("YTA_TTS_ENABLED", bool(tts_data.get("enabled", True))),
            provider=str(tts_data.get("provider", "edge-tts")),
            voice=os.getenv("YTA_TTS_VOICE", str(tts_data.get("voice", "ko-KR-SunHiNeural"))),
            rate=os.getenv("YTA_TTS_RATE", str(tts_data.get("rate", "+0%"))),
        ),
        render=RenderConfig(
            enabled=_env_bool("YTA_RENDER_ENABLED", bool(render_data.get("enabled", True))),
            width=int(render_data.get("width", 1080)),
            height=int(render_data.get("height", 1920)),
            fps=int(render_data.get("fps", 30)),
            default_duration_seconds=int(render_data.get("default_duration_seconds", 180)),
        ),
        thumbnail=ThumbnailConfig(
            enabled=_env_bool("YTA_THUMBNAIL_ENABLED", bool(thumbnail_data.get("enabled", True))),
            width=int(thumbnail_data.get("width", 1280)),
            height=int(thumbnail_data.get("height", 720)),
            label=os.getenv("YTA_THUMBNAIL_LABEL", str(thumbnail_data.get("label", ThumbnailConfig().label))),
            footer=os.getenv("YTA_THUMBNAIL_FOOTER", str(thumbnail_data.get("footer", ThumbnailConfig().footer))),
        ),
        youtube=YouTubeConfig(
            enabled=_env_bool("YTA_UPLOAD_ENABLED", bool(youtube_data.get("enabled", False))),
            privacy_status=os.getenv("YTA_YOUTUBE_PRIVACY_STATUS", str(youtube_data.get("privacy_status", "private"))),
            category_id=os.getenv("YTA_YOUTUBE_CATEGORY_ID", str(youtube_data.get("category_id", "25"))),
            client_secrets_file=_env_path("YOUTUBE_CLIENT_SECRETS_FILE", str(youtube_data.get("client_secrets_file", "")), root),
            token_file=_env_path("YOUTUBE_TOKEN_FILE", str(youtube_data.get("token_file", "")), root),
            default_language=os.getenv("YTA_YOUTUBE_DEFAULT_LANGUAGE", str(youtube_data.get("default_language", "ko"))),
            default_audio_language=os.getenv(
                "YTA_YOUTUBE_DEFAULT_AUDIO_LANGUAGE",
                str(youtube_data.get("default_audio_language", "ko-KR")),
            ),
            made_for_kids=_env_bool("YTA_YOUTUBE_MADE_FOR_KIDS", bool(youtube_data.get("made_for_kids", False))),
            altered_content_mode=os.getenv(
                "YTA_YOUTUBE_ALTERED_CONTENT_MODE",
                str(youtube_data.get("altered_content_mode", "auto")),
            ),
            manual_contains_synthetic=_env_bool(
                "YTA_YOUTUBE_CONTAINS_SYNTHETIC_MEDIA",
                bool(youtube_data.get("contains_synthetic_media", False)),
            ),
            contains_synthetic_media=_env_bool(
                "YTA_YOUTUBE_CONTAINS_SYNTHETIC_MEDIA",
                bool(youtube_data.get("contains_synthetic_media", False)),
            ),
            caption_certification_hint=os.getenv(
                "YTA_YOUTUBE_CAPTION_CERTIFICATION_HINT",
                str(youtube_data.get("caption_certification_hint", DEFAULT_CAPTION_CERTIFICATION)),
            ),
        ),
        scheduler=SchedulerConfig(hours=int(os.getenv("YTA_SCHEDULE_HOURS", scheduler_data.get("hours", 6)))),
        studio=StudioConfig(
            settings_file=_resolve_path(str(studio_data.get("settings_file", "data/studio_settings.json")), root)
        ),
    )
    config.prepare_directories()

    store = StudioSettingsStore(Path(config.studio.settings_file))
    settings = store.load()
    config.scheduler.hours = max(1, int(settings.automation.schedule_hours))
    resolved_channel_id = channel_id or settings.active_channel_id
    active_channel = next((channel for channel in settings.channels if channel.id == resolved_channel_id), settings.channels[0])
    _apply_active_channel(config, active_channel)
    return config


def _apply_active_channel(config: AppConfig, active_channel: ChannelProfile) -> None:
    config.active_channel = active_channel
    preset = preset_by_key(active_channel.preset_key)
    config.generation.channel_name = active_channel.display_name or config.generation.channel_name
    config.ai.text_model = (
        config.ai.story_text_model if preset.content_format == "longform_story" else config.ai.short_text_model
    )

    if active_channel.title_prefix:
        config.generation.title_prefix = active_channel.title_prefix
    if active_channel.title_suffix:
        config.generation.title_suffix = active_channel.title_suffix
    if active_channel.call_to_action:
        config.generation.call_to_action = active_channel.call_to_action
    if active_channel.content_duration_seconds:
        config.generation.target_duration_seconds = active_channel.content_duration_seconds
        config.render.default_duration_seconds = active_channel.content_duration_seconds

    config.generation.content_format = preset.content_format
    config.generation.story_scene_count = max(1, int(active_channel.story_scene_count or preset.scene_count or 7))
    config.generation.hook_duration_seconds = max(
        20,
        int(active_channel.hook_duration_seconds or preset.hook_duration_seconds or 40),
    )
    config.generation.audience_hint = preset.audience_hint
    config.render.width, config.render.height = _fixed_render_size_for_channel(active_channel, preset.content_format)
    if preset.content_format == "longform_story" and config.tts.rate == TTSConfig().rate:
        config.tts.rate = "-8%"

    config.ai.use_text_generation = (
        config.ai.use_text_generation
        and active_channel.enabled
        and active_channel.auto_generate
        and active_channel.use_ai_text
        and active_channel.use_ai_metadata
    )
    config.ai.use_image_generation = (
        config.ai.use_image_generation
        and active_channel.enabled
        and active_channel.auto_generate
        and active_channel.use_ai_images
        and preset.content_format == "longform_story"
    )
    config.tts.enabled = config.tts.enabled and active_channel.enabled and active_channel.auto_render
    config.render.enabled = config.render.enabled and active_channel.enabled and active_channel.auto_render
    config.thumbnail.enabled = config.thumbnail.enabled and active_channel.enabled and active_channel.auto_render
    config.youtube.enabled = config.youtube.enabled and active_channel.enabled and active_channel.auto_upload

    config.youtube.privacy_status = active_channel.privacy_status or config.youtube.privacy_status
    config.youtube.category_id = active_channel.category_id or config.youtube.category_id
    config.youtube.default_language = active_channel.default_language or config.youtube.default_language
    config.youtube.default_audio_language = active_channel.default_audio_language or config.youtube.default_audio_language
    config.youtube.made_for_kids = active_channel.made_for_kids
    config.youtube.altered_content_mode = active_channel.altered_content_mode or config.youtube.altered_content_mode
    config.youtube.manual_contains_synthetic = active_channel.manual_contains_synthetic
    config.youtube.contains_synthetic_media = bool(active_channel.manual_contains_synthetic or preset.synthetic_media_default)
    config.youtube.caption_certification_hint = (
        active_channel.caption_certification_hint or config.youtube.caption_certification_hint
    )

    if active_channel.youtube_client_secrets_file:
        config.youtube.client_secrets_file = _resolve_path(active_channel.youtube_client_secrets_file, config.project_root)
    config.youtube.token_file = resolve_youtube_token_file(
        active_channel.youtube_token_file,
        active_channel.id,
        config.project_root,
    )
