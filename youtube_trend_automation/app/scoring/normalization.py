from __future__ import annotations

from collections import defaultdict

from app.models import TopicCandidate
from app.utils.text import normalize_text


def normalize_topic(text: str) -> str:
    """Normalize a topic string."""

    return normalize_text(text)


def group_candidates(candidates: list[TopicCandidate]) -> dict[str, list[TopicCandidate]]:
    """Group raw candidates by normalized topic text."""

    grouped: dict[str, list[TopicCandidate]] = defaultdict(list)
    for candidate in candidates:
        normalized = normalize_topic(candidate.title)
        if normalized:
            grouped[normalized].append(candidate)
    return dict(grouped)

