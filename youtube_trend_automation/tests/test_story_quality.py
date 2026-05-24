from pathlib import Path

from app.collectors.life_content import LifeContentCollector
from app.config import load_config
from app.generation.ai_service import OpenAIContentService
from app.generation.content_generator import ContentGenerator
from app.models import GeneratedContent, RankedTopic, StoryScene, TopicDetail


def test_news_title_strips_slug_like_source_tokens(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="housing update",
        representative_title="housing update - v-daum-net",
        score=3.0,
        sources=["google_trends"],
        mentions=["housing update"],
        keywords=["housing", "update"],
    )

    title = generator._build_title(topic)

    assert "v-daum-net" not in title.lower()
    assert "daum" not in title.lower()


def test_story_finalize_removes_repeated_blocks(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    generator = ContentGenerator(config)
    generator.ai.expand_story_package = lambda **kwargs: None  # type: ignore[method-assign]
    topic = RankedTopic(
        normalized_topic="family story",
        representative_title="가족이 다시 마주 앉게 된 사연",
        score=4.0,
        sources=["stories"],
        mentions=["family story"],
        keywords=["가족", "사연"],
    )
    repeated_block = "그날 밤 그는 아무 말도 하지 못한 채 창밖만 바라봤다."
    scenes = [
        StoryScene(
            index=1,
            title="시작",
            summary="첫 번째 균열",
            narration=f"{repeated_block}\n\n낯선 침묵이 집 안을 더 무겁게 만들었다.\n\n아침 식탁에서도 아무도 먼저 말을 꺼내지 못했다.",
            image_prompt="16:9 photoreal Korean home, no text",
        ),
        StoryScene(
            index=2,
            title="오해",
            summary="서로 다른 마음",
            narration=f"{repeated_block}\n\n둘째 날에는 오래 묵은 오해가 처음으로 말 밖으로 나왔다.\n\n작은 한마디가 쌓인 서운함을 건드렸다.",
            image_prompt="16:9 photoreal Korean apartment, no text",
        ),
        StoryScene(
            index=3,
            title="반전",
            summary="숨겨 둔 진심",
            narration=f"{repeated_block}\n\n세 번째 밤에야 감춰 둔 편지 한 장이 식탁 위에 놓였다.\n\n그제야 모두가 같은 상처를 다른 말로 버티고 있었다는 걸 알았다.",
            image_prompt="16:9 photoreal Korean living room, no text",
        ),
    ]
    content = GeneratedContent(
        topic=topic,
        video_title="가족이 다시 마주 앉게 된 사연",
        script="",
        description="",
        tags=[],
        segments=[],
        content_format="longform_story",
        detail_points=[],
        estimated_duration_seconds=config.generation.target_duration_seconds,
        preset_key="senior_story_longform",
        background_prompt="",
        thumbnail_prompt="",
        thumbnail_text="그날 밤의 진실",
        hook_script="그날 저녁, 모두가 숨기고 싶었던 말이 한꺼번에 터져 나왔다.",
        scenes=scenes,
    )
    details = [
        TopicDetail(title="배경", summary="가족이 오래된 오해를 안고 같은 집에서 버티고 있었다.", source="stories"),
        TopicDetail(title="갈등", summary="편지 한 장이 감춰 둔 진심을 끌어냈다.", source="stories"),
    ]

    finalized = generator._finalize_story_content(content, details)
    combined_narration = "\n".join(scene.narration for scene in finalized.scenes)

    assert combined_narration.count(repeated_block) == 1
    assert finalized.content_format == "longform_story"


def test_story_thumbnail_text_is_short_and_source_free(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="retirement story",
        representative_title="은퇴 후 다시 웃게 된 아버지 - v-daum-net",
        score=4.0,
        sources=["stories"],
        mentions=["retirement story"],
        keywords=["은퇴", "아버지"],
    )

    text = generator._resolve_thumbnail_text("", topic)

    assert len(text) <= 18
    assert "daum" not in text.lower()


def test_story_collector_rotates_story_themes(monkeypatch, tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    collector = LifeContentCollector(config)

    monkeypatch.setattr(collector, "_story_rotation_seed", lambda: 0)
    first_batch = [item.title for item in collector.collect("stories", limit=5)]

    monkeypatch.setattr(collector, "_story_rotation_seed", lambda: 7)
    second_batch = [item.title for item in collector.collect("stories", limit=5)]

    assert len(set(first_batch)) == 5
    assert len(set(second_batch)) == 5
    assert first_batch != second_batch


def test_story_prompt_requires_high_view_pattern_inspiration_without_copying(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    service = OpenAIContentService(config)
    captured: dict[str, str] = {}

    monkeypatch.setattr(service, "available", lambda: True)

    def _capture(prompt: str) -> dict[str, str]:
        captured["prompt"] = prompt
        return {}

    monkeypatch.setattr(service, "_generate_json", _capture)

    topic = RankedTopic(
        normalized_topic="late life family conflict",
        representative_title="평생 가족만 위해 산 어머니가 마지막에 자신을 선택한 사연",
        score=4.5,
        sources=["stories"],
        mentions=["family conflict"],
        keywords=["가족", "어머니", "선택"],
    )
    details = [
        TopicDetail(title="고조회수 제목 패턴", summary="관계와 선택의 충돌을 제목에서 먼저 보여줍니다.", source="stories"),
        TopicDetail(title="초반 훅", summary="가장 강한 감정 충돌을 초반 30초 안에 보여줍니다.", source="stories"),
    ]

    service.generate_story_package(topic=topic, details=details)
    prompt = captured["prompt"].lower()

    assert "around one million views" in prompt
    assert "never copy any real youtube title" in prompt
    assert "rotate across different high-performing story buckets" in prompt


def test_story_visual_prompts_feel_like_photoreal_drama_assets(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="hospital reunion",
        representative_title="병원 복도에서 다시 마주친 남매의 사연",
        score=4.1,
        sources=["stories"],
        mentions=["hospital reunion"],
        keywords=["병원", "남매", "사연"],
    )

    scene_prompt = generator._story_image_prompt(topic, "복도에서 멈춘 순간", "늦게 도착한 동생이 병원 복도 끝에서 형을 다시 마주본다.")
    thumbnail_prompt = generator._build_thumbnail_prompt(topic)

    scene_prompt_lower = scene_prompt.lower()
    thumbnail_prompt_lower = thumbnail_prompt.lower()

    assert "photoreal" in scene_prompt_lower
    assert "korean drama" in scene_prompt_lower
    assert "no text" in scene_prompt_lower
    assert "no collage" in scene_prompt_lower
    assert "close-up" in thumbnail_prompt_lower
    assert "text-safe space" in thumbnail_prompt_lower
    assert "no collage" in thumbnail_prompt_lower


def test_story_detail_fragments_drop_meta_youtube_guidance(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="late life conflict",
        representative_title="황혼에 다시 사랑을 시작한 어머니를 가족이 반대하는 사연",
        score=4.1,
        sources=["stories"],
        mentions=["late life conflict"],
        keywords=["황혼", "어머니", "가족"],
    )
    details = [
        TopicDetail(title="고조회수 제목 패턴", summary="관계와 선택의 충돌이 한눈에 보이는 제목 구조를 참고합니다.", source="stories"),
        TopicDetail(title="초반 훅", summary="가족 식사 자리에서 갈등이 터지는 장면으로 시작합니다.", source="stories"),
        TopicDetail(title="실제 사정", summary="어머니는 혼자 남은 노후가 두려워 끝내 자신의 삶을 선택하려 했습니다.", source="stories"),
    ]

    fragments = generator._story_detail_fragments(details, topic, minimum=3)
    combined = " ".join(fragments)

    assert "고조회수" not in combined
    assert "제목 구조" not in combined
    assert "초반 훅" not in combined
    assert "노후" in combined
