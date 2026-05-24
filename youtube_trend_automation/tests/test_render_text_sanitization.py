from app.config import load_config
from app.models import GeneratedContent, RankedTopic
from app.render.thumbnail_builder import ThumbnailBuilder
from app.render.video_builder import VideoBuilder
from PIL import Image


def test_render_text_cleaners_strip_source_suffixes(tmp_path) -> None:
    config = load_config(tmp_path)

    thumbnail_builder = ThumbnailBuilder(config)
    video_builder = VideoBuilder(config)
    sample = "통합특별시민 '정부 20조 지원금' 운용 방안 들여보니 - 드림투데이"

    assert "드림투데이" not in thumbnail_builder._clean_display_text(sample)
    assert "드림투데이" not in video_builder._clean_display_text(sample)


def test_render_text_cleaners_strip_domains(tmp_path) -> None:
    config = load_config(tmp_path)

    thumbnail_builder = ThumbnailBuilder(config)
    video_builder = VideoBuilder(config)
    sample = "OECD 근원물가 전망 - v.daum.net"

    assert "v.daum.net" not in thumbnail_builder._clean_display_text(sample)
    assert "v.daum.net" not in video_builder._clean_display_text(sample)


def test_story_thumbnail_prefers_dedicated_thumbnail_prompt(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    builder = ThumbnailBuilder(config)

    class DummyContent:
        content_format = "longform_story"
        thumbnail_prompt = "photoreal korean emotional thumbnail"

    output_image = config.output_thumbnails_dir / "story_run.story-thumb-base.png"

    def fake_generate_image(*, prompt, output_path):
        assert "thumbnail" in prompt
        output_path.write_bytes(b"fake")
        return True

    builder.ai.generate_image = fake_generate_image  # type: ignore[method-assign]

    resolved = builder._resolve_base_image(DummyContent(), "story_run", None)

    assert resolved == output_image


def test_short_thumbnail_uses_vertical_render_size(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    builder = ThumbnailBuilder(config)
    content = GeneratedContent(
        topic=RankedTopic(
            normalized_topic="interest rate update",
            representative_title="Interest rate update",
            score=3.0,
            sources=["news"],
            mentions=["Interest rate update"],
            keywords=["interest", "rate", "update"],
        ),
        video_title="금리 이슈 정리",
        script="금리 이슈 정리",
        description="desc",
        tags=["#금리"],
        segments=["금리 이슈 정리"],
        content_format="short",
        detail_points=["금리 방향 다시 주목"],
        estimated_duration_seconds=35,
        preset_key="economy_news",
        thumbnail_text="금리 이번엔 멈출까",
    )

    artifact = builder.build(content, "short_thumb_test", None)
    image = Image.open(artifact.path)

    assert image.size == (config.render.width, config.render.height)
