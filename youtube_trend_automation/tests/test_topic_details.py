from types import SimpleNamespace

from app.collectors.topic_details import TopicDetailCollector
from app.models import RankedTopic, TopicDetail


def _sample_topic() -> RankedTopic:
    return RankedTopic(
        normalized_topic="한국 증시 흐름",
        representative_title="한국 증시 흐름, 반도체 실적이 변수",
        score=1.0,
        sources=["google_trends"],
        mentions=["한국 증시 흐름, 반도체 실적이 변수"],
        keywords=["한국", "증시", "반도체", "실적"],
    )


def test_collect_prefers_google_results_before_naver(monkeypatch) -> None:
    collector = TopicDetailCollector(SimpleNamespace(allow_network=True))
    naver_called = {"value": False}

    def fake_google(_topic, _limit):
        return [
            TopicDetail(title=f"구글 {index}", summary=f"설명 {index}", source="google_news")
            for index in range(4)
        ]

    def fake_naver(_topic, _limit):
        naver_called["value"] = True
        return [TopicDetail(title="네이버", summary="설명", source="naver_search")]

    monkeypatch.setattr(collector, "_collect_google_news", fake_google)
    monkeypatch.setattr(collector, "_collect_naver_news", fake_naver)

    result = collector.collect(_sample_topic(), limit=4)

    assert len(result) == 4
    assert naver_called["value"] is False


def test_collect_uses_naver_only_when_google_is_empty(monkeypatch) -> None:
    collector = TopicDetailCollector(SimpleNamespace(allow_network=True))
    naver_called = {"value": False}

    def fake_google(_topic, _limit):
        return []

    def fake_naver(_topic, _limit):
        naver_called["value"] = True
        return [TopicDetail(title="네이버 1", summary="설명 2", source="naver_search")]

    monkeypatch.setattr(collector, "_collect_google_news", fake_google)
    monkeypatch.setattr(collector, "_collect_naver_news", fake_naver)

    result = collector.collect(_sample_topic(), limit=4)

    assert len(result) == 1
    assert naver_called["value"] is True


def test_detail_matches_topic_rejects_single_generic_overlap() -> None:
    topic = RankedTopic(
        normalized_topic="뉴욕 증시 급등 다우 평균 2.9",
        representative_title="뉴욕 증시 급등, 다우 평균 2.9%",
        score=1.0,
        sources=["google_news_search"],
        mentions=["뉴욕 증시 급등, 다우 평균 2.9%"],
        keywords=["뉴욕", "증시", "다우"],
    )

    assert (
        TopicDetailCollector._detail_matches_topic(
            topic,
            title="아슬아슬한 승리로 다음 라운드 진출",
            summary="베트남 U20 대표팀이 경기에서 아슬아슬한 승리를 거뒀다.",
        )
        is False
    )


def test_detail_matches_topic_accepts_specific_token_overlap() -> None:
    topic = RankedTopic(
        normalized_topic="민생지원금",
        representative_title="민생지원금",
        score=1.0,
        sources=["google_trends"],
        mentions=["민생지원금"],
        keywords=["민생지원금"],
    )

    assert (
        TopicDetailCollector._detail_matches_topic(
            topic,
            title="3차 민생지원금 지급 논의 이어져",
            summary="대상과 지급 방식은 아직 최종 확정 전이다.",
        )
        is True
    )
