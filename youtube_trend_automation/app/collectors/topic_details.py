from __future__ import annotations

from html import unescape
import itertools
import re
from urllib.parse import quote_plus
from xml.etree import ElementTree

from bs4 import BeautifulSoup
import requests

from app.config import AppConfig
from app.models import RankedTopic, TopicDetail
from app.utils.logging import get_logger
from app.utils.text import normalize_text

LOGGER = get_logger(__name__)


class TopicDetailCollector:
    """Collect supporting headlines and snippets for a selected topic."""

    _USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0 Safari/537.36"
    )
    _GENERIC_TOKENS = {
        "오늘",
        "이번",
        "다시",
        "급등",
        "급락",
        "최대",
        "관련",
        "논란",
        "속보",
        "브리핑",
        "핵심",
        "이슈",
        "아슬아슬한",
        "심상찮은",
        "주목",
        "전망",
        "예상",
        "상승",
        "하락",
        "지원",
        "지급",
        "신청",
        "검토",
        "논의",
        "정부",
        "한국",
    }

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def collect(self, topic: RankedTopic, limit: int = 6) -> list[TopicDetail]:
        if not self.config.allow_network:
            return self._fallback(topic, limit, "network disabled")

        google_items = self._collect_google_news(topic, max(limit * 2, 8))
        merged = self._merge_items(google_items, limit=limit)
        if merged:
            return merged

        naver_items = self._collect_naver_news(topic, limit)
        merged = self._merge_items([*google_items, *naver_items], limit=limit)
        if merged:
            return merged

        return self._fallback(topic, limit, "no detail snippets")

    def _collect_google_news(self, topic: RankedTopic, limit: int) -> list[TopicDetail]:
        headers = {"User-Agent": self._USER_AGENT}
        items: list[TopicDetail] = []
        seen_titles: set[str] = set()
        seen_queries: set[str] = set()

        for raw_query in self._detail_queries(topic):
            normalized_query = re.sub(r"\s+", " ", raw_query).strip()
            if not normalized_query or normalized_query in seen_queries:
                continue
            seen_queries.add(normalized_query)
            search_query = quote_plus(f"{normalized_query} when:7d")
            url = f"https://news.google.com/rss/search?q={search_query}&hl=ko&gl=KR&ceid=KR:ko"
            try:
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                root = ElementTree.fromstring(response.text)
            except Exception as exc:  # pragma: no cover - network path
                LOGGER.warning("Google News detail collection failed: %s", exc)
                continue

            for node in root.findall("./channel/item"):
                title = (node.findtext("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                source_node = node.find("source")
                source_name = (source_node.text or "").strip() if source_node is not None and source_node.text else "google_news"
                description_html = node.findtext("description") or ""
                description_text = BeautifulSoup(unescape(description_html), "html.parser").get_text(" ", strip=True)
                clean_headline = title.split(" - ")[0].strip()
                summary = self._clean_google_summary(
                    description_text=description_text,
                    clean_headline=clean_headline,
                    source_name=source_name,
                )
                if not self._detail_matches_topic(topic, title=title, summary=summary):
                    continue

                items.append(
                    TopicDetail(
                        title=title,
                        summary=summary,
                        source=source_name,
                        url=(node.findtext("link") or "").strip() or url,
                        published_at=(node.findtext("pubDate") or "").strip() or None,
                    )
                )
                if len(items) >= limit:
                    return items
        return items

    def _collect_naver_news(self, topic: RankedTopic, limit: int) -> list[TopicDetail]:
        query = quote_plus(topic.representative_title.split(" - ")[0].strip())
        url = f"https://search.naver.com/search.naver?where=news&query={query}&sort=1"
        headers = {"User-Agent": self._USER_AGENT}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
        except Exception as exc:  # pragma: no cover - network path
            LOGGER.warning("Naver detail collection failed: %s", exc)
            return []

        grouped: dict[str, list[str]] = {}
        for anchor in soup.select("div.group_news a[href]"):
            href = (anchor.get("href") or "").strip()
            text = anchor.get_text(" ", strip=True)
            if not href or not text or href == "#":
                continue
            if "keep.naver.com" in href:
                continue
            grouped.setdefault(href, []).append(text)

        items: list[TopicDetail] = []
        for href, texts in grouped.items():
            title = ""
            summary = ""
            for text in texts:
                cleaned = re.sub(r"\s+", " ", text).strip()
                if not cleaned:
                    continue
                if not title and 10 <= len(cleaned) <= 90:
                    title = cleaned
                    continue
                if len(cleaned) > max(40, len(title) + 12):
                    summary = cleaned
                    break
            if not title:
                continue
            summary = summary or title
            if not self._detail_matches_topic(topic, title=title, summary=summary):
                continue
            items.append(
                TopicDetail(
                    title=title,
                    summary=summary,
                    source="naver_search",
                    url=href,
                )
            )
            if len(items) >= limit:
                break

        return items

    def _detail_queries(self, topic: RankedTopic) -> list[str]:
        queries: list[str] = []

        clean_title = topic.representative_title.split(" - ")[0].strip()
        normalized_topic = re.sub(r"\s+", " ", topic.normalized_topic or "").strip()
        keyword_tokens = [token.strip() for token in topic.keywords if token.strip()]

        if clean_title:
            queries.append(clean_title)
        if normalized_topic and normalized_topic != clean_title:
            queries.append(normalized_topic)
        if keyword_tokens:
            queries.append(" ".join(keyword_tokens[:4]))

        for size in (2, 3):
            for combo in itertools.combinations(keyword_tokens[:5], size):
                queries.append(" ".join(combo))
                if len(queries) >= 6:
                    break
            if len(queries) >= 6:
                break

        unique_queries: list[str] = []
        seen: set[str] = set()
        for query in queries:
            normalized = re.sub(r"\s+", " ", query).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_queries.append(normalized)
        return unique_queries[:6]

    @staticmethod
    def _merge_items(items: list[TopicDetail], *, limit: int) -> list[TopicDetail]:
        unique_by_title: dict[str, TopicDetail] = {}
        for item in items:
            title_key = re.sub(r"\s+", " ", item.title or "").strip().lower()
            if not title_key:
                continue
            current = unique_by_title.get(title_key)
            if current is None or TopicDetailCollector._detail_score(item) > TopicDetailCollector._detail_score(current):
                unique_by_title[title_key] = item
        ranked = sorted(unique_by_title.values(), key=TopicDetailCollector._detail_score, reverse=True)
        return ranked[:limit]

    @staticmethod
    def _detail_score(item: TopicDetail) -> tuple[int, int, int]:
        title = re.sub(r"\s+", " ", item.title or "").strip()
        summary = re.sub(r"\s+", " ", item.summary or "").strip()
        descriptive = int(bool(summary and summary != title))
        source_bonus = int(item.source not in {"fallback", "naver_search"})
        official_bonus = int(TopicDetailCollector._is_official_like_detail(item))
        return (official_bonus, descriptive, source_bonus, len(summary))

    @staticmethod
    def _is_official_like_detail(item: TopicDetail) -> bool:
        haystack = normalize_text(
            " ".join(
                [
                    item.source or "",
                    item.title or "",
                    item.summary or "",
                    item.url or "",
                ]
            )
        )
        official_terms = (
            "govkr",
            "gokr",
            "koreakr",
            "정부24",
            "정책브리핑",
            "보건복지부",
            "고용노동부",
            "여성가족부",
            "행정안전부",
            "교육부",
            "국토교통부",
            "환경부",
            "산업통상자원부",
            "복지로",
            "국민연금공단",
            "건강보험공단",
            "주민센터",
            "행정복지센터",
            "시청",
            "구청",
            "군청",
            "도청",
            "지자체",
        )
        return any(normalize_text(term) in haystack for term in official_terms)

    @staticmethod
    def _clean_google_summary(*, description_text: str, clean_headline: str, source_name: str) -> str:
        summary = description_text or clean_headline
        summary = re.sub(r"\s+", " ", summary).strip()
        if source_name:
            summary = re.sub(rf"\b{re.escape(source_name)}\b", "", summary, flags=re.IGNORECASE).strip()
        summary = re.sub(r"\s*[-|]\s*$", "", summary).strip(" -|")
        if not summary:
            summary = clean_headline
        return summary or "관련 내용을 조금 더 확인해볼 필요가 있습니다."

    @staticmethod
    def _detail_matches_topic(topic: RankedTopic, *, title: str, summary: str) -> bool:
        haystack = normalize_text(f"{title} {summary}")
        topic_tokens = TopicDetailCollector._topic_tokens(topic)
        if not topic_tokens:
            return False

        overlaps = [token for token in topic_tokens if token in haystack]
        if len(topic_tokens) <= 2:
            return bool(overlaps)

        anchor_tokens = [token for token in topic_tokens if len(token) >= 3]
        anchor_overlaps = [token for token in anchor_tokens if token in haystack]
        return len(anchor_overlaps) >= 1 or len(overlaps) >= 2

    @staticmethod
    def _topic_tokens(topic: RankedTopic) -> list[str]:
        candidates = [topic.representative_title, topic.normalized_topic, *topic.keywords]
        tokens: list[str] = []
        seen: set[str] = set()
        for raw in candidates:
            normalized = normalize_text(raw or "")
            for token in normalized.split():
                token = token.strip()
                if len(token) < 2 or token in seen:
                    continue
                if token in TopicDetailCollector._GENERIC_TOKENS:
                    continue
                seen.add(token)
                tokens.append(token)
        return tokens[:16]

    @staticmethod
    def _fallback(topic: RankedTopic, limit: int, reason: str) -> list[TopicDetail]:
        base = [
            TopicDetail(
                title=f"{topic.representative_title} 현재 상황",
                summary="지금 가장 많이 함께 언급되는 핵심 흐름을 짧게 정리한 보강 정보입니다.",
                source="fallback",
            ),
            TopicDetail(
                title=f"{topic.representative_title} 핵심 변수",
                summary=f"주요 키워드는 {', '.join(topic.keywords[:3] or [topic.normalized_topic])} 중심으로 묶이고 있습니다.",
                source="fallback",
            ),
            TopicDetail(
                title=f"{topic.representative_title} 앞으로 볼 포인트",
                summary="추가 발표와 후속 기사 흐름을 함께 보면 핵심 맥락을 더 정확히 이해하기 좋습니다.",
                source="fallback",
            ),
        ]
        if reason:
            base.append(
                TopicDetail(
                    title=f"{topic.representative_title} 참고",
                    summary=f"상세 수집이 제한되어 보강 요약으로 대체했습니다. 사유: {reason}",
                    source="fallback",
                )
            )
        return base[:limit]
