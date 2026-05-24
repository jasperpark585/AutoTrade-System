from datetime import datetime, timedelta
import os
from pathlib import Path

from app.config import load_config
from app.models import RankedTopic
from app.storage.repository import StorageRepository


def test_repository_marks_duplicate_per_channel(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    repo = StorageRepository(config)
    topic = RankedTopic(
        normalized_topic="ai 에이전트",
        representative_title="AI 에이전트",
        score=3.2,
        sources=["fallback"],
        mentions=["AI 에이전트"],
        keywords=["ai", "에이전트"],
    )

    assert repo.is_duplicate(topic) is False
    repo.mark_processed(topic, "테스트 제목")
    assert repo.is_duplicate(topic) is True


def test_repository_does_not_permanently_block_only_similar_topics(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    repo = StorageRepository(config)
    original = RankedTopic(
        normalized_topic="iran oil price shock",
        representative_title="Iran oil price shock",
        score=4.1,
        sources=["news"],
        mentions=["Iran oil price shock"],
        keywords=["iran", "oil", "price", "shock"],
    )
    similar = RankedTopic(
        normalized_topic="iran oil price surge",
        representative_title="Iran oil price surge",
        score=4.0,
        sources=["news"],
        mentions=["Iran oil price surge"],
        keywords=["iran", "oil", "price", "surge"],
    )

    repo.mark_processed(original, "recent title")

    assert repo.is_duplicate(similar) is False


def test_repository_blocks_recently_redundant_topics(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    repo = StorageRepository(config)
    recent = RankedTopic(
        normalized_topic="iran oil price shock",
        representative_title="Iran oil price shock",
        score=4.1,
        sources=["news"],
        mentions=["Iran oil price shock"],
        keywords=["iran", "oil", "price", "shock"],
    )
    similar = RankedTopic(
        normalized_topic="iran oil price surge",
        representative_title="Iran oil price surge",
        score=4.0,
        sources=["news"],
        mentions=["Iran oil price surge"],
        keywords=["iran", "oil", "price", "surge"],
    )
    different = RankedTopic(
        normalized_topic="housing loan policy easing",
        representative_title="Housing loan policy easing",
        score=3.8,
        sources=["news"],
        mentions=["Housing loan policy easing"],
        keywords=["housing", "loan", "policy", "easing"],
    )

    repo.mark_processed(recent, "recent title")

    assert repo.is_recently_redundant(similar, limit=5) is True
    assert repo.is_recently_redundant(different, limit=5) is False


def test_repository_prunes_old_outputs_and_zero_byte_files(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    repo = StorageRepository(config)

    old_video = config.output_videos_dir / "old.mp4"
    old_video.write_bytes(b"legacy")
    old_zero = config.output_metadata_dir / "old-zero.json"
    old_zero.write_bytes(b"")
    recent_video = config.output_videos_dir / "recent.mp4"
    recent_video.write_bytes(b"recent")
    for index in range(16):
        path = config.output_videos_dir / f"recent-{index:02}.mp4"
        path.write_bytes(b"x")
        ts = (datetime.now() - timedelta(minutes=index)).timestamp()
        os.utime(path, (ts, ts))

    old_time = (datetime.now() - timedelta(hours=30)).timestamp()
    recent_time = (datetime.now() - timedelta(hours=1)).timestamp()
    os.utime(old_video, (old_time, old_time))
    os.utime(old_zero, (old_time, old_time))
    os.utime(recent_video, (recent_time, recent_time))

    repo.prune_outputs()

    assert old_video.exists() is False
    assert old_zero.exists() is False
    assert recent_video.exists() is True


def test_repository_enforces_size_limit_while_protecting_newest_entries(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    repo = StorageRepository(config)

    created: list[Path] = []
    for index in range(4):
        path = config.output_videos_dir / f"clip-{index}.mp4"
        path.write_bytes(b"1234567890")
        ts = (datetime.now() - timedelta(minutes=10 - index)).timestamp()
        os.utime(path, (ts, ts))
        created.append(path)

    repo._enforce_size_limit(config.output_videos_dir, max_bytes=15, keep_recent=1)

    assert created[-1].exists() is True
    assert created[0].exists() is False
    assert created[1].exists() is False
    assert created[2].exists() is False


def test_repository_recent_processed_returns_thumbnail_history(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    repo = StorageRepository(config)
    first = RankedTopic(
        normalized_topic="inflation one",
        representative_title="Inflation one",
        score=3.2,
        sources=["news"],
        mentions=["Inflation one"],
        keywords=["inflation"],
    )
    second = RankedTopic(
        normalized_topic="inflation two",
        representative_title="Inflation two",
        score=3.1,
        sources=["news"],
        mentions=["Inflation two"],
        keywords=["inflation"],
    )

    repo.mark_processed(first, "title 1", thumbnail_text="물가 왜 다시 오르나")
    repo.mark_processed(second, "title 2", thumbnail_text="이번 달 물가 변수")

    recent = repo.recent_processed(limit=2)

    assert [item["thumbnail_text"] for item in recent] == ["이번 달 물가 변수", "물가 왜 다시 오르나"]
