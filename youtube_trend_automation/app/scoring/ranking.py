from __future__ import annotations

from app.config import AppConfig
from app.models import RankedTopic, TopicCandidate
from app.scoring.normalization import group_candidates
from app.utils.text import normalize_text, unique_preserve_order


def _extract_keywords(values: list[str], max_keywords: int = 5) -> list[str]:
    keywords: list[str] = []
    for value in values:
        for token in normalize_text(value).split():
            if len(token) <= 1:
                continue
            keywords.append(token)
    return unique_preserve_order(keywords)[:max_keywords]


def rank_topics(candidates: list[TopicCandidate], config: AppConfig, *, top_k: int | None = None) -> list[RankedTopic]:
    """Aggregate and score normalized topics."""

    grouped = group_candidates(candidates)
    ranked: list[RankedTopic] = []
    for normalized, items in grouped.items():
        mentions = [item.title for item in items]
        sources = unique_preserve_order([item.source for item in items])
        source_score = sum(config.scoring.source_weights.get(item.source, 1.0) * item.weight for item in items)
        score = round(source_score + len(sources) * 0.35 + len(mentions) * 0.1, 4)
        ranked.append(
            RankedTopic(
                normalized_topic=normalized,
                representative_title=mentions[0],
                score=score,
                sources=sources,
                mentions=mentions,
                keywords=_extract_keywords(mentions),
                metadata={
                    "mention_count": len(mentions),
                    "source_count": len(sources),
                },
            )
        )

    ranked.sort(key=lambda item: item.score, reverse=True)
    limit = top_k if top_k is not None else config.scoring.top_k
    return ranked[:limit]
