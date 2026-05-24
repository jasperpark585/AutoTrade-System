from __future__ import annotations

from typing import Iterable
from xml.etree import ElementTree

import requests

from app.config import AppConfig
from app.models import TopicCandidate
from app.utils.logging import get_logger

LOGGER = get_logger(__name__)


class GoogleTrendsCollector:
    """Collect Google Trends terms for South Korea with graceful fallback."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def collect(self, limit: int | None = None) -> list[TopicCandidate]:
        limit = limit or self.config.collection.google_trends_limit
        if not self.config.allow_network:
            return self._fallback(limit, "network disabled")

        rss_topics = self._collect_from_rss(limit)
        if rss_topics:
            return rss_topics

        try:
            from pytrends.request import TrendReq
        except ImportError as exc:  # pragma: no cover - dependency guarded by install
            LOGGER.warning("pytrends import failed: %s", exc)
            return self._fallback(limit, "pytrends unavailable")

        try:
            client = TrendReq(hl="ko-KR", tz=540)
            frame = client.trending_searches(pn="south_korea")
            values = [str(item).strip() for item in frame.iloc[:, 0].tolist() if str(item).strip()]
        except Exception as exc:  # pragma: no cover - network path
            LOGGER.warning("Google Trends collection failed: %s", exc)
            return self._fallback(limit, str(exc))

        topics = [
            TopicCandidate(
                title=value,
                source="google_trends",
                url="https://trends.google.com/trending",
                weight=1.0,
            )
            for value in values[:limit]
        ]
        return topics or self._fallback(limit, "no google trends results")

    def _fallback(self, limit: int, reason: str) -> list[TopicCandidate]:
        LOGGER.info("Using Google Trends fallback topics: %s", reason)
        return self._build_fallback(self.config.collection.fallback_topics[:limit])

    def _collect_from_rss(self, limit: int) -> list[TopicCandidate]:
        try:
            response = requests.get(
                f"https://trends.google.com/trending/rss?geo={self.config.collection.google_trends_geo}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)
        except Exception as exc:  # pragma: no cover - network path
            LOGGER.warning("Google Trends RSS collection failed: %s", exc)
            return []

        topics: list[TopicCandidate] = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            topics.append(
                TopicCandidate(
                    title=title,
                    source="google_trends",
                    url=(item.findtext("link") or "").strip() or "https://trends.google.com/trending",
                    published_at=(item.findtext("pubDate") or "").strip() or None,
                    weight=1.0,
                )
            )
            if len(topics) >= limit:
                break
        return topics

    @staticmethod
    def _build_fallback(terms: Iterable[str]) -> list[TopicCandidate]:
        return [
            TopicCandidate(
                title=term,
                source="fallback",
                weight=0.8,
                metadata={"reason": "fallback_collector"},
            )
            for term in terms
        ]
