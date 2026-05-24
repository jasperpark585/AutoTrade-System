import json
from pathlib import Path

from PIL import Image

from app.config import load_config
from app.models import ArtifactStatus, GeneratedContent, RankedTopic, StoryScene, TopicDetail
from app.pipeline import Pipeline
from app.render.background_builder import BackgroundBuilder


def test_dry_run_creates_metadata(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    config.allow_network = False
    pipeline = Pipeline(config)

    result = pipeline.dry_run()

    assert result.status == "success"
    assert result.metadata_path is not None
    assert Path(result.metadata_path).exists()


def test_run_once_creates_background_thumbnail_and_subtitles_without_network(tmp_path: Path, monkeypatch) -> None:
    empty_local = tmp_path / "empty_localappdata"
    empty_local.mkdir()
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(empty_local))
    monkeypatch.setenv("PATH", "")
    config = load_config(tmp_path)
    config.allow_network = False
    pipeline = Pipeline(config)

    result = pipeline.run_once()

    assert result.status == "success"
    assert result.background is not None
    assert result.thumbnail is not None
    assert result.thumbnail.status == "created"
    assert result.thumbnail.path is not None
    assert Path(result.thumbnail.path).exists()
    assert result.audio is not None
    assert result.audio.status == "mocked"
    assert result.subtitles is not None
    assert result.subtitles.status == "created"
    assert result.video is not None
    assert result.video.status == "skipped"


def test_story_channel_run_once_creates_scene_assets_without_network(tmp_path: Path, monkeypatch) -> None:
    empty_local = tmp_path / "empty_localappdata"
    empty_local.mkdir()
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(empty_local))
    monkeypatch.setenv("PATH", "")
    config = load_config(tmp_path, channel_id="story_default")
    music_track = config.music_dir / "story-bed.mp3"
    music_track.write_bytes(b"placeholder music")
    assert config.active_channel is not None
    config.active_channel.background_music_path = "story-bed.mp3"
    config.allow_network = False
    pipeline = Pipeline(config)

    result = pipeline.run_once()

    assert result.status == "success"
    assert result.background is not None
    assert result.background.status == "created"
    assert result.background.extra.get("scene_images")
    assert result.background.extra.get("hook_images")
    assert len(result.background.extra["scene_images"][0]["paths"]) == 1
    assert result.audio is not None
    assert result.audio.path is not None
    assert result.audio.status == "mocked"
    assert result.subtitles is not None
    assert result.subtitles.status == "created"
    assert result.video is not None
    assert result.video.status == "skipped"
    assert result.video.path is not None
    render_plan = json.loads(Path(result.video.path).read_text(encoding="utf-8"))
    assert render_plan["mode"] == "longform_story"
    assert render_plan["burn_in_subtitles"] is True
    assert render_plan["background_music_path"].endswith("story-bed.mp3")
    assert render_plan["segments"][0]["motion_template"] == "dramatic_push"
    assert render_plan["segments"][0]["duration_seconds"] <= 40
    assert all(not segment["title_text"] for segment in render_plan["segments"])


def test_sync_audio_timings_updates_short_duration_from_real_audio(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path)
    pipeline = Pipeline(config)
    content = GeneratedContent(
        topic=RankedTopic(
            normalized_topic="복지 정보",
            representative_title="복지 정보",
            score=1.0,
            sources=["fallback"],
            mentions=["복지 정보"],
            keywords=["복지", "지원"],
        ),
        video_title="테스트 제목",
        script="테스트 스크립트",
        description="테스트 설명",
        tags=["#복지"],
        segments=["대상 설명", "혜택 설명"],
        estimated_duration_seconds=45,
        content_format="short",
    )
    audio = ArtifactStatus(status="created", provider="edge-tts", path=str(tmp_path / "audio.mp3"))

    monkeypatch.setattr(pipeline, "_probe_media_duration", lambda path: 28.39)

    pipeline._sync_audio_timings(content, audio)

    assert content.estimated_duration_seconds == 28


def test_sync_story_audio_timings_uses_real_segment_durations(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    pipeline = Pipeline(config)
    content = GeneratedContent(
        topic=RankedTopic(
            normalized_topic="가족 사연",
            representative_title="가족 사연",
            score=1.0,
            sources=["stories"],
            mentions=["가족 사연"],
            keywords=["가족", "사연"],
        ),
        video_title="가족 사연",
        script="",
        description="",
        tags=[],
        segments=[],
        content_format="longform_story",
        hook_script="훅 대본",
        hook_duration_seconds=40,
        scenes=[
            StoryScene(index=1, title="장면1", summary="요약1", narration="장면1 대본", image_prompt="prompt", duration_seconds=300),
            StoryScene(index=2, title="장면2", summary="요약2", narration="장면2 대본", image_prompt="prompt", duration_seconds=300),
        ],
    )
    audio = ArtifactStatus(
        status="created",
        provider="edge-tts",
        path=str(tmp_path / "audio"),
        extra={
            "segments": [
                {"label": "hook", "path": str(tmp_path / "hook.mp3")},
                {"label": "scene_01", "path": str(tmp_path / "scene_01.mp3")},
                {"label": "scene_02", "path": str(tmp_path / "scene_02.mp3")},
            ]
        },
    )

    durations = {
        str(tmp_path / "hook.mp3"): 31.2,
        str(tmp_path / "scene_01.mp3"): 502.4,
        str(tmp_path / "scene_02.mp3"): 487.8,
    }
    monkeypatch.setattr(pipeline, "_probe_media_duration", lambda path: durations.get(path))

    pipeline._sync_audio_timings(content, audio)

    assert content.hook_duration_seconds == 31.2
    assert content.scenes[0].duration_seconds == 502.4
    assert content.scenes[1].duration_seconds == 487.8
    assert audio.extra["segments"][0]["duration_seconds"] == 31.2


def test_run_once_uses_best_available_fallback_when_fresh_topics_are_exhausted(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    config.allow_network = False
    pipeline = Pipeline(config)
    topic = RankedTopic(
        normalized_topic="inflation pressure",
        representative_title="Inflation pressure",
        score=3.0,
        sources=["news"],
        mentions=["Inflation pressure"],
        keywords=["inflation", "pressure"],
    )

    monkeypatch.setattr(pipeline, "_collect_and_rank", lambda: [topic])

    state = {"count": 0}

    def fake_select(ranked_topics, *, allow_duplicate):
        state["count"] += 1
        if not allow_duplicate:
            return None, []
        return topic, []

    monkeypatch.setattr(pipeline, "_select_topic_with_details", fake_select)

    result = pipeline.run_once(skip_render=True, skip_upload=True)

    assert result.status == "success"
    assert result.selected_topic == "Inflation pressure"
    assert result.warnings
    assert "best available fallback topic" in result.warnings[0]
    assert state["count"] == 2


def test_run_once_skips_disabled_channel(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    assert config.active_channel is not None
    config.active_channel.enabled = False
    pipeline = Pipeline(config)

    result = pipeline.run_once()

    assert result.status == "skipped"
    assert result.warnings
    assert "channel disabled" in result.warnings[0]


def test_upload_only_skips_when_auto_upload_disabled(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    assert config.active_channel is not None
    config.active_channel.auto_upload = False
    pipeline = Pipeline(config)

    result = pipeline.upload_only()

    assert result.status == "skipped"
    assert result.warnings
    assert "upload disabled" in result.warnings[0]


def test_economy_topic_filter_rejects_sports_like_news(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    sports_topic = RankedTopic(
        normalized_topic="한국 여자 골프 신기록",
        representative_title="한국 여자 골프 신기록",
        score=3.2,
        sources=["google_news_search"],
        mentions=["한국 여자 골프 신기록"],
        keywords=["골프", "신기록", "오거스타"],
    )
    sports_details = [
        TopicDetail(title="골프 신기록", summary="여자골프 선수가 오거스타 대회에서 새 기록을 세웠습니다.", source="google"),
        TopicDetail(title="반응", summary="투어 관계자와 팬들의 관심이 커지고 있습니다.", source="google"),
    ]

    assert pipeline._is_actionable_economy_topic(sports_topic) is False
    assert pipeline._is_actionable_economy_candidate(sports_topic, sports_details) is False


def test_economy_topic_filter_rejects_editorial_opinion_like_news(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    editorial_topic = RankedTopic(
        normalized_topic="기업 비업무용 부동산 규제 사설",
        representative_title="[사설] 기업 비업무용 부동산 규제 강화, 국민경제를 위해 필요하다",
        score=3.6,
        sources=["google_news_search"],
        mentions=["기업 비업무용 부동산 규제 사설"],
        keywords=["사설", "부동산", "규제"],
    )
    editorial_details = [
        TopicDetail(title="사설", summary="기업 비업무용 부동산 규제 강화가 필요하다는 사설입니다.", source="google"),
        TopicDetail(title="논평", summary="보유 부담을 더 강화해야 한다는 논평 성격의 기사입니다.", source="google"),
    ]

    assert pipeline._is_actionable_economy_topic(editorial_topic) is False
    assert pipeline._is_actionable_economy_candidate(editorial_topic, editorial_details) is False


def test_economy_topic_filter_rejects_mixed_market_roundup_titles(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    roundup_topic = RankedTopic(
        normalized_topic="뉴욕증시 비트코인 엔비디아 FOMC 금리인하",
        representative_title="뉴욕증시 비트코인 엔비디아 충격 연준 FOMC 금리인하 전면 수정",
        score=3.7,
        sources=["google_news_search"],
        mentions=["뉴욕증시 비트코인 엔비디아 충격 연준 FOMC 금리인하 전면 수정"],
        keywords=["뉴욕증시", "비트코인", "엔비디아", "FOMC", "연준"],
    )
    roundup_details = [
        TopicDetail(title="시황", summary="뉴욕증시와 비트코인, 엔비디아, 연준 FOMC 전망이 한꺼번에 언급된 시황 요약입니다.", source="google"),
        TopicDetail(title="흐름", summary="나스닥과 다우, 국제유가까지 한 문장에 묶인 종합 시황 기사입니다.", source="google"),
    ]

    assert pipeline._is_actionable_economy_topic(roundup_topic) is False
    assert pipeline._is_actionable_economy_candidate(roundup_topic, roundup_details) is False


def test_economy_topic_filter_rejects_overly_generic_labels(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    generic_topic = RankedTopic(
        normalized_topic="주가",
        representative_title="주가",
        score=3.8,
        sources=["google_trends"],
        mentions=["주가"],
        keywords=["주가", "증시", "코스피"],
    )
    generic_details = [
        TopicDetail(title="삼성전자 목표주가 상향", summary="삼성전자 목표주가가 다시 올라섰습니다.", source="google"),
        TopicDetail(title="삼천당제약 주가 반토막", summary="제약주 변동성이 다시 커졌습니다.", source="google"),
        TopicDetail(title="낸드플래시 호황 기대", summary="반도체주 실적 기대가 주가를 흔들고 있습니다.", source="google"),
    ]

    assert pipeline._is_actionable_economy_topic(generic_topic) is False
    assert pipeline._is_actionable_economy_candidate(generic_topic, generic_details) is False


def test_economy_topic_filter_rejects_judicial_scandal_topics(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    judicial_topic = RankedTopic(
        normalized_topic="주가조작 세력 유착 의혹",
        representative_title="'주가조작 세력 유착' 의혹 檢, 경찰청 본청 압수수색",
        score=3.9,
        sources=["google_news_search"],
        mentions=["주가조작 세력 유착 의혹"],
        keywords=["주가조작", "검찰", "압수수색"],
    )
    judicial_details = [
        TopicDetail(title="특검", summary="특검이 주가조작 의혹 관련 자료를 확보했습니다.", source="google"),
        TopicDetail(title="재판", summary="관련 공범 재판이 이어지고 있습니다.", source="google"),
    ]

    assert pipeline._is_actionable_economy_topic(judicial_topic) is False
    assert pipeline._is_actionable_economy_candidate(judicial_topic, judicial_details) is False


def test_economy_topic_filter_rejects_desk_jargon_topics(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    jargon_topic = RankedTopic(
        normalized_topic="irs 금리 관망세",
        representative_title="IRS 금리, 종전 기대감 약화에 소폭 상승·금통위 관망세",
        score=3.7,
        sources=["google_news_search"],
        mentions=["IRS 금리"],
        keywords=["irs", "금리", "관망세"],
    )
    jargon_details = [
        TopicDetail(title="채권시장", summary="서울 채권시장이 관망세를 보였습니다.", source="google"),
        TopicDetail(title="스와프", summary="IRS와 커브 변동성이 이어졌습니다.", source="google"),
    ]

    assert pipeline._is_actionable_economy_topic(jargon_topic) is False
    assert pipeline._is_actionable_economy_candidate(jargon_topic, jargon_details) is False


def test_economy_topic_filter_rejects_low_intent_culture_topics(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    culture_topic = RankedTopic(
        normalized_topic="북펀드 반도체 공학자 대서사",
        representative_title="과학사 출판 '북펀드' 참가 어떠세요? 반도체·제조업 강국 이끈 30인 공학자 대서사",
        score=3.4,
        sources=["google_news_search"],
        mentions=["북펀드"],
        keywords=["출판", "반도체", "공학자"],
    )
    culture_details = [
        TopicDetail(title="출판", summary="과학사 출판 북펀드 참가를 안내하는 내용입니다.", source="google"),
        TopicDetail(title="행사", summary="공학자 대서사를 소개하는 문화성 기사입니다.", source="google"),
    ]

    assert pipeline._is_actionable_economy_topic(culture_topic) is False
    assert pipeline._is_actionable_economy_candidate(culture_topic, culture_details) is False


def test_story_scene_images_reuse_last_successful_image_when_generation_fails(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    builder = BackgroundBuilder(config)
    content = GeneratedContent(
        topic=RankedTopic(
            normalized_topic="late life romance",
            representative_title="황혼에 다시 사랑을 시작한 어머니의 사연",
            score=2.5,
            sources=["stories"],
            mentions=["late life romance"],
            keywords=["황혼", "사랑", "어머니"],
        ),
        video_title="황혼에 다시 사랑을 시작한 어머니",
        script="",
        description="",
        tags=[],
        segments=[],
        content_format="longform_story",
        hook_title="식탁에서 꺼낸 한마디",
        hook_script="그날 저녁, 어머니는 더는 숨기지 않겠다고 말했다.",
        hook_image_prompt="photoreal Korean senior woman at a family dinner table, no text",
        scenes=[
            StoryScene(
                index=1,
                title="복도에서 마주친 밤",
                summary="어머니가 늦은 밤 병원 복도에서 오래된 인연과 마주친다.",
                narration="narration",
                image_prompt="photoreal Korean senior woman in a hospital corridor, no text",
            )
        ],
    )

    calls = {"count": 0}

    def fake_generate_image(*, prompt: str, output_path: Path) -> bool:
        calls["count"] += 1
        if calls["count"] == 1:
            Image.new("RGB", (1280, 720), "#654321").save(output_path)
            return True
        return False

    builder.ai.generate_image = fake_generate_image  # type: ignore[method-assign]

    result = builder.build(content, "story_reuse")

    hook_images = result.extra["hook_images"]
    scene_images = result.extra["scene_images"][0]["paths"]

    assert Path(hook_images[0]).exists()
    assert Path(hook_images[1]).exists()
    assert Path(hook_images[0]).read_bytes() != Path(hook_images[1]).read_bytes()
    assert len(scene_images) == 1
    assert Path(scene_images[0]).exists()
    assert Path(scene_images[0]).read_bytes() != Path(hook_images[0]).read_bytes()
