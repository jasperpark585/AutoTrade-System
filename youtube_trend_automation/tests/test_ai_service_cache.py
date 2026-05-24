import base64
from io import BytesIO
import sys
import types
from pathlib import Path

from PIL import Image

from app.config import load_config
from app.generation.ai_service import OpenAIContentService
from app.models import ArtifactStatus, GeneratedContent, RankedTopic
from app.render.background_builder import BackgroundBuilder
from app.render.thumbnail_builder import ThumbnailBuilder


def _sample_topic() -> RankedTopic:
    return RankedTopic(
        normalized_topic="interest rate update",
        representative_title="Interest rate update",
        score=3.0,
        sources=["news"],
        mentions=["Interest rate update"],
        keywords=["interest", "rate", "update"],
    )


def _sample_short_content() -> GeneratedContent:
    return GeneratedContent(
        topic=_sample_topic(),
        video_title="금리 업데이트 정리",
        script="금리 업데이트 정리",
        description="desc",
        tags=["#금리"],
        segments=["금리 업데이트 정리"],
        content_format="short",
        detail_points=["금리 방향 다시 주목"],
        estimated_duration_seconds=35,
        preset_key="economy_news",
        background_prompt="photoreal economic newsroom background",
        thumbnail_prompt="photoreal breaking news thumbnail with dramatic contrast",
        thumbnail_text="금리 이번엔?",
    )


def test_openai_text_generation_uses_persistent_cache(tmp_path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    config.allow_network = True
    config.ai.enabled = True
    config.ai.use_text_generation = True
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    calls = {"text": 0}

    class FakeResponses:
        def create(self, *, model, input, tools=None):
            calls["text"] += 1
            return types.SimpleNamespace(output_text='{"title":"cached title","description":"cached description"}')

    class FakeOpenAI:
        def __init__(self, api_key):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))

    service = OpenAIContentService(config)
    first = service._generate_json("  prompt with   extra   spaces  ")
    second = OpenAIContentService(config)._generate_json("prompt with extra spaces")

    assert first == second
    assert calls["text"] == 1
    assert list(config.openai_text_cache_dir.glob("*.json"))


def test_openai_image_generation_uses_persistent_cache(tmp_path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    config.allow_network = True
    config.ai.enabled = True
    config.ai.use_image_generation = True
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    calls = {"image": 0}
    image_bytes = b"fake-image-bytes"

    class FakeResponses:
        def create(self, *, model, input, tools=None):
            calls["image"] += 1
            return types.SimpleNamespace(
                output=[
                    types.SimpleNamespace(
                        type="image_generation_call",
                        result=base64.b64encode(image_bytes).decode("ascii"),
                    )
                ]
            )

    class FakeOpenAI:
        def __init__(self, api_key):
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))

    first_output = config.output_backgrounds_dir / "first.png"
    second_output = config.output_backgrounds_dir / "second.png"

    service = OpenAIContentService(config)
    monkeypatch.setattr(service, "_should_use_free_story_images", lambda: False)
    assert service.generate_image(prompt="same prompt", output_path=first_output) is True
    second_service = OpenAIContentService(config)
    monkeypatch.setattr(second_service, "_should_use_free_story_images", lambda: False)
    assert second_service.generate_image(prompt=" same   prompt ", output_path=second_output) is True
    assert calls["image"] == 1
    assert second_output.read_bytes() == image_bytes
    assert list(config.openai_image_cache_dir.glob("*.png"))


def test_story_images_use_free_provider_without_openai_key(tmp_path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    config.allow_network = True
    config.ai.enabled = True
    config.ai.use_image_generation = True
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    image = Image.new("RGB", (1280, 720), "#345678")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    payload = buffer.getvalue()

    class FakeResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self.headers = {"Content-Type": "image/jpeg"}

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def fake_urlopen(request, timeout=0):
        return FakeResponse(payload)

    monkeypatch.setattr("app.generation.ai_service.urllib.request.urlopen", fake_urlopen)

    output = config.output_backgrounds_dir / "story_free.png"
    service = OpenAIContentService(config)

    assert service.generate_image(prompt="Korean senior emotional hospital hallway", output_path=output) is True
    assert output.exists()
    assert output.stat().st_size > 0
    assert list(config.openai_image_cache_dir.glob("*.png"))


def test_short_thumbnail_reuses_generated_background_image(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    builder = ThumbnailBuilder(config)
    content = _sample_short_content()

    background_path = config.output_backgrounds_dir / "shared-short-base.png"
    Image.new("RGB", (config.render.width, config.render.height), "#123456").save(background_path)
    background = ArtifactStatus(status="created", provider="openai-image", path=str(background_path))

    def should_not_run(*, prompt, output_path):
        raise AssertionError("short thumbnails should reuse the generated background image")

    builder.ai.generate_image = should_not_run  # type: ignore[method-assign]

    resolved = builder._resolve_base_image(content, "short_reuse", background)

    assert resolved == background_path


def test_news_background_uses_reusable_card_without_openai(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    builder = BackgroundBuilder(config)
    content = _sample_short_content()

    def should_not_run(*, prompt, output_path):
        raise AssertionError("news backgrounds should use the reusable card background")

    builder.ai.generate_image = should_not_run  # type: ignore[method-assign]

    artifact = builder.build(content, "reusable_news")

    assert artifact.status == "created"
    assert artifact.provider == "reusable-background"
    assert artifact.path is not None
    assert Path(artifact.path).exists()


def test_short_background_uses_reusable_card_without_openai(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    builder = BackgroundBuilder(config)
    content = _sample_short_content()

    artifact = builder.build(content, "short_base")

    assert artifact.status == "created"
    assert artifact.provider == "reusable-background"
    assert artifact.path is not None
    assert Path(artifact.path).exists()
