from __future__ import annotations

from urllib.parse import quote_plus
from xml.etree import ElementTree

from bs4 import BeautifulSoup
import requests

from app.config import AppConfig
from app.models import TopicCandidate
from app.utils.logging import get_logger

LOGGER = get_logger(__name__)


class NaverNewsCollector:
    """Collect topic candidates, preferring Google News search before Naver sections."""

    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0 Safari/537.36"
    )

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def collect(self, seed_keywords: list[str], limit: int | None = None) -> list[TopicCandidate]:
        limit = limit or self.config.collection.google_trends_limit
        if not self.config.allow_network:
            return self._fallback(seed_keywords, limit, "network disabled")

        headers = {"User-Agent": self._USER_AGENT}
        collected: list[TopicCandidate] = []
        seen_titles: set[str] = set()
        primary_keywords = seed_keywords or self.config.collection.fallback_topics

        collected.extend(
            self._collect_search_results(
                seed_keywords=primary_keywords,
                headers=headers,
                limit=max(limit * 2, 10),
                seen_titles=seen_titles,
            )
        )
        if len(collected) >= self._minimum_primary_results(limit):
            return collected[:limit]

        try:
            for section in self.config.collection.naver_sections:
                url = f"https://news.naver.com/section/{section}"
                response = requests.get(url, headers=headers, timeout=8)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                for node in soup.select("a.sa_text_title, strong.sa_text_strong, a.cnf_news_title"):
                    title = node.get_text(" ", strip=True)
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    collected.append(
                        TopicCandidate(
                            title=title,
                            source="naver_news",
                            url=url,
                            weight=1.0,
                        )
                    )
                    if len(collected) >= limit:
                        return collected
        except Exception as exc:  # pragma: no cover - network path
            LOGGER.warning("Naver section collection failed: %s", exc)

        if collected:
            return collected[:limit]
        return self._fallback(seed_keywords, limit, "no naver headlines")

    @staticmethod
    def _minimum_primary_results(limit: int) -> int:
        return min(limit, 5)

    def _collect_search_results(
        self,
        *,
        seed_keywords: list[str],
        headers: dict[str, str],
        limit: int,
        seen_titles: set[str],
    ) -> list[TopicCandidate]:
        results: list[TopicCandidate] = []
        query_limit = min(len(seed_keywords), max(5, limit))
        per_query_cap = 2 if limit >= 8 else 1
        for keyword in seed_keywords[:query_limit]:
            query = quote_plus(f"{keyword} when:1d")
            url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
            try:
                response = requests.get(url, headers=headers, timeout=8)
                response.raise_for_status()
                root = ElementTree.fromstring(response.text)
            except Exception:
                continue

            query_results = 0
            for node in root.findall("./channel/item"):
                title = (node.findtext("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                results.append(
                    TopicCandidate(
                        title=title,
                        source="google_news_search",
                        url=(node.findtext("link") or "").strip() or url,
                        published_at=(node.findtext("pubDate") or "").strip() or None,
                        weight=1.2,
                        metadata={"query": keyword},
                    )
                )
                query_results += 1
                if query_results >= per_query_cap:
                    break
                if len(results) >= limit:
                    return results
        return results

    def _fallback(self, seed_keywords: list[str], limit: int, reason: str) -> list[TopicCandidate]:
        LOGGER.info("Using Naver fallback topics: %s", reason)
        values = seed_keywords or self.config.collection.fallback_topics
        topics: list[TopicCandidate] = []
        for keyword in values[:limit]:
            query = quote_plus(keyword)
            topics.append(
                TopicCandidate(
                    title=f"{keyword} 관련 최신 이슈 브리핑",
                    source="fallback",
                    url=f"https://search.naver.com/search.naver?where=news&query={query}",
                    weight=0.7,
                    metadata={"reason": "fallback_collector"},
                )
            )
        return topics
