from __future__ import annotations

from datetime import datetime
import secrets
import shutil
import subprocess
from typing import Any

from app.collectors.google_trends import GoogleTrendsCollector
from app.collectors.life_content import LifeContentCollector
from app.collectors.naver_news import NaverNewsCollector
from app.collectors.topic_details import TopicDetailCollector
from app.config import AppConfig
from app.generation.content_generator import ContentGenerator
from app.models import ArtifactStatus, GeneratedContent, PipelineResult, RankedTopic, StoryScene, TopicCandidate, TopicDetail
from app.render.background_builder import BackgroundBuilder
from app.render.thumbnail_builder import ThumbnailBuilder
from app.render.video_builder import VideoBuilder
from app.scoring.ranking import rank_topics
from app.storage.repository import StorageRepository
from app.studio.presets import preset_by_key
from app.subtitles.srt_builder import SubtitleBuilder
from app.tts.edge_provider import EdgeTTSProvider
from app.utils.logging import get_logger
from app.utils.text import normalize_text, slugify, unique_preserve_order
from app.youtube.uploader import YouTubeUploader

LOGGER = get_logger(__name__)


class Pipeline:
    """Orchestrate topic collection, generation, persistence, and deployment artifacts."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.google_collector = GoogleTrendsCollector(config)
        self.naver_collector = NaverNewsCollector(config)
        self.detail_collector = TopicDetailCollector(config)
        self.life_collector = LifeContentCollector(config)
        self.generator = ContentGenerator(config)
        self.repository = StorageRepository(config)
        self.tts = EdgeTTSProvider(config)
        self.subtitle_builder = SubtitleBuilder(config)
        self.background_builder = BackgroundBuilder(config)
        self.thumbnail_builder = ThumbnailBuilder(config)
        self.video_builder = VideoBuilder(config)
        self.youtube_uploader = YouTubeUploader(config)

    def dry_run(self) -> PipelineResult:
        """Run the pipeline without external side effects."""
        guard = self._guard_channel_operation(mode="dry-run", needs_generate=True, needs_render=False, needs_upload=False)
        if guard is not None:
            return guard

        ranked_topics = self._collect_and_rank()
        topic, details = self._select_topic_with_details(ranked_topics, allow_duplicate=True)
        if topic is None:
            return PipelineResult(mode="dry-run", status="failed", warnings=["No topics available"])

        content = self.generator.generate(topic, details)
        preview = PipelineResult(
            mode="dry-run",
            status="success",
            selected_topic=topic.representative_title,
            details={
                "preview_tags": content.tags,
                "title": content.video_title,
                "description": content.description,
                "thumbnail_text": content.thumbnail_text,
                "channel": self.config.active_channel.display_name if self.config.active_channel else "",
                "preset_key": content.preset_key,
                "contains_synthetic_media": content.contains_synthetic_media,
                "detail_titles": [item.title for item in details],
            },
        )
        metadata_path = self.repository.save_run(
            run_id=self._build_run_id(topic),
            topic=topic,
            content=content,
            metadata={"mode": "dry-run"},
            result=preview,
            details_collected=details,
        )
        preview.metadata_path = str(metadata_path)
        preview.warnings.append("Dry-run does not synthesize audio, render video, or upload.")
        preview.details["ranked_topics"] = [item.representative_title for item in ranked_topics]
        return preview

    def run_once(
        self,
        *,
        skip_render: bool = False,
        skip_upload: bool = False,
        force: bool = False,
    ) -> PipelineResult:
        """Execute a single end-to-end run with safe fallbacks."""
        guard = self._guard_channel_operation(
            mode="run-once",
            needs_generate=True,
            needs_render=not skip_render,
            needs_upload=not skip_upload,
        )
        if guard is not None:
            return guard
        self.repository.prune_outputs()
        try:
            ranked_topics = self._collect_and_rank()
            used_duplicate_fallback = False
            topic, details = self._select_topic_with_details(ranked_topics, allow_duplicate=force)
            if topic is None and ranked_topics and not force:
                topic, details = self._select_topic_with_details(ranked_topics, allow_duplicate=True)
                used_duplicate_fallback = topic is not None
            if topic is None:
                return PipelineResult(
                    mode="run-once",
                    status="failed",
                    warnings=["No non-duplicate topics available"],
                    details={"ranked_topics": [item.representative_title for item in ranked_topics]},
                )

            content = self.generator.generate(topic, details)
            run_id = self._build_run_id(topic)

            background = self.background_builder.build(content, run_id)
            thumbnail = self.thumbnail_builder.build(content, run_id, background)
            audio = self.tts.synthesize(content, run_id)
            self._sync_audio_timings(content, audio)
            subtitles = self.subtitle_builder.build(content, run_id)
            video = (
                ArtifactStatus(status="skipped", provider="ffmpeg", message="Render skipped by CLI.")
                if skip_render
                else self.video_builder.build(content, run_id, audio, background)
            )
            upload = (
                ArtifactStatus(status="skipped", provider="youtube", message="Upload skipped by CLI.")
                if skip_upload
                else self.youtube_uploader.upload(content, video, thumbnail)
            )

            warnings = [
                item.message
                for item in (background, thumbnail, audio, subtitles, video, upload)
                if item.message and item.status not in {"created"}
            ]
            if used_duplicate_fallback:
                warnings.insert(0, "Fresh topic candidates were exhausted, so the best available fallback topic was used.")
            result = PipelineResult(
                mode="run-once",
                status="success",
                selected_topic=topic.representative_title,
                background=background,
                audio=audio,
                subtitles=subtitles,
                video=video,
                thumbnail=thumbnail,
                upload=upload,
                warnings=warnings,
                details={
                    "score": topic.score,
                    "channel": self.config.active_channel.display_name if self.config.active_channel else "",
                    "preset_key": content.preset_key,
                    "content_format": content.content_format,
                    "contains_synthetic_media": content.contains_synthetic_media,
                },
            )
            metadata = {
                "background": background.to_dict(),
                "thumbnail": thumbnail.to_dict(),
                "audio": audio.to_dict(),
                "subtitles": subtitles.to_dict(),
                "video": video.to_dict(),
                "upload": upload.to_dict(),
            }
            metadata_path = self.repository.save_run(run_id, topic, content, metadata, result, details_collected=details)
            result.metadata_path = str(metadata_path)
            self.repository.mark_processed(topic, content.video_title, thumbnail_text=content.thumbnail_text)
            return result
        finally:
            self.repository.prune_outputs()

    def render_only(self) -> PipelineResult:
        """Rebuild subtitles and video from the latest metadata file."""
        guard = self._guard_channel_operation(mode="render-only", needs_generate=False, needs_render=True, needs_upload=False)
        if guard is not None:
            return guard

        latest = self.repository.latest_run(self._channel_id())
        if latest is None:
            return PipelineResult(mode="render-only", status="failed", warnings=["No metadata to replay"])

        content = self._content_from_latest(latest)
        run_id = latest["run_id"]
        artifacts = latest.get("artifacts", {})
        background = self._artifact_from_payload(artifacts.get("background", {}), provider="background")
        if background is None or background.status != "created":
            background = self.background_builder.build(content, run_id)

        audio = self._artifact_from_payload(artifacts.get("audio", {}), provider="edge-tts") or ArtifactStatus(
            status="skipped",
            provider="edge-tts",
            message="No stored audio artifact.",
        )
        self._sync_audio_timings(content, audio)
        thumbnail = self.thumbnail_builder.build(content, run_id, background)
        subtitles = self.subtitle_builder.build(content, run_id)
        video = self.video_builder.build(content, run_id, audio, background)
        return PipelineResult(
            mode="render-only",
            status="success",
            selected_topic=content.topic.representative_title,
            background=background,
            thumbnail=thumbnail,
            subtitles=subtitles,
            video=video,
            warnings=[
                item.message
                for item in (background, thumbnail, subtitles, video)
                if item.message and item.status not in {"created"}
            ],
        )

    def upload_only(self) -> PipelineResult:
        """Upload the latest rendered video or return a mock result."""
        guard = self._guard_channel_operation(mode="upload-only", needs_generate=False, needs_render=False, needs_upload=True)
        if guard is not None:
            return guard

        latest = self.repository.latest_run(self._channel_id())
        if latest is None:
            return PipelineResult(mode="upload-only", status="failed", warnings=["No metadata to upload"])

        content = self._content_from_latest(latest)
        run_id = latest["run_id"]
        artifacts = latest.get("artifacts", {})
        background = self._artifact_from_payload(artifacts.get("background", {}), provider="background")
        thumbnail = self._artifact_from_payload(artifacts.get("thumbnail", {}), provider="thumbnail")
        if thumbnail is None or thumbnail.status != "created":
            thumbnail = self.thumbnail_builder.build(content, run_id, background)
        video = self._artifact_from_payload(artifacts.get("video", {}), provider="ffmpeg") or ArtifactStatus(
            status="skipped",
            provider="ffmpeg",
            message="No rendered video found in metadata.",
        )
        upload = self.youtube_uploader.upload(content, video, thumbnail)
        return PipelineResult(
            mode="upload-only",
            status="success",
            selected_topic=content.topic.representative_title,
            background=background,
            thumbnail=thumbnail,
            video=video,
            upload=upload,
            warnings=[upload.message] if upload.message and upload.status != "created" else [],
        )

    def _collect_and_rank(self) -> list[RankedTopic]:
        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        if preset.collection_mode == "news":
            rank_limit = max(self.config.scoring.top_k * 6, 18)
            google_topics = self.google_collector.collect()
            seeds = [item.title for item in google_topics]
            naver_topics = self.naver_collector.collect(seeds)
            candidates = google_topics + naver_topics
            include_keywords = self._include_keywords()
            if include_keywords:
                focus_queries = unique_preserve_order(self._diversity_seed_queries(preset.key) + include_keywords)
                focused_candidates = self.naver_collector.collect(
                    focus_queries,
                    limit=max(self.config.scoring.top_k * 6, len(focus_queries) * 2, 18),
                )
                candidates.extend(focused_candidates)
            candidates = self._dedupe_candidates(candidates)
            LOGGER.info("Collected %s candidates", len(candidates))
            ranked = rank_topics(candidates, self.config, top_k=rank_limit)
            filtered = self._filter_ranked_topics(ranked)
            if filtered:
                return filtered
            if preset.key == "welfare_news":
                actionable_ranked = [topic for topic in ranked if self._is_actionable_welfare_topic(topic)]
                if actionable_ranked:
                    return actionable_ranked

            if include_keywords:
                focused_candidates = self.naver_collector.collect(include_keywords, limit=max(self.config.scoring.top_k * 3, 10))
                focused_ranked = rank_topics(focused_candidates, self.config, top_k=rank_limit)
                focused_filtered = self._filter_ranked_topics(focused_ranked)
                if focused_filtered:
                    return focused_filtered
                if preset.key == "welfare_news":
                    actionable_focused = [topic for topic in focused_ranked if self._is_actionable_welfare_topic(topic)]
                    if actionable_focused:
                        return actionable_focused
                if focused_ranked and preset.key != "welfare_news":
                    return focused_ranked
            return [] if preset.key == "welfare_news" else ranked

        candidates = self.life_collector.collect(preset.collection_mode, limit=max(self.config.scoring.top_k, 5))
        ranked = rank_topics(candidates, self.config)
        return self._filter_ranked_topics(ranked) or ranked

    def _guard_channel_operation(
        self,
        *,
        mode: str,
        needs_generate: bool,
        needs_render: bool,
        needs_upload: bool,
    ) -> PipelineResult | None:
        channel = self.config.active_channel
        channel_name = channel.display_name if channel else "channel"
        channel_id = channel.id if channel else "unknown"
        if channel and not bool(getattr(channel, "enabled", True)):
            message = f"[SKIP] channel disabled: {channel_name}"
            LOGGER.info(message)
            return PipelineResult(mode=mode, status="skipped", warnings=[message], details={"channel_id": channel_id})
        if channel and needs_generate and not bool(getattr(channel, "auto_generate", True)):
            message = f"[SKIP] generation disabled: {channel_name}"
            LOGGER.info(message)
            return PipelineResult(mode=mode, status="skipped", warnings=[message], details={"channel_id": channel_id})
        if channel and needs_render and not bool(getattr(channel, "auto_render", True)):
            message = f"[SKIP] render disabled: {channel_name}"
            LOGGER.info(message)
            return PipelineResult(mode=mode, status="skipped", warnings=[message], details={"channel_id": channel_id})
        if channel and needs_upload and not bool(getattr(channel, "auto_upload", True)):
            message = f"[SKIP] upload disabled: {channel_name}"
            LOGGER.info(message)
            return PipelineResult(mode=mode, status="skipped", warnings=[message], details={"channel_id": channel_id})
        return None

    def _collect_details(self, topic: RankedTopic) -> list[TopicDetail]:
        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        if preset.collection_mode == "news":
            return self.detail_collector.collect(topic)
        return self.life_collector.collect_details(topic, preset.collection_mode)

    def _filter_ranked_topics(self, ranked_topics: list[RankedTopic]) -> list[RankedTopic]:
        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        include_keywords = [normalize_text(value) for value in self._include_keywords() if normalize_text(value)]
        exclude_keywords = [normalize_text(value) for value in self._exclude_keywords() if normalize_text(value)]
        if not include_keywords and not exclude_keywords:
            return ranked_topics

        filtered: list[RankedTopic] = []
        for topic in ranked_topics:
            haystack = normalize_text(" ".join([topic.representative_title, *topic.keywords, *topic.mentions]))
            if exclude_keywords and any(keyword in haystack for keyword in exclude_keywords):
                continue
            if include_keywords and not any(keyword in haystack for keyword in include_keywords):
                continue
            if preset.key == "economy_news" and not self._is_actionable_economy_topic(topic):
                continue
            if preset.key == "welfare_news" and not self._is_actionable_welfare_topic(topic):
                continue
            filtered.append(topic)
        return filtered

    def _select_topic(self, ranked_topics: list[RankedTopic], allow_duplicate: bool) -> RankedTopic | None:
        for topic in ranked_topics:
            if allow_duplicate or not self._is_blocked_topic(topic):
                return topic
        return ranked_topics[0] if allow_duplicate and ranked_topics else None

    def _select_topic_with_details(
        self,
        ranked_topics: list[RankedTopic],
        *,
        allow_duplicate: bool,
    ) -> tuple[RankedTopic | None, list[TopicDetail]]:
        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        fallback_topic = self._select_topic(ranked_topics, allow_duplicate=allow_duplicate)
        fallback_details = self._collect_details(fallback_topic) if fallback_topic else []
        if preset.key == "economy_news" and fallback_topic and not self._is_actionable_economy_candidate(fallback_topic, fallback_details):
            fallback_topic = None
            fallback_details = []
        if preset.key == "welfare_news" and fallback_topic and not self._is_actionable_welfare_candidate(fallback_topic, fallback_details):
            fallback_topic = None
            fallback_details = []
        eligible: list[tuple[float, RankedTopic, list[TopicDetail]]] = []
        reviewed: list[tuple[float, RankedTopic, list[TopicDetail]]] = []

        for topic in ranked_topics:
            if not allow_duplicate and self._is_blocked_topic(topic):
                continue
            details = self._collect_details(topic)
            if preset.key == "economy_news" and not self._is_actionable_economy_candidate(topic, details):
                continue
            adjusted_score = topic.score + self._topic_priority_boost(topic, details)
            reviewed.append((adjusted_score, topic, details))
            if self._details_satisfy_requirements(topic, details):
                eligible.append((adjusted_score, topic, details))

        if eligible:
            eligible.sort(key=lambda item: item[0], reverse=True)
            eligible = self._prioritize_diverse_candidates(eligible, preset_key=preset.key)
            _, topic, details = eligible[0]
            return topic, details

        if reviewed:
            reviewed.sort(key=lambda item: item[0], reverse=True)
            reviewed = self._prioritize_diverse_candidates(reviewed, preset_key=preset.key)
            if preset.key == "welfare_news":
                actionable_reviewed = [
                    item for item in reviewed if self._is_actionable_welfare_candidate(item[1], item[2])
                ]
                if actionable_reviewed:
                    _, topic, details = actionable_reviewed[0]
                    return topic, details
            _, topic, details = reviewed[0]
            return topic, details

        return fallback_topic, fallback_details

    def _is_blocked_topic(self, topic: RankedTopic) -> bool:
        if self.repository.is_duplicate(topic):
            return True
        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        if preset.collection_mode == "news" and self.repository.is_recently_redundant(topic, limit=5):
            return True
        return False

    def _details_satisfy_requirements(self, topic: RankedTopic, details: list[TopicDetail]) -> bool:
        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        if preset.collection_mode != "news":
            return True

        considered_details = details[:3]
        real_details = [item for item in considered_details if item.source and item.source != "fallback"]
        distinct_sources = {item.source for item in real_details}
        if len(real_details) < 2 or len(distinct_sources) < 2:
            return False

        if preset.key == "economy_news":
            return self._is_actionable_economy_candidate(topic, considered_details)

        if preset.key != "welfare_news":
            return True

        if not self._is_actionable_welfare_candidate(topic, considered_details):
            return False

        signals = self._welfare_requirement_signals(topic, considered_details)
        if self._is_welfare_planning_only(topic, considered_details):
            return False
        if not signals["official"]:
            return False
        return bool(signals["target"] and signals["benefit"] and (signals["application"] or signals["timing"]))

    def _is_actionable_welfare_candidate(self, topic: RankedTopic, details: list[TopicDetail]) -> bool:
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.keywords,
                    *(item.title for item in details[:4]),
                    *(item.summary for item in details[:4]),
                ]
            )
        )
        strong_actionable_terms = [
            "신청",
            "접수",
            "대상",
            "지급",
            "복지로",
            "주민센터",
            "행정복지센터",
            "바우처",
            "환급",
            "감면",
            "수당",
            "연금",
            "민생지원금",
            "생활지원금",
            "피해지원금",
        ]
        support_terms = ["지원금", "보조금", "혜택"]
        general_audience_terms = [
            "가구",
            "가정",
            "시민",
            "주민",
            "부모",
            "가족",
            "어르신",
            "청년",
            "노인",
            "국민",
            "전국민",
            "출산",
            "육아",
            "돌봄",
            "연령",
            "소득",
        ]
        editorial_noise_terms = [
            "실패를 넘어",
            "지속가능한",
            "기고",
            "칼럼",
            "사설",
            "논평",
            "서평",
            "인터뷰",
            "읽고",
            "돌아보다",
            "브리핑",
            "특집",
        ]
        entertainment_noise_terms = [
            "동상이몽",
            "핑크빛",
            "열애",
            "첫인상",
            "배우",
            "가수",
            "예능",
            "드라마",
            "방송인",
            "연예계",
            "아이돌",
        ]
        if any(normalize_text(term) in haystack for term in editorial_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in entertainment_noise_terms):
            return False
        if self._is_foreign_or_industry_welfare_noise(topic, details[:4]):
            return False
        if self._is_welfare_planning_only(topic, details[:4]):
            return False
        signals = self._welfare_requirement_signals(topic, details[:4])
        if signals["target"] and signals["benefit"] and (signals["application"] or signals["timing"]):
            return True
        if any(normalize_text(term) in haystack for term in strong_actionable_terms):
            return signals["official"] or signals["application"] or signals["benefit"]
        return (
            any(normalize_text(term) in haystack for term in support_terms)
            and any(normalize_text(term) in haystack for term in general_audience_terms)
            and (signals["official"] or signals["application"])
        )

    def _is_actionable_economy_candidate(self, topic: RankedTopic, details: list[TopicDetail]) -> bool:
        if self._is_overly_generic_economy_label(topic.representative_title):
            return False
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.keywords,
                    *(item.title for item in details[:4]),
                    *(item.summary for item in details[:4]),
                ]
            )
        )
        economy_terms = [
            "물가",
            "인플레이션",
            "금리",
            "기준금리",
            "대출",
            "이자",
            "집값",
            "부동산",
            "전세",
            "환율",
            "증시",
            "주가",
            "반도체",
            "수출",
            "유가",
            "원유",
            "세금",
            "교통",
            "교육",
            "보험",
            "연금",
            "관세",
            "예산",
            "소비",
            "경기",
            "무역",
            "한은",
        ]
        sports_noise_terms = [
            "골프",
            "야구",
            "축구",
            "농구",
            "배구",
            "테니스",
            "오거스타",
            "우승",
            "준우승",
            "신기록",
            "선수",
            "리그",
            "투어",
            "lpga",
            "pga",
        ]
        entertainment_noise_terms = [
            "열애",
            "드라마",
            "예능",
            "아이돌",
            "배우",
            "가수",
            "동상이몽",
            "핑크빛",
            "첫인상",
        ]
        editorial_noise_terms = [
            "사설",
            "칼럼",
            "기고",
            "논평",
            "인터뷰",
            "오피니언",
            "데스크칼럼",
            "기자수첩",
        ]
        low_intent_terms = [
            "인사이트",
            "북펀드",
            "출판",
            "대서사",
            "전시",
            "공연",
            "포럼",
            "세미나",
            "참가",
            "어떠세요",
        ]
        judicial_noise_terms = [
            "검찰",
            "특검",
            "경찰청",
            "압수수색",
            "재판",
            "징역",
            "구형",
            "혐의",
            "공범",
            "기소",
            "수사",
            "영장",
            "김건희",
            "도이치",
            "통일교",
            "주가조작",
        ]
        desk_jargon_terms = [
            "irs",
            "crs",
            "스와프",
            "커브",
            "민평금리",
            "cp금리",
            "채권딜",
        ]
        mixed_market_noise_terms = [
            "뉴욕증시",
            "비트코인",
            "엔비디아",
            "fomc",
            "연준",
            "나스닥",
            "다우",
            "s&p",
            "테슬라",
            "애플",
        ]
        if any(normalize_text(term) in haystack for term in sports_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in entertainment_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in editorial_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in low_intent_terms):
            return False
        if any(normalize_text(term) in haystack for term in judicial_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in desk_jargon_terms):
            return False
        if sum(1 for term in mixed_market_noise_terms if normalize_text(term) in haystack) >= 4:
            return False
        return any(normalize_text(term) in haystack for term in economy_terms)

    def _is_actionable_welfare_topic(self, topic: RankedTopic) -> bool:
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.mentions[:2],
                    *topic.keywords[:4],
                ]
            )
        )
        strong_actionable_terms = [
            "신청",
            "접수",
            "대상",
            "지급",
            "복지로",
            "주민센터",
            "행정복지센터",
            "바우처",
            "환급",
            "감면",
            "수당",
            "연금",
            "민생지원금",
            "생활지원금",
            "피해지원금",
        ]
        support_terms = ["지원금", "보조금", "혜택"]
        general_audience_terms = [
            "가구",
            "가정",
            "시민",
            "주민",
            "부모",
            "가족",
            "어르신",
            "청년",
            "노인",
            "국민",
            "전국민",
            "출산",
            "육아",
            "돌봄",
            "연령",
            "소득",
        ]
        editorial_noise_terms = [
            "실패를 넘어",
            "지속가능한",
            "기고",
            "칼럼",
            "사설",
            "논평",
            "서평",
            "인터뷰",
            "읽고",
            "돌아보다",
            "브리핑",
            "특집",
            "실험의 장",
        ]
        low_intent_terms = ["발대식", "개관", "센터", "봉사", "교육", "협약", "기념식", "문화센터", "우수사례"]
        entertainment_noise_terms = [
            "동상이몽",
            "핑크빛",
            "열애",
            "첫인상",
            "배우",
            "가수",
            "예능",
            "드라마",
            "방송인",
            "연예계",
            "아이돌",
        ]
        if any(normalize_text(term) in haystack for term in editorial_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in low_intent_terms):
            return False
        if any(normalize_text(term) in haystack for term in entertainment_noise_terms):
            return False
        if self._is_foreign_or_industry_welfare_noise(topic, []):
            return False
        planning_terms = ["논의", "검토", "추진", "예정", "계획", "가능성", "거론", "추경안", "운용 방안"]
        concrete_terms = strong_actionable_terms + ["금액", "연령", "소득", "가구", "조건"]
        if any(normalize_text(term) in haystack for term in planning_terms) and not any(
            normalize_text(term) in haystack for term in concrete_terms
        ):
            return False
        if any(normalize_text(term) in haystack for term in strong_actionable_terms):
            return True
        return (
            any(normalize_text(term) in haystack for term in support_terms)
            and any(normalize_text(term) in haystack for term in general_audience_terms)
        )

    def _welfare_requirement_signals(self, topic: RankedTopic, details: list[TopicDetail]) -> dict[str, bool]:
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.keywords,
                    *(item.title for item in details),
                    *(item.summary for item in details),
                ]
            )
        )
        target_terms = ("대상", "연령", "소득", "가구", "조건", "차상위", "수급자", "어르신", "청년", "부모", "시민", "주민")
        benefit_terms = ("혜택", "지원금", "보조금", "지급", "수당", "연금", "바우처", "감면", "환급", "급여", "지원", "금액")
        application_terms = ("신청", "접수", "복지로", "정부24", "주민센터", "행정복지센터", "홈페이지", "온라인", "방문")
        timing_terms = ("마감", "기한", "기간", "시행", "부터", "까지", "이번 달", "이번 주", "예산", "추경")
        return {
            "target": any(normalize_text(term) in haystack for term in target_terms),
            "benefit": any(normalize_text(term) in haystack for term in benefit_terms),
            "application": any(normalize_text(term) in haystack for term in application_terms),
            "timing": any(normalize_text(term) in haystack for term in timing_terms),
            "official": any(self._is_official_welfare_detail(item) for item in details),
        }

    def _is_welfare_planning_only(self, topic: RankedTopic, details: list[TopicDetail]) -> bool:
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.keywords,
                    *(item.title for item in details),
                    *(item.summary for item in details),
                ]
            )
        )
        planning_terms = ("논의", "검토", "추진", "예정", "계획", "가능성", "거론", "추경안", "운용 방안", "최종 확정 전")
        if not any(normalize_text(term) in haystack for term in planning_terms):
            return False
        signals = self._welfare_requirement_signals(topic, details)
        return not (signals["target"] and signals["benefit"] and (signals["application"] or signals["timing"]))

    def _is_official_welfare_detail(self, detail: TopicDetail) -> bool:
        haystack = normalize_text(
            " ".join(
                [
                    detail.source or "",
                    detail.title or "",
                    detail.summary or "",
                    detail.url or "",
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

    def _is_foreign_or_industry_welfare_noise(self, topic: RankedTopic, details: list[TopicDetail]) -> bool:
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.keywords,
                    *(item.title for item in details),
                    *(item.summary for item in details),
                ]
            )
        )
        foreign_terms = (
            "미국",
            "영국",
            "프랑스",
            "독일",
            "일본",
            "중국",
            "해외",
            "주요국",
            "비축유",
            "유류세",
            "원유",
            "정유업체",
            "국제유가",
        )
        domestic_terms = (
            "한국",
            "정부",
            "보건복지부",
            "고용노동부",
            "행정안전부",
            "교육부",
            "복지로",
            "정부24",
            "주민센터",
            "행정복지센터",
            "국민",
            "전국민",
            "주민",
            "시민",
            "지자체",
        )
        industry_terms = (
            "중소기업",
            "기업",
            "산업",
            "기술",
            "통신3사",
            "단말기",
            "정유사",
            "주유소",
            "소상공인 지원센터",
        )
        has_foreign = any(normalize_text(term) in haystack for term in foreign_terms)
        has_domestic = any(normalize_text(term) in haystack for term in domestic_terms)
        has_industry = any(normalize_text(term) in haystack for term in industry_terms)
        return has_industry or (has_foreign and not has_domestic)

    def _is_actionable_economy_topic(self, topic: RankedTopic) -> bool:
        if self._is_overly_generic_economy_label(topic.representative_title):
            return False
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.mentions[:2],
                    *topic.keywords[:6],
                ]
            )
        )
        economy_terms = [
            "물가",
            "인플레이션",
            "금리",
            "기준금리",
            "대출",
            "이자",
            "집값",
            "부동산",
            "전세",
            "환율",
            "증시",
            "주가",
            "반도체",
            "수출",
            "유가",
            "원유",
            "세금",
            "교통",
            "교육",
            "보험",
            "연금",
            "관세",
            "예산",
            "소비",
            "경기",
            "무역",
            "한은",
        ]
        sports_noise_terms = [
            "골프",
            "야구",
            "축구",
            "농구",
            "배구",
            "테니스",
            "오거스타",
            "우승",
            "준우승",
            "신기록",
            "선수",
            "리그",
            "투어",
            "lpga",
            "pga",
        ]
        entertainment_noise_terms = [
            "열애",
            "드라마",
            "예능",
            "아이돌",
            "배우",
            "가수",
            "동상이몽",
            "핑크빛",
            "첫인상",
        ]
        editorial_noise_terms = [
            "사설",
            "칼럼",
            "기고",
            "논평",
            "인터뷰",
            "오피니언",
            "데스크칼럼",
            "기자수첩",
        ]
        low_intent_terms = [
            "인사이트",
            "북펀드",
            "출판",
            "대서사",
            "전시",
            "공연",
            "포럼",
            "세미나",
            "참가",
            "어떠세요",
        ]
        judicial_noise_terms = [
            "검찰",
            "특검",
            "경찰청",
            "압수수색",
            "재판",
            "징역",
            "구형",
            "혐의",
            "공범",
            "기소",
            "수사",
            "영장",
            "김건희",
            "도이치",
            "통일교",
            "주가조작",
        ]
        desk_jargon_terms = [
            "irs",
            "crs",
            "스와프",
            "커브",
            "민평금리",
            "cp금리",
            "채권딜",
        ]
        mixed_market_noise_terms = [
            "뉴욕증시",
            "비트코인",
            "엔비디아",
            "fomc",
            "연준",
            "나스닥",
            "다우",
            "s&p",
            "테슬라",
            "애플",
        ]
        if any(normalize_text(term) in haystack for term in sports_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in entertainment_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in editorial_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in low_intent_terms):
            return False
        if any(normalize_text(term) in haystack for term in judicial_noise_terms):
            return False
        if any(normalize_text(term) in haystack for term in desk_jargon_terms):
            return False
        if sum(1 for term in mixed_market_noise_terms if normalize_text(term) in haystack) >= 4:
            return False
        return any(normalize_text(term) in haystack for term in economy_terms)

    @staticmethod
    def _is_overly_generic_economy_label(title: str) -> bool:
        normalized = normalize_text(title)
        if not normalized:
            return True

        generic_terms = {
            "경제",
            "물가",
            "인플레이션",
            "금리",
            "기준금리",
            "대출",
            "이자",
            "집값",
            "부동산",
            "전세",
            "환율",
            "증시",
            "주가",
            "주식",
            "반도체",
            "수출",
            "유가",
            "원유",
            "세금",
            "교통",
            "교육",
            "보험",
            "연금",
            "관세",
            "예산",
            "소비",
            "경기",
            "무역",
            "한은",
        }
        filler_terms = {
            "오늘",
            "이슈",
            "소식",
            "브리핑",
            "정리",
            "전망",
            "분석",
            "흐름",
            "변수",
            "핵심",
            "속보",
            "체크",
        }

        if normalized in generic_terms:
            return True

        token_source = str(title or "").replace("/", " ").replace("·", " ").replace(":", " ")
        tokens = [normalize_text(token) for token in token_source.split() if normalize_text(token)]
        if not tokens:
            return True

        meaningful_tokens = [token for token in tokens if token not in filler_terms]
        if not meaningful_tokens:
            return True

        return len(meaningful_tokens) <= 2 and all(token in generic_terms for token in meaningful_tokens)

    def _topic_priority_boost(self, topic: RankedTopic, details: list[TopicDetail]) -> float:
        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        boost = 0.0

        considered_details = details[:3]
        haystack = normalize_text(
            " ".join(
                [
                    topic.representative_title,
                    *topic.keywords,
                    *(item.title for item in considered_details),
                    *(item.summary for item in considered_details),
                ]
            )
        )
        if preset.key == "welfare_news":
            clean_general_audience_terms = [
                "생활지원금",
                "민생지원금",
                "가구",
                "가정",
                "시민",
                "주민",
                "부모",
                "가족",
                "어르신",
                "개인",
                "연금",
                "환급",
                "감면",
                "보조금",
            ]
            clean_business_terms = [
                "중소기업",
                "기업",
                "산업",
                "기술",
                "통신3사",
                "여행기",
            ]
            if any(normalize_text(term) in haystack for term in clean_general_audience_terms):
                boost += 1.5
            if any(normalize_text(term) in haystack for term in clean_business_terms):
                boost -= 1.5
            current_bucket = self._topic_bucket(
                representative_title=topic.representative_title,
                keywords=topic.keywords,
                preset_key=preset.key,
            )
            bucket_bonus = {
                "application": 2.2,
                "payment": 2.0,
                "eligibility": 1.0,
                "regional": 0.6,
                "general": -0.6,
            }
            boost += bucket_bonus.get(current_bucket, 0.0)
            low_intent_terms = ["발대식", "개관", "센터", "봉사", "교육", "협약", "기념식", "문화센터", "우수사례"]
            if any(normalize_text(term) in haystack for term in low_intent_terms):
                boost -= 3.0
            entertainment_noise_terms = [
                "동상이몽",
                "핑크빛",
                "열애",
                "첫인상",
                "배우",
                "가수",
                "예능",
                "드라마",
                "방송인",
                "연예계",
                "아이돌",
            ]
            if any(normalize_text(term) in haystack for term in entertainment_noise_terms):
                boost -= 4.5
            political_terms = ["대통령", "의원", "민주당", "국민의힘", "선거용", "정치", "반박", "지적"]
            if any(normalize_text(term) in haystack for term in political_terms):
                boost -= 1.8
            general_audience_terms = [
                "생활지원금",
                "민생지원금",
                "가구",
                "가정",
                "시민",
                "주민",
                "부모",
                "가족",
                "어르신",
                "노인",
                "연금",
                "돌봄",
                "감면",
                "환급",
            ]
            business_terms = [
                "중소기업",
                "기업",
                "산업",
                "기술",
                "통신3사",
                "단말기",
            ]
            if any(normalize_text(term) in haystack for term in general_audience_terms):
                boost += 1.5
            if any(normalize_text(term) in haystack for term in business_terms):
                boost -= 1.5
            signals = self._welfare_requirement_signals(topic, considered_details)
            if signals["official"]:
                boost += 1.6
            if signals["target"] and signals["benefit"]:
                boost += 1.2
            if signals["application"]:
                boost += 0.8
            if self._is_welfare_planning_only(topic, considered_details):
                boost -= 2.5

        boost -= self._recent_topic_penalty(topic, preset_key=preset.key)
        return boost

    def _recent_topic_penalty(self, topic: RankedTopic, *, preset_key: str) -> float:
        if preset_key not in {"economy_news", "welfare_news"}:
            return 0.0

        recent_items = self.repository.recent_processed(limit=5)
        if not recent_items:
            return 0.0

        current_bucket = self._topic_bucket(
            representative_title=topic.representative_title,
            keywords=topic.keywords,
            preset_key=preset_key,
        )
        current_haystack = normalize_text(" ".join([topic.representative_title, *topic.keywords]))
        penalty = 0.0

        for index, item in enumerate(recent_items):
            recent_bucket = self._topic_bucket(
                representative_title=str(item.get("representative_title", "")),
                keywords=[str(value) for value in item.get("keywords", []) if str(value).strip()],
                preset_key=preset_key,
            )
            recent_haystack = normalize_text(
                " ".join(
                    [
                        str(item.get("representative_title", "")),
                        *[str(value) for value in item.get("keywords", []) if str(value).strip()],
                    ]
                )
            )
            weight = max(0.8, 3.0 - (index * 0.45))
            if current_bucket and recent_bucket and current_bucket == recent_bucket:
                penalty = max(penalty, weight)
                continue
            overlap = sum(
                1
                for token in topic.keywords[:4]
                if normalize_text(str(token)) and normalize_text(str(token)) in recent_haystack
            )
            if overlap >= 2 or (current_haystack and recent_haystack and current_haystack == recent_haystack):
                penalty = max(penalty, weight - 0.2)
        return penalty

    def _prioritize_diverse_candidates(
        self,
        eligible: list[tuple[float, RankedTopic, list[TopicDetail]]],
        *,
        preset_key: str,
    ) -> list[tuple[float, RankedTopic, list[TopicDetail]]]:
        if preset_key not in {"economy_news", "welfare_news"} or len(eligible) <= 1:
            return eligible

        recent_buckets = self._recent_buckets(preset_key=preset_key, limit=3)
        blocked_buckets = {bucket for bucket in recent_buckets[:2] if bucket and bucket != "general"}
        if not blocked_buckets:
            return eligible

        diverse = [
            item
            for item in eligible
            if self._topic_bucket(
                representative_title=item[1].representative_title,
                keywords=item[1].keywords,
                preset_key=preset_key,
            )
            not in blocked_buckets
        ]
        if not diverse:
            return eligible
        return diverse + [item for item in eligible if item not in diverse]

    def _recent_buckets(self, *, preset_key: str, limit: int) -> list[str]:
        buckets: list[str] = []
        for item in self.repository.recent_processed(limit=limit):
            bucket = self._topic_bucket(
                representative_title=str(item.get("representative_title", "")),
                keywords=[str(value) for value in item.get("keywords", []) if str(value).strip()],
                preset_key=preset_key,
            )
            if bucket:
                buckets.append(bucket)
        return buckets

    def _dedupe_candidates(self, candidates: list[TopicCandidate]) -> list[TopicCandidate]:
        deduped: list[TopicCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = normalize_text(candidate.title)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    @staticmethod
    def _diversity_seed_queries(preset_key: str) -> list[str]:
        if preset_key == "welfare_news":
            return [
                "복지 신청",
                "지원금 대상",
                "연금 혜택",
                "바우처 지급",
                "감면 제도",
                "지자체 복지",
                "복지로 신청",
                "주민센터 혜택",
            ]
        return [
            "금리 대출",
            "환율 수출",
            "증시 반도체",
            "부동산 전세",
            "세금 정책",
            "교통 요금",
            "교육 정책",
            "소비 경기",
        ]

    @staticmethod
    def _topic_bucket(*, representative_title: str, keywords: list[str], preset_key: str) -> str:
        haystack = normalize_text(" ".join([representative_title, *keywords]))
        clean_bucket_map = (
            {
                "application": ["신청", "접수", "마감", "기한", "기간", "복지로", "주민센터", "홈페이지"],
                "eligibility": ["대상", "연령", "소득", "가구", "조건", "누가", "해당"],
                "payment": ["지급", "금액", "보조금", "지원금", "민생지원금", "혜택", "바우처", "환급"],
                "regional": ["지자체", "시민", "주민", "광명", "순천", "구별", "지역"],
            }
            if preset_key == "welfare_news"
            else {
                "inflation": ["물가", "인플레이션", "cpi", "생활비", "장바구니"],
                "rates": ["금리", "동결", "인하", "인상", "대출", "이자", "한은"],
                "housing": ["집값", "부동산", "주택", "전세", "주담대"],
                "markets": ["증시", "주가", "환율", "반도체", "수출", "코스피"],
                "global": ["이란", "중동", "미국", "중국", "호르무즈", "원유", "유가"],
                "policy": ["정책", "세금", "교육", "교통", "규제", "발표", "개편"],
            }
        )
        best_bucket = "general"
        best_score = 0
        for bucket, terms in clean_bucket_map.items():
            score = sum(2 for term in terms if normalize_text(term) in haystack)
            if score > best_score:
                best_bucket = bucket
                best_score = score
        if best_score:
            return best_bucket
        if preset_key == "welfare_news":
            bucket_map = {
                "application": ["신청", "접수", "마감", "기간", "복지로", "주민센터", "홈페이지"],
                "eligibility": ["대상", "연령", "소득", "가구", "조건", "누가"],
                "payment": ["지급", "금액", "환급", "연금", "혜택", "바우처"],
                "regional": ["지자체", "시민", "주민", "광명", "순천", "구별"],
            }
        else:
            bucket_map = {
                "inflation": ["물가", "인플레이션", "cpi", "생활비"],
                "rates": ["금리", "동결", "인하", "인상", "대출"],
                "housing": ["집값", "부동산", "주택", "전세", "주담대"],
                "markets": ["증시", "주가", "환율", "반도체", "수출"],
                "global": ["이란", "중동", "미국", "중국", "호르무즈", "원유", "유가"],
                "policy": ["정책", "세금", "교육", "교통", "규제", "발표", "개편"],
            }

        for bucket, terms in bucket_map.items():
            if any(normalize_text(term) in haystack for term in terms):
                return bucket
        return "general"

    def _build_run_id(self, topic: RankedTopic) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        suffix = secrets.token_hex(2)
        return f"{timestamp}_{suffix}_{slugify(topic.representative_title)}"

    def _include_keywords(self) -> list[str]:
        if not self.config.active_channel:
            return []

        configured = [keyword.strip() for keyword in self.config.active_channel.topic_include_keywords if keyword.strip()]
        if configured:
            return unique_preserve_order(configured)

        clean_defaults: dict[str, list[str]] = {
            "economy_news": [
                "물가",
                "금리",
                "대출",
                "집값",
                "부동산",
                "세금",
                "교통",
                "교육",
                "보험",
                "연금",
                "환율",
                "전기요금",
                "가스요금",
                "소비",
            ],
            "welfare_news": [
                "지원금",
                "혜택",
                "신청",
                "바우처",
                "감면",
                "급여",
                "연금",
                "수당",
                "환급",
                "보조금",
                "지자체",
                "접수",
                "지급",
                "모집",
            ],
        }
        preset_defaults = clean_defaults.get(self.config.active_channel.preset_key, [])
        if preset_defaults:
            return preset_defaults

        defaults: dict[str, list[str]] = {
            "economy_news": [
                "물가",
                "금리",
                "대출",
                "집값",
                "부동산",
                "세금",
                "교통",
                "교육",
                "보험",
                "연금",
                "환율",
                "전기요금",
                "가스요금",
                "소비",
            ],
            "welfare_news": [
                "지원금",
                "혜택",
                "신청",
                "바우처",
                "감면",
                "급여",
                "연금",
                "수당",
                "돌봄",
                "환급",
                "지원사업",
                "접수",
                "지급",
                "모집",
            ],
        }
        preset_defaults = defaults.get(self.config.active_channel.preset_key, [])
        if preset_defaults:
            return preset_defaults
        return list(self.config.active_channel.topic_include_keywords)

    def _exclude_keywords(self) -> list[str]:
        if not self.config.active_channel:
            return []

        configured = [keyword.strip() for keyword in self.config.active_channel.topic_exclude_keywords if keyword.strip()]
        clean_defaults: dict[str, list[str]] = {
            "economy_news": ["지지율", "공천", "총선", "대선", "후보", "정당", "북한"],
            "welfare_news": ["지지율", "공천", "총선", "대선", "후보", "정당", "청문회"],
        }
        preset_defaults = clean_defaults.get(self.config.active_channel.preset_key, [])
        if configured or preset_defaults:
            return unique_preserve_order([*configured, *preset_defaults])

        defaults: dict[str, list[str]] = {
            "economy_news": ["지지율", "공천", "당내", "의총", "대정부질문", "하버드", "전한길"],
            "welfare_news": ["지지율", "공천", "당내", "의총", "대정부질문", "정청래", "민주당", "국민의힘"],
        }
        preset_defaults = defaults.get(self.config.active_channel.preset_key, [])
        return list(dict.fromkeys([*self.config.active_channel.topic_exclude_keywords, *preset_defaults]))

    def _channel_id(self) -> str | None:
        return self.config.active_channel.id if self.config.active_channel else None

    @staticmethod
    def _artifact_from_payload(payload: dict[str, Any], provider: str) -> ArtifactStatus | None:
        if not payload:
            return None
        return ArtifactStatus(
            status=payload.get("status", "skipped"),
            provider=payload.get("provider", provider),
            path=payload.get("path"),
            message=payload.get("message", ""),
            extra=payload.get("extra", {}),
        )

    @staticmethod
    def _content_from_latest(latest: dict[str, Any]) -> GeneratedContent:
        topic_payload = latest["topic"]
        topic = RankedTopic(
            normalized_topic=topic_payload["normalized_topic"],
            representative_title=topic_payload["representative_title"],
            score=topic_payload["score"],
            sources=topic_payload["sources"],
            mentions=topic_payload["mentions"],
            keywords=topic_payload["keywords"],
        )
        content_payload = latest["content"]
        return GeneratedContent(
            topic=topic,
            video_title=content_payload["video_title"],
            script=content_payload["script"],
            description=content_payload["description"],
            tags=content_payload["tags"],
            segments=content_payload["segments"],
            content_format=content_payload.get("content_format", "short"),
            detail_points=content_payload.get("detail_points", []),
            estimated_duration_seconds=content_payload.get("estimated_duration_seconds", 0),
            preset_key=content_payload.get("preset_key", ""),
            background_prompt=content_payload.get("background_prompt", ""),
            thumbnail_prompt=content_payload.get("thumbnail_prompt", ""),
            contains_synthetic_media=content_payload.get("contains_synthetic_media", False),
            altered_content_reason=content_payload.get("altered_content_reason", ""),
            thumbnail_text=content_payload.get("thumbnail_text", topic.representative_title),
            hook_title=content_payload.get("hook_title", ""),
            hook_script=content_payload.get("hook_script", ""),
            hook_duration_seconds=float(content_payload.get("hook_duration_seconds", 0) or 0),
            hook_image_prompt=content_payload.get("hook_image_prompt", ""),
            scenes=[
                StoryScene(
                    index=int(scene.get("index", index)),
                    title=str(scene.get("title", "")),
                    summary=str(scene.get("summary", "")),
                    narration=str(scene.get("narration", "")),
                    image_prompt=str(scene.get("image_prompt", "")),
                    duration_seconds=float(scene.get("duration_seconds", 0) or 0),
                    visual_hint=str(scene.get("visual_hint", "")),
                )
                for index, scene in enumerate(content_payload.get("scenes", []), start=1)
                if isinstance(scene, dict)
            ],
        )

    def _sync_audio_timings(self, content: GeneratedContent, audio: ArtifactStatus) -> None:
        if content.content_format == "longform_story" and content.scenes and audio.extra:
            self._sync_story_audio_timings(content, audio)
            return

        if content.content_format != "short":
            return

        duration = self._probe_media_duration(str(audio.path or ""))
        if duration is None:
            return
        content.estimated_duration_seconds = max(1, int(round(duration)))

    def _sync_story_audio_timings(self, content: GeneratedContent, audio: ArtifactStatus) -> None:
        if content.content_format != "longform_story" or not content.scenes or not audio.extra:
            return

        segments = audio.extra.get("segments", [])
        if not isinstance(segments, list):
            return

        duration_by_label: dict[str, float] = {}
        for item in segments:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            path = str(item.get("path", "")).strip()
            duration = self._probe_media_duration(path)
            if label and duration is not None:
                normalized_duration = max(0.25, float(duration))
                duration_by_label[label] = normalized_duration
                item["duration_seconds"] = normalized_duration

        if not duration_by_label:
            return

        hook_duration = duration_by_label.get("hook")
        if hook_duration:
            content.hook_duration_seconds = hook_duration

        for scene in content.scenes:
            duration = duration_by_label.get(f"scene_{scene.index:02}")
            if duration:
                scene.duration_seconds = duration

        content.estimated_duration_seconds = max(
            1,
            int(round(float(content.hook_duration_seconds or 0) + sum(float(scene.duration_seconds or 0) for scene in content.scenes))),
        )

    def _probe_media_duration(self, path: str) -> float | None:
        media_path = str(path or "").strip()
        if not media_path:
            return None

        ffprobe = self._resolve_ffprobe_binary()
        if not ffprobe:
            return None

        process = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                media_path,
            ],
            cwd=str(self.config.project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            return None
        try:
            return float(process.stdout.strip())
        except ValueError:
            return None

    def _resolve_ffprobe_binary(self) -> str | None:
        system_binary = shutil.which("ffprobe")
        if system_binary:
            return system_binary

        ffmpeg_binary = str(getattr(self.video_builder.ffmpeg, "binary", "") or "").strip()
        if ffmpeg_binary:
            ffmpeg_path = Path(ffmpeg_binary)
            candidates = [
                ffmpeg_path.with_name("ffprobe.exe"),
                ffmpeg_path.with_name("ffprobe"),
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        return None
