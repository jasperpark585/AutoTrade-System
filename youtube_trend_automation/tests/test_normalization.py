from app.models import TopicCandidate
from app.scoring.normalization import group_candidates, normalize_topic


def test_normalize_topic_handles_spacing_and_case() -> None:
    assert normalize_topic("  AI   에이전트!! ") == "ai 에이전트"


def test_group_candidates_groups_similar_titles() -> None:
    candidates = [
        TopicCandidate(title="AI 에이전트", source="google_trends"),
        TopicCandidate(title="ai   에이전트", source="naver_news"),
    ]
    grouped = group_candidates(candidates)
    assert list(grouped.keys()) == ["ai 에이전트"]
    assert len(grouped["ai 에이전트"]) == 2
