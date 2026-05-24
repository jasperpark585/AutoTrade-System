from __future__ import annotations

import re

from app.config import AppConfig
from app.generation.ai_service import OpenAIContentService
from app.models import GeneratedContent, RankedTopic, StoryScene, TopicDetail
from app.storage.repository import StorageRepository
from app.studio.presets import preset_by_key
from app.utils.text import normalize_text, unique_preserve_order
from app.youtube.policy import decide_contains_synthetic_media


class ContentGenerator:
    """Generate channel-specific scripts, metadata, and prompts."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.ai = OpenAIContentService(config)
        self.repository = StorageRepository(config)

    def generate(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        preset = self._preset()
        if preset.collection_mode == "stories":
            ai_payload = self.ai.generate_story_package(topic=topic, details=details)
            if ai_payload:
                content = self._story_from_ai_payload(topic, ai_payload, details)
                if content is not None:
                    return self._finalize_story_content(content, details)
            return self._finalize_story_content(self._build_story_fallback(topic, details), details)

        ai_payload = self.ai.generate_briefing(topic=topic, details=details)
        if ai_payload:
            content = self._briefing_from_ai_payload(topic, ai_payload, details)
            if content is not None:
                return self._finalize_short_content(content, details)

        return self._finalize_short_content(self._build_short_fallback(topic, details), details)

    def _briefing_from_ai_payload(
        self,
        topic: RankedTopic,
        payload: dict[str, object],
        details: list[TopicDetail],
    ) -> GeneratedContent | None:
        segments = [str(item).strip() for item in payload.get("segments", []) if str(item).strip()]
        tags = [self._normalize_tag(str(item)) for item in payload.get("tags", []) if str(item).strip()]
        detail_points = [str(item).strip() for item in payload.get("detail_points", []) if str(item).strip()]
        title = str(payload.get("title", "")).strip()
        description = str(payload.get("description", "")).strip()
        altered_reason = str(payload.get("altered_content_reason", "")).strip()

        if not title or not description or len(segments) < 4:
            return None

        contains_synthetic_media = decide_contains_synthetic_media(
            self.config,
            topic=topic,
            ai_recommendation=str(payload.get("altered_content_answer", "")),
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._resolve_title(title, topic),
            script=" ".join(segments),
            description=self._resolve_description(description, topic, detail_points, tags),
            tags=unique_preserve_order([tag for tag in tags if tag])[: self.config.generation.max_tags],
            segments=segments[: self.config.generation.script_sections],
            detail_points=detail_points or [detail.summary for detail in details[:4]],
            estimated_duration_seconds=max(
                self.config.generation.target_duration_seconds,
                self._estimate_seconds(segments),
            ),
            preset_key=self._preset().key,
            background_prompt=self._resolve_background_prompt(str(payload.get("background_prompt", "")).strip(), topic),
            thumbnail_prompt=self._resolve_thumbnail_prompt(str(payload.get("thumbnail_prompt", "")).strip(), topic),
            contains_synthetic_media=contains_synthetic_media,
            altered_content_reason=altered_reason,
            thumbnail_text=self._resolve_thumbnail_text(str(payload.get("thumbnail_text", topic.representative_title)).strip(), topic),
            content_format="short",
        )

    def _story_from_ai_payload(
        self,
        topic: RankedTopic,
        payload: dict[str, object],
        details: list[TopicDetail],
    ) -> GeneratedContent | None:
        raw_scenes = payload.get("scenes", [])
        if not isinstance(raw_scenes, list) or not raw_scenes:
            return None

        scenes: list[StoryScene] = []
        for index, raw_scene in enumerate(raw_scenes, start=1):
            if not isinstance(raw_scene, dict):
                continue
            title = str(raw_scene.get("title", "")).strip()
            summary = str(raw_scene.get("summary", "")).strip()
            narration = str(raw_scene.get("narration", "")).strip()
            image_prompt = str(raw_scene.get("image_prompt", "")).strip()
            if not title or not narration:
                continue
            scenes.append(
                StoryScene(
                    index=index,
                    title=title,
                    summary=summary or title,
                    narration=narration,
                    image_prompt=image_prompt or self._story_image_prompt(topic, title, summary or narration[:120]),
                )
            )

        if len(scenes) < max(3, int(self.config.generation.story_scene_count * 0.6)):
            return None

        detail_points = [scene.summary for scene in scenes[:5]] or [detail.summary for detail in details[:5]]
        tags = [self._normalize_tag(str(item)) for item in payload.get("tags", []) if str(item).strip()]
        contains_synthetic_media = decide_contains_synthetic_media(
            self.config,
            topic=topic,
            ai_recommendation=str(payload.get("altered_content_answer", "yes")),
        )
        hook_script = str(payload.get("hook_script", "")).strip()
        return GeneratedContent(
            topic=topic,
            video_title=self._resolve_title(str(payload.get("title", "")).strip(), topic),
            script="",
            description=self._resolve_story_description(
                str(payload.get("description", "")).strip(),
                topic,
                detail_points,
                tags,
            ),
            tags=unique_preserve_order([tag for tag in tags if tag])[: self.config.generation.max_tags],
            segments=[],
            content_format="longform_story",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._resolve_background_prompt(str(payload.get("background_prompt", "")).strip(), topic),
            thumbnail_prompt=self._resolve_thumbnail_prompt(str(payload.get("thumbnail_prompt", "")).strip(), topic),
            contains_synthetic_media=contains_synthetic_media,
            altered_content_reason=str(payload.get("altered_content_reason", "")).strip(),
            thumbnail_text=self._resolve_thumbnail_text(str(payload.get("thumbnail_text", topic.representative_title)).strip(), topic),
            hook_title=str(payload.get("hook_title", "3분 몰입 훅")).strip() or "3분 몰입 훅",
            hook_script=hook_script,
            hook_image_prompt=str(payload.get("hook_image_prompt", "")).strip()
            or self._story_image_prompt(topic, "hook", hook_script[:160] or topic.representative_title),
            scenes=scenes[: self.config.generation.story_scene_count],
        )

    def _build_short_fallback(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        preset = self._preset()
        if preset.collection_mode == "quotes":
            return self._build_quotes_content(topic, details)
        if preset.collection_mode == "poems":
            return self._build_poem_content(topic, details)
        if preset.key == "welfare_news":
            return self._build_welfare_content(topic, details)
        return self._build_news_content(topic, details)

    def _build_news_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        summaries = [item.summary for item in details[:6]] or [topic.representative_title]
        while len(summaries) < 6:
            summaries.append(f"{topic.representative_title}와 관련해 후속 확인이 필요한 흐름이 이어지고 있습니다.")

        segments = [
            f"지금 가장 빠르게 정리해야 할 이슈는 {topic.representative_title}입니다.",
            f"오늘 브리핑에서는 이 주제가 왜 뜨는지 한 번에 정리해드리겠습니다.",
            f"먼저 현재 상황을 보면 {summaries[0]}",
            f"이 흐름이 커진 배경에는 {summaries[1]}",
            f"사람들이 이 이슈를 크게 보는 이유는 {summaries[2]}",
            f"지금 시장과 여론이 주목하는 키워드는 {', '.join(topic.keywords[:4] or [topic.normalized_topic])}입니다.",
            f"반응을 더 자세히 보면 {summaries[3]}",
            f"앞으로 체크해야 할 포인트는 {summaries[4]}",
            "중요한 건 단순한 화제성인지, 실제 변화로 이어질지 구분해서 보는 일입니다.",
            f"마지막으로 실무적으로 기억할 부분은 {summaries[5]}",
            "오늘 브리핑은 여기까지입니다. 다음 이슈도 가장 빠르게 정리해드리겠습니다.",
        ]
        tags = self._build_tags(topic, self._preset().key)
        detail_points = [item.title for item in details[:5]] or [topic.representative_title]
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{topic.representative_title} 이슈를 짧고 빠르게 이해할 수 있도록 핵심만 정리했습니다.",
            ),
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="그래픽 기반 뉴스형 구성으로 안내 문구만 관리하면 충분합니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
        )

    def _build_quotes_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        quote = topic.representative_title
        detail_points = [item.summary for item in details[:4]] or [quote]
        segments = [
            f"오늘 함께 볼 문장은 {quote}입니다.",
            "짧은 문장이지만 삶의 방향을 다시 보게 만드는 힘이 있습니다.",
            f"이 문장이 중요한 첫 번째 이유는 {detail_points[0]}",
            f"일상에 적용하면 {detail_points[1] if len(detail_points) > 1 else '아주 작은 태도 하나가 관계와 분위기를 바꾸기 때문입니다.'}",
            "변화는 늘 큰 결심보다 반복되는 작은 행동에서 시작됩니다.",
            f"관계에 적용하면 {detail_points[2] if len(detail_points) > 2 else '상대의 반응보다 내 마음을 먼저 정리하게 도와줍니다.'}",
            f"오늘의 실천 포인트는 {detail_points[3] if len(detail_points) > 3 else '하루가 끝나기 전에 이 문장을 떠올리며 한 가지 행동을 바꾸는 것입니다.'}",
            "문장은 짧지만 행동으로 이어질 때 비로소 삶을 바꾸게 됩니다.",
            "오늘의 명언은 여기까지입니다. 내일도 힘이 되는 문장으로 다시 찾아오겠습니다.",
        ]
        tags = self._build_tags(topic, self._preset().key)
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{quote}를 오늘 삶에 어떻게 적용할지 짧고 선명하게 정리했습니다.",
            ),
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="명언 채널용 그래픽 및 이미지 기반 구성입니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
        )

    def _build_poem_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        detail_points = [item.summary for item in details[:4]] or [topic.representative_title]
        segments = [
            f"오늘은 {topic.representative_title}라는 분위기를 천천히 읽어보겠습니다.",
            "짧은 문장일수록 마음에 오래 남는 잔상이 있습니다.",
            f"첫 번째 장면은 {detail_points[0]}",
            f"이 문장이 좋은 이유는 {detail_points[1] if len(detail_points) > 1 else '복잡한 감정을 조용히 풀어내기 때문입니다.'}",
            "감정을 설명하려 애쓰기보다 그대로 바라보는 순간이 필요할 때가 있습니다.",
            f"오늘의 해석 포인트는 {detail_points[2] if len(detail_points) > 2 else '흔들리는 마음을 억지로 몰아세우지 않는 것'}입니다.",
            f"마지막으로 기억할 문장은 {detail_points[3] if len(detail_points) > 3 else '지금의 마음도 언젠가 지나갈 풍경이라는 사실'}입니다.",
            "오늘의 낭독은 여기까지입니다. 다음 문장으로 다시 찾아오겠습니다.",
        ]
        tags = self._build_tags(topic, self._preset().key)
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{topic.representative_title}를 주제로 감정과 해석을 짧게 담은 영상입니다.",
            ),
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="감성형 이미지와 낭독 중심 구성입니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
        )

    def _build_story_fallback(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        scene_count = max(1, int(self.config.generation.story_scene_count))
        hook_script = (
            f"장례식장 앞에서 그녀는 끝내 들어가지 못했습니다. "
            f"한밤중에 도착한 문자 한 통 때문이었습니다. "
            f"평생 가족만 바라보고 살았던 주인공은 마지막 순간에 왜 가장 가까운 사람을 외면했을까요. "
            f"오늘 이야기는 {topic.representative_title}라는 한 문장에서 시작되지만, 결국 용서와 선택에 대한 기록으로 남게 됩니다."
        )
        detail_texts = [item.summary for item in details] or ["가족과 노후, 관계의 상처가 천천히 드러나는 이야기"]
        scene_titles = [
            "평범했던 아침이 무너진 날",
            "숨겨온 상처가 드러나는 순간",
            "아무에게도 말하지 못한 진심",
            "집을 떠나며 남긴 마지막 말",
            "늦게 도착한 편지의 내용",
            "다시 마주 앉은 가족의 저녁",
            "결국 남겨진 사람의 선택",
        ][:scene_count]
        scenes: list[StoryScene] = []
        for index, title in enumerate(scene_titles, start=1):
            detail = detail_texts[(index - 1) % len(detail_texts)]
            narration = self._fallback_story_narration(topic, title, detail, index, scene_count)
            scenes.append(
                StoryScene(
                    index=index,
                    title=title,
                    summary=detail,
                    narration=narration,
                    image_prompt=self._story_image_prompt(topic, title, detail),
                )
            )

        tags = self._build_tags(topic, self._preset().key)
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script="",
            description=self._build_story_description(
                topic=topic,
                detail_points=[scene.summary for scene in scenes[:5]],
                tags=tags,
            ),
            tags=tags,
            segments=[],
            content_format="longform_story",
            detail_points=[scene.summary for scene in scenes[:5]],
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=True,
            altered_content_reason="시니어 인생사연 롱폼은 AI 이미지와 재구성 서사를 함께 사용합니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
            hook_title="3분 몰입 훅",
            hook_script=hook_script,
            hook_image_prompt=self._story_image_prompt(topic, "hook", hook_script[:180]),
            scenes=scenes,
        )

    def _finalize_short_content(self, content: GeneratedContent, details: list[TopicDetail]) -> GeneratedContent:
        target_seconds = max(24, int(self.config.generation.target_duration_seconds * 0.8))
        segments = list(content.segments)
        detail_summaries = [item.summary for item in details if item.summary]
        keywords = ", ".join(content.topic.keywords[:4] or [content.topic.representative_title])

        while self._estimate_seconds(segments) < target_seconds:
            extra = self._supplementary_segment(
                next_index=len(segments),
                content=content,
                detail_summaries=detail_summaries,
                keywords=keywords,
            )
            if extra in segments:
                break
            segments.append(extra)
            if len(segments) >= 10:
                break

        content.segments = segments
        content.script = " ".join(segments)
        content.estimated_duration_seconds = max(content.estimated_duration_seconds, self._estimate_seconds(segments))
        return content

    def _finalize_story_content(self, content: GeneratedContent, details: list[TopicDetail]) -> GeneratedContent:
        target_seconds = max(1200, int(self.config.generation.target_duration_seconds))
        hook_script = content.hook_script.strip() or self._build_story_hook(content.topic, details)
        scenes = list(content.scenes)
        if not scenes:
            return self._build_story_fallback(content.topic, details)

        min_target = int(target_seconds * 0.88)
        detail_cycle = [item.summary for item in details if item.summary] or content.detail_points or [content.topic.representative_title]
        scene_pointer = 0
        while self._estimate_story_seconds(hook_script, scenes) < min_target:
            detail = detail_cycle[scene_pointer % len(detail_cycle)]
            scene = scenes[scene_pointer % len(scenes)]
            scene.narration = f"{scene.narration}\n\n{self._story_extension(content.topic, scene.title, detail, scene_pointer)}"
            scene_pointer += 1
            if scene_pointer > len(scenes) * 4:
                break

        hook_duration = max(self.config.generation.hook_duration_seconds, self._estimate_seconds([hook_script]))
        scene_weights = [max(1, self._estimate_seconds([scene.narration])) for scene in scenes]
        remaining = max(1, target_seconds - hook_duration)
        weight_total = sum(scene_weights) or len(scene_weights)
        for scene, weight in zip(scenes, scene_weights):
            scene.duration_seconds = max(120, int(remaining * (weight / weight_total)))

        content.hook_script = hook_script
        content.segments = [hook_script, *[scene.narration for scene in scenes]]
        content.script = "\n\n".join(content.segments)
        content.scenes = scenes
        content.estimated_duration_seconds = hook_duration + sum(scene.duration_seconds for scene in scenes)
        content.content_format = "longform_story"
        return content

    def _build_title(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        manual_title = channel.manual_title.strip() if channel and channel.manual_title else ""
        if manual_title:
            return manual_title
        pieces = [
            (channel.title_prefix if channel and channel.title_prefix else self.config.generation.title_prefix).strip(),
            topic.representative_title,
            (channel.title_suffix if channel and channel.title_suffix else self.config.generation.title_suffix).strip(),
        ]
        return " ".join(piece for piece in pieces if piece)

    def _build_description(
        self,
        *,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
        summary_intro: str,
    ) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_description.strip():
            return channel.manual_description.strip()

        lines = [
            self.config.generation.channel_name,
            "",
            summary_intro,
            "",
            "[이번 영상 핵심]",
            *(f"- {point}" for point in detail_points[:5]),
            "",
            self.config.generation.call_to_action,
        ]
        if self.config.generation.description_include_score:
            lines.extend(["", f"트렌드 점수: {topic.score:.2f}"])
        if self.config.generation.description_include_sources:
            lines.extend(["", f"수집 소스: {', '.join(topic.sources)}"])
        if self.config.generation.description_include_generation_note:
            lines.extend(["", self.config.generation.generation_note])
        lines.extend(["", "[해시태그]", " ".join(tags)])
        return "\n".join(lines)

    def _build_story_description(
        self,
        *,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
    ) -> str:
        return self._build_description(
            topic=topic,
            detail_points=detail_points,
            tags=tags,
            summary_intro=f"{topic.representative_title}를 바탕으로 한 1시간 내외의 시니어 인생사연 몰입 영상입니다.",
        )

    def _build_tags(self, topic: RankedTopic, preset_key: str) -> list[str]:
        preset = preset_by_key(preset_key)
        tags = [
            topic.representative_title.replace(" ", ""),
            *(keyword.replace(" ", "") for keyword in topic.keywords),
            self.config.generation.channel_name.replace(" ", ""),
            preset.label.replace(" ", ""),
            "유튜브자동화",
        ]
        normalized = [self._normalize_tag(tag) for tag in unique_preserve_order(tags)]
        return [tag for tag in normalized if tag][: self.config.generation.max_tags]

    @staticmethod
    def _normalize_tag(value: str) -> str:
        cleaned = value.strip().lstrip("#")
        return f"#{cleaned}" if cleaned else ""

    def _build_background_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_background_prompt.strip():
            return channel.manual_background_prompt.strip()
        return (
            f"Premium vertical editorial artwork for '{topic.representative_title}', "
            f"style: {self._preset().visual_style}, cinematic lighting, high detail, no text"
        )

    def _build_thumbnail_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_thumbnail_prompt.strip():
            return channel.manual_thumbnail_prompt.strip()
        return (
            f"Premium YouTube thumbnail background for '{topic.representative_title}', "
            f"style: {self._preset().visual_style}, dramatic focal point, no text"
        )

    def _resolve_title(self, title: str, topic: RankedTopic) -> str:
        manual_title = self.config.active_channel.manual_title.strip() if self.config.active_channel else ""
        if manual_title:
            return manual_title
        if self._preset().collection_mode == "news":
            return self._build_title(topic)
        candidate = self._strip_source_like_tokens(title or self._build_title(topic))
        candidate = re.sub(r"\s+", " ", candidate).strip(" -,:;_|")
        return candidate or self._build_title(topic)

    def _resolve_description(
        self,
        description: str,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
    ) -> str:
        manual_description = self.config.active_channel.manual_description.strip() if self.config.active_channel else ""
        if manual_description:
            return manual_description
        return description or self._build_description(
            topic=topic,
            detail_points=detail_points,
            tags=tags,
            summary_intro=f"{topic.representative_title} 이슈를 짧고 빠르게 이해할 수 있도록 핵심만 정리했습니다.",
        )

    def _resolve_story_description(
        self,
        description: str,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
    ) -> str:
        manual_description = self.config.active_channel.manual_description.strip() if self.config.active_channel else ""
        if manual_description:
            return manual_description
        return description or self._build_story_description(topic=topic, detail_points=detail_points, tags=tags)

    def _resolve_background_prompt(self, prompt: str, topic: RankedTopic) -> str:
        return prompt or self._build_background_prompt(topic)

    def _resolve_thumbnail_prompt(self, prompt: str, topic: RankedTopic) -> str:
        return prompt or self._build_thumbnail_prompt(topic)

    def _resolve_thumbnail_text(self, text: str, topic: RankedTopic) -> str:
        return (text or topic.representative_title).strip()[:26]

    def _preset(self):
        channel = self.config.active_channel
        return preset_by_key(channel.preset_key if channel else "economy_news")

    @staticmethod
    def _estimate_seconds(segments: list[str]) -> int:
        text = "".join(segments).replace(" ", "")
        return max(18, int(len(text) / 4.4))

    @staticmethod
    def _estimate_story_segment_seconds(text: str) -> int:
        compact = re.sub(r"\s+", "", text or "")
        return max(20, int(len(compact) / 6.0))

    def _estimate_story_seconds(self, hook_script: str, scenes: list[StoryScene]) -> int:
        total = self._estimate_story_segment_seconds(hook_script)
        total += sum(self._estimate_story_segment_seconds(scene.narration) for scene in scenes)
        return total

    def _supplementary_segment(
        self,
        *,
        next_index: int,
        content: GeneratedContent,
        detail_summaries: list[str],
        keywords: str,
    ) -> str:
        fallbacks = [
            f"이 주제를 볼 때 숫자보다 맥락을 함께 보는 것이 중요합니다. 지금 언급되는 {keywords} 같은 연결 키워드를 같이 보면 방향이 더 선명해집니다.",
            "당장 결론을 내리기보다 후속 발표와 실제 반응이 어떻게 이어지는지 확인하는 태도가 필요합니다.",
            "비슷한 이슈는 자주 등장하지만, 여러 변수와 함께 나타날 때는 생각보다 더 오래 영향을 남기곤 합니다.",
            "그래서 단순한 화제인지 실제 변화인지 구분해서 보는 습관이 중요합니다.",
            "영상을 본 뒤에는 공식 발표와 후속 기사 두세 개만 더 확인해도 이해의 깊이가 크게 달라집니다.",
        ]
        if detail_summaries:
            detail = detail_summaries[next_index % len(detail_summaries)]
            return f"추가로 기억할 부분은 {detail}입니다. 이 포인트는 전체 흐름을 이해하는 데 중요한 연결고리가 됩니다."
        return fallbacks[next_index % len(fallbacks)]

    def _build_title(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        manual_title = channel.manual_title.strip() if channel and channel.manual_title else ""
        if manual_title:
            return manual_title
        base_title = self._strip_source_like_tokens(self._short_topic_label(topic))
        base_title = re.sub(r"\s+", " ", base_title).strip(" -,:;_|")
        pieces = [
            (channel.title_prefix if channel and channel.title_prefix else self.config.generation.title_prefix).strip(),
            base_title,
            (channel.title_suffix if channel and channel.title_suffix else self.config.generation.title_suffix).strip(),
        ]
        return " ".join(piece for piece in pieces if piece).strip()

    def _build_tags(self, topic: RankedTopic, preset_key: str) -> list[str]:
        preset = preset_by_key(preset_key)
        cleaned_title = self._tag_token(self._strip_source_like_tokens(self._short_topic_label(topic)))
        keyword_tags = [
            self._tag_token(self._strip_source_like_tokens(keyword))
            for keyword in topic.keywords[:6]
        ]
        tags = [
            cleaned_title,
            *keyword_tags,
            self._tag_token(self.config.generation.channel_name),
            self._tag_token(preset.label),
            "shorts" if preset.collection_mode != "stories" else "",
            self._tag_token("유튜브자동화"),
        ]
        normalized = [self._normalize_tag(tag) for tag in unique_preserve_order(tags)]
        return [tag for tag in normalized if tag][: self.config.generation.max_tags]

    def _resolve_description(
        self,
        description: str,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
    ) -> str:
        manual_description = self.config.active_channel.manual_description.strip() if self.config.active_channel else ""
        if manual_description:
            return manual_description

        safe_points = self._sanitize_description_points(detail_points)
        label = self._short_topic_label(topic)
        if self._preset().collection_mode == "news":
            if self._preset().key == "welfare_news":
                intro = f"{label} 정보를 대상, 혜택, 신청 포인트 순서로 짧고 쉽게 정리했습니다."
            else:
                intro = f"{label} 이슈를 한 번에 이해할 수 있게 핵심만 정리했습니다."
            base = self._build_description(
                topic=topic,
                detail_points=safe_points or [label],
                tags=tags,
                summary_intro=intro,
            )
        else:
            base = description or self._build_description(
                topic=topic,
                detail_points=safe_points or detail_points,
                tags=tags,
                summary_intro=f"{label} 이슈를 짧고 자연스럽게 이해할 수 있도록 핵심만 정리했습니다.",
            )
        return self._apply_channel_description_rules(base, detail_points=safe_points or detail_points)

    def _apply_channel_description_rules(self, description: str, *, detail_points: list[str]) -> str:
        text = str(description or "").strip()
        clean_points = [
            point
            for point in self._sanitize_description_points(detail_points)
            if point
            and "검증에 사용한 핵심 사실 포인트" not in point
            and "공식 발표문" not in point
            and "원문 확인 필요" not in point
        ]
        hashtags = self._extract_hashtags(text)
        if self._preset().collection_mode != "stories" and "#shorts" not in [tag.lower() for tag in hashtags]:
            hashtags.append("#shorts")

        if self._preset().collection_mode != "news":
            return text

        blocked_markers = (
            "검증에 사용한 핵심 사실 포인트",
            "공식 발표문",
            "원문 확인 필요",
            "공식 사이트",
            "출처",
            "v.daum.net",
            "mk.co.kr",
            "nate.com",
            "네이트",
        )
        lead = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line == self.config.generation.channel_name:
                continue
            if line.startswith("[") or line.startswith("#"):
                continue
            if any(marker.lower() in line.lower() for marker in blocked_markers):
                continue
            cleaned = self._clean_detail_fact(self._strip_source_like_tokens(line)).rstrip(".")
            if cleaned:
                lead = cleaned + "."
                break

        if not lead:
            if self._preset().key == "welfare_news":
                lead = "이번 복지 정보를 대상, 혜택, 신청 순서로 이해하기 쉽게 정리했습니다."
            else:
                lead = "이번 뉴스 이슈를 한 번에 이해할 수 있게 핵심만 정리했습니다."

        blocks = [
            self.config.generation.channel_name,
            "",
            lead,
        ]
        if clean_points:
            blocks.extend(["", "[핵심 정리]", *[f"- {point.rstrip('.')}" for point in clean_points[:3]]])
        if self._preset().key == "welfare_news":
            blocks.extend(
                [
                    "",
                    "[오해하기 쉬운 부분]",
                    "- 지역, 연령, 소득 기준에 따라 실제 대상이 달라질 수 있습니다.",
                    "- 신청 기간과 예산 상황에 따라 지급 시점이나 접수 방식이 달라질 수 있습니다.",
                ]
            )
        blocks.extend(["", self.config.generation.call_to_action])
        if hashtags:
            blocks.extend(["", " ".join(hashtags[: self.config.generation.max_tags])])
        return "\n".join(blocks).strip()

    def _build_background_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_background_prompt.strip():
            return channel.manual_background_prompt.strip()

        title = self._short_topic_label(topic)
        preset_key = self._preset().key
        if preset_key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts news background. Primary request: create a high-CTR vertical image about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: Korean adult reacting to a real-life economic shock or policy change. "
                "Style/medium: photoreal Korean editorial thumbnail frame, vivid but trustworthy, dramatic contrast, mobile-first readability. "
                "Composition/framing: 9:16, upper-body close-up or medium shot, one strong visual focal point, clean text-safe space at top and lower-center. "
                "Constraints: no watermark, no logo, no embedded text, no screenshot, no article clipping."
            )
        if preset_key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts life-info background. Primary request: create a high-CTR vertical image about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: Korean adult or family member checking a phone alert, letter, welfare notice, or payment update. "
                "Style/medium: photoreal Korean life-benefit thumbnail frame, practical and trustworthy, emotionally engaging. "
                "Composition/framing: 9:16, expressive face, one clear prop, clean text-safe space at top and lower-center. "
                "Constraints: no government logo, no watermark, no embedded text, no screenshot."
            )
        return (
            f"Premium vertical editorial artwork for '{title}', "
            f"style: {self._preset().visual_style}, cinematic lighting, high detail, no text"
        )

    def _build_thumbnail_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_thumbnail_prompt.strip():
            return channel.manual_thumbnail_prompt.strip()

        title = self._short_topic_label(topic)
        if self._preset().key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: shocked or sharply focused Korean adult reacting to a market, price, exchange-rate, or policy alert. "
                "Style/medium: photoreal Korean breaking-news thumbnail, crisp and high-contrast with strong curiosity. "
                "Composition/framing: 9:16, large face emphasis, one decisive prop or signal, dramatic depth, strong text-safe space. "
                "Constraints: no watermark, no logo, no embedded text, no article screenshot."
            )
        if self._preset().key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: middle-aged Korean adult reacting to a benefit notice, 신청 화면, payment alert, or letter. "
                "Style/medium: photoreal Korean life-info thumbnail, clear and trustworthy with strong curiosity. "
                "Composition/framing: 9:16, expressive face, simple practical prop, warm interior depth, strong text-safe space. "
                "Constraints: no government logo, no watermark, no embedded text."
            )
        return (
            f"Premium YouTube thumbnail background for '{title}', "
            f"style: {self._preset().visual_style}, dramatic focal point, no text"
        )

    def _resolve_thumbnail_text(self, text: str, topic: RankedTopic) -> str:
        candidate = self._strip_source_like_tokens((text or self._short_topic_label(topic)).strip())
        if self._preset().collection_mode == "stories":
            candidate = re.split(r"[|:!?]", candidate, maxsplit=1)[0].strip()
            candidate = re.sub(r"\s+", " ", candidate)
            words = candidate.split()
            if len(words) > 4:
                candidate = " ".join(words[:4])
            return candidate[:18].strip()
        if self._preset().collection_mode == "news":
            return self._pick_short_thumbnail_text(topic, [candidate] if candidate else [], preferred_text=candidate)
        return candidate[:26]

    def _resolve_short_thumbnail_text(self, topic: RankedTopic, detail_points: list[str]) -> str:
        return self._pick_short_thumbnail_text(topic, detail_points, preferred_text="")

    def _news_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "inflation":
            return (
                "Korean grocery aisle or kitchen table with price tags, receipt, shopping basket, and anxious reaction, "
                "consumer-cost pressure, cinematic news lighting"
            )
        if bucket == "rates":
            return (
                "Korean bank-loan or mortgage tension scene with loan papers, smartphone banking alert, calculator, "
                "serious adult reaction, crisp newsroom contrast"
            )
        if bucket == "housing":
            return (
                "Korean apartment skyline or home-loan consultation mood, contract papers, house model, "
                "tense realistic housing-market atmosphere"
            )
        if bucket == "markets":
            return (
                "Korean market-watch scene with exchange-rate or stock movement on phone screen, city night lights, "
                "high-volatility financial mood"
            )
        if bucket == "global":
            return (
                "global-tension news scene with oil, shipping route, or world-map crisis motif, "
                "split-focus confrontation atmosphere and dramatic broadcast lighting"
            )
        if bucket == "policy":
            return (
                "Korean public-policy reaction scene with document, briefing backdrop, urban daily-life setting, "
                "clear change-impact mood"
            )
        return (
            "Korean breaking-news atmosphere with a reacting adult, alert screen glow, dramatic newsroom lighting, "
            "high public-interest issue visual"
        )

    def _welfare_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "application":
            return (
                "Korean resident checking an application page, 주민센터 notice, or deadline alert on smartphone, "
                "urgent but trustworthy life-info mood"
            )
        if bucket == "eligibility":
            return (
                "middle-aged Korean adult reviewing eligibility papers, letter, or welfare criteria checklist at home, "
                "clear practical-life tension"
            )
        if bucket == "payment":
            return (
                "Korean phone payment alert, envelope, or support-message scene with surprised but hopeful reaction, "
                "practical benefit and money timing mood"
            )
        if bucket == "regional":
            return (
                "Korean neighborhood or local government service setting with resident checking region-specific benefit information, "
                "realistic civic-life atmosphere"
            )
        return (
            "middle-aged Korean person or couple reacting to a life-benefit message, warm home or desk setting, "
            "clear eligibility-and-benefit atmosphere"
        )

    def _build_background_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_background_prompt.strip():
            return channel.manual_background_prompt.strip()

        title = self._short_topic_label(topic)
        preset_key = self._preset().key
        if preset_key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts news background. Primary request: create a high-CTR vertical image about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: Korean adult reacting to a real-life economic shock or policy change. "
                "Style/medium: photoreal Korean editorial thumbnail frame, vivid but trustworthy, dramatic contrast, mobile-first readability. "
                "Composition/framing: 9:16, upper-body close-up or medium shot, one strong visual focal point, clean text-safe space at top and lower-center. "
                "Constraints: no watermark, no logo, no embedded text, no screenshot, no article clipping."
            )
        if preset_key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts life-info background. Primary request: create a high-CTR vertical image about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: Korean adult or family member checking a phone alert, letter, welfare notice, or payment update. "
                "Style/medium: photoreal Korean life-benefit thumbnail frame, practical and trustworthy, emotionally engaging. "
                "Composition/framing: 9:16, expressive face, one clear prop, clean text-safe space at top and lower-center. "
                "Constraints: no government logo, no watermark, no embedded text, no screenshot."
            )
        return (
            f"Premium vertical editorial artwork for '{title}', "
            f"style: {self._preset().visual_style}, cinematic lighting, high detail, no text"
        )

    def _build_thumbnail_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_thumbnail_prompt.strip():
            return channel.manual_thumbnail_prompt.strip()

        title = self._short_topic_label(topic)
        if self._preset().key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: shocked or sharply focused Korean adult reacting to a market, price, exchange-rate, or policy alert. "
                "Style/medium: photoreal Korean breaking-news thumbnail, crisp and high-contrast with strong curiosity. "
                "Composition/framing: 9:16, large face emphasis, one decisive prop or signal, dramatic depth, strong text-safe space. "
                "Constraints: no watermark, no logo, no embedded text, no article screenshot."
            )
        if self._preset().key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: middle-aged Korean adult reacting to a benefit notice, 신청 화면, payment alert, or letter. "
                "Style/medium: photoreal Korean life-info thumbnail, clear and trustworthy with strong curiosity. "
                "Composition/framing: 9:16, expressive face, simple practical prop, warm interior depth, strong text-safe space. "
                "Constraints: no government logo, no watermark, no embedded text."
            )
        return (
            f"Premium YouTube thumbnail background for '{title}', "
            f"style: {self._preset().visual_style}, dramatic focal point, no text"
        )

    def _resolve_thumbnail_text(self, text: str, topic: RankedTopic) -> str:
        candidate = self._strip_source_like_tokens((text or self._short_topic_label(topic)).strip())
        if self._preset().collection_mode == "stories":
            candidate = re.split(r"[|:!?]", candidate, maxsplit=1)[0].strip()
            candidate = re.sub(r"\s+", " ", candidate)
            words = candidate.split()
            if len(words) > 4:
                candidate = " ".join(words[:4])
            return candidate[:18].strip()
        if self._preset().collection_mode == "news":
            return self._pick_short_thumbnail_text(topic, [candidate] if candidate else [], preferred_text=candidate)
        return candidate[:26]

    def _resolve_short_thumbnail_text(self, topic: RankedTopic, detail_points: list[str]) -> str:
        return self._pick_short_thumbnail_text(topic, detail_points, preferred_text="")

    def _news_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "inflation":
            return (
                "Korean grocery aisle or kitchen table with price tags, receipt, shopping basket, and anxious reaction, "
                "consumer-cost pressure, cinematic news lighting"
            )
        if bucket == "rates":
            return (
                "Korean bank-loan or mortgage tension scene with loan papers, smartphone banking alert, calculator, "
                "serious adult reaction, crisp newsroom contrast"
            )
        if bucket == "housing":
            return (
                "Korean apartment skyline or home-loan consultation mood, contract papers, house model, "
                "tense realistic housing-market atmosphere"
            )
        if bucket == "markets":
            return (
                "Korean market-watch scene with exchange-rate or stock movement on phone screen, city night lights, "
                "high-volatility financial mood"
            )
        if bucket == "global":
            return (
                "global-tension news scene with oil, shipping route, or world-map crisis motif, "
                "split-focus confrontation atmosphere and dramatic broadcast lighting"
            )
        if bucket == "policy":
            return (
                "Korean public-policy reaction scene with document, briefing backdrop, urban daily-life setting, "
                "clear change-impact mood"
            )
        return (
            "Korean breaking-news atmosphere with a reacting adult, alert screen glow, dramatic newsroom lighting, "
            "high public-interest issue visual"
        )

    def _welfare_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "application":
            return (
                "Korean resident checking an application page, 주민센터 notice, or deadline alert on smartphone, "
                "urgent but trustworthy life-info mood"
            )
        if bucket == "eligibility":
            return (
                "middle-aged Korean adult reviewing eligibility papers, letter, or welfare criteria checklist at home, "
                "clear practical-life tension"
            )
        if bucket == "payment":
            return (
                "Korean phone payment alert, envelope, or support-message scene with surprised but hopeful reaction, "
                "practical benefit and money timing mood"
            )
        if bucket == "regional":
            return (
                "Korean neighborhood or local government service setting with resident checking region-specific benefit information, "
                "realistic civic-life atmosphere"
            )
        return (
            "middle-aged Korean person or couple reacting to a life-benefit message, warm home or desk setting, "
            "clear eligibility-and-benefit atmosphere"
        )

    def _build_background_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_background_prompt.strip():
            return channel.manual_background_prompt.strip()

        title = self._short_topic_label(topic)
        preset_key = self._preset().key
        if preset_key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts news background. Primary request: create a high-CTR vertical image about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: Korean adult reacting to a real-life economic shock or policy change. "
                "Style/medium: photoreal Korean editorial thumbnail frame, vivid but trustworthy, dramatic contrast, mobile-first readability. "
                "Composition/framing: 9:16, upper-body close-up or medium shot, one strong visual focal point, clean text-safe space at top and lower-center. "
                "Constraints: no watermark, no logo, no embedded text, no screenshot, no article clipping."
            )
        if preset_key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts life-info background. Primary request: create a high-CTR vertical image about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: Korean adult or family member checking a phone alert, letter, welfare notice, or payment update. "
                "Style/medium: photoreal Korean life-benefit thumbnail frame, practical and trustworthy, emotionally engaging. "
                "Composition/framing: 9:16, expressive face, one clear prop, clean text-safe space at top and lower-center. "
                "Constraints: no government logo, no watermark, no embedded text, no screenshot."
            )
        return (
            f"Premium vertical editorial artwork for '{title}', "
            f"style: {self._preset().visual_style}, cinematic lighting, high detail, no text"
        )

    def _build_thumbnail_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_thumbnail_prompt.strip():
            return channel.manual_thumbnail_prompt.strip()

        title = self._short_topic_label(topic)
        if self._preset().key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: shocked or sharply focused Korean adult reacting to a market, price, exchange-rate, or policy alert. "
                "Style/medium: photoreal Korean breaking-news thumbnail, crisp and high-contrast with strong curiosity. "
                "Composition/framing: 9:16, large face emphasis, one decisive prop or signal, dramatic depth, strong text-safe space. "
                "Constraints: no watermark, no logo, no embedded text, no article screenshot."
            )
        if self._preset().key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: middle-aged Korean adult reacting to a benefit notice, 신청 화면, payment alert, or letter. "
                "Style/medium: photoreal Korean life-info thumbnail, clear and trustworthy with strong curiosity. "
                "Composition/framing: 9:16, expressive face, simple practical prop, warm interior depth, strong text-safe space. "
                "Constraints: no government logo, no watermark, no embedded text."
            )
        return (
            f"Premium YouTube thumbnail background for '{title}', "
            f"style: {self._preset().visual_style}, dramatic focal point, no text"
        )

    def _resolve_thumbnail_text(self, text: str, topic: RankedTopic) -> str:
        candidate = self._strip_source_like_tokens((text or self._short_topic_label(topic)).strip())
        if self._preset().collection_mode == "stories":
            candidate = re.split(r"[|:!?]", candidate, maxsplit=1)[0].strip()
            candidate = re.sub(r"\s+", " ", candidate)
            words = candidate.split()
            if len(words) > 4:
                candidate = " ".join(words[:4])
            return candidate[:18].strip()
        if self._preset().collection_mode == "news":
            return self._pick_short_thumbnail_text(topic, [candidate] if candidate else [], preferred_text=candidate)
        return candidate[:26]

    def _resolve_short_thumbnail_text(self, topic: RankedTopic, detail_points: list[str]) -> str:
        return self._pick_short_thumbnail_text(topic, detail_points, preferred_text="")

    def _pick_short_thumbnail_text(
        self,
        topic: RankedTopic,
        detail_points: list[str],
        *,
        preferred_text: str,
    ) -> str:
        recent_texts = self._recent_thumbnail_texts(limit=8)
        normalized_recent = [self._short_text_key(text) for text in recent_texts if self._short_text_key(text)]
        bucket = self._short_topic_bucket(topic, detail_points)
        candidates: list[str] = []

        cleaned_preferred = self._clean_short_thumbnail_phrase(preferred_text)
        if cleaned_preferred and not self._is_generic_short_thumbnail_text(cleaned_preferred):
            candidates.append(cleaned_preferred)

        candidates.extend(self._fact_driven_thumbnail_candidates(topic, detail_points))
        candidates.extend(self._thumbnail_phrase_candidates(topic, detail_points, bucket=bucket))
        candidates = unique_preserve_order([self._clean_short_thumbnail_phrase(candidate) for candidate in candidates if candidate])

        for candidate in candidates:
            normalized = self._short_text_key(candidate)
            if not normalized:
                continue
            if any(
                normalized == recent
                or normalized in recent
                or recent in normalized
                for recent in normalized_recent
            ):
                continue
            return candidate

        for candidate in candidates:
            if candidate:
                return candidate

        return self._clean_short_thumbnail_phrase(self._short_topic_label(topic))[:18]

    def _fact_driven_thumbnail_candidates(self, topic: RankedTopic, detail_points: list[str]) -> list[str]:
        label = self._short_topic_label(topic)
        focus_text = " ".join([label, *detail_points[:2]])
        candidates: list[str] = []
        amount_match = re.search(r"(최대\s*\d+\s*억\s*원|\d+\s*억\s*원|\d+\s*만\s*원|\d+\s*원)", focus_text)
        amount = re.sub(r"\s+", " ", amount_match.group(1)).strip() if amount_match else ""

        if "포상금" in focus_text:
            if amount:
                candidates.append(f"{amount} 포상금")
            if "신고" in focus_text:
                candidates.append("신고하면 포상금")
            candidates.append("포상금 어디까지")
        if "환급" in focus_text:
            if amount:
                candidates.append(f"{amount} 환급")
            candidates.append("환급 얼마나 받나")
        if "지원금" in focus_text or "보조금" in focus_text:
            if amount:
                candidates.append(f"{amount} 지원")
            candidates.append("지원 규모 어디까지")

        return candidates

    def _thumbnail_phrase_candidates(
        self,
        topic: RankedTopic,
        detail_points: list[str],
        *,
        bucket: str,
    ) -> list[str]:
        label = self._short_topic_label(topic)
        preset_key = self._preset().key
        if preset_key == "welfare_news":
            phrase_bank = {
                "application": ["오늘 신청 막차", "지금 안 보면 늦는다", "신청창구 열렸나", "이번 접수 뭐가 달라졌나"],
                "eligibility": ["나는 받을 수 있나", "이번엔 어디까지 해당", "누가 먼저 챙겨야 하나", "대상 기준 달라졌다"],
                "payment": ["이번엔 얼마까지", "언제 입금되나", "지원금 얼마나 오나", "혜택 규모 어디까지"],
                "regional": ["우리 지역도 해당될까", "지역마다 뭐가 다를까", "이번엔 어디가 먼저", "지자체별 차이 크다"],
                "general": ["이번 복지 뭐가 달라졌나", "지금 챙길 정보", "놓치면 아쉬운 혜택", "이번엔 뭐가 풀리나"],
            }
        else:
            finance_haystack = " ".join([label, *detail_points[:2]])
            if any(term in finance_haystack for term in ("적금", "예금", "저축", "우대금리", "청약")):
                phrase_bank = {
                    "rates": ["적금 혜택 얼마나", "우대금리 어디까지", "지금 가입이 유리할까", "저축 혜택 더 커지나"],
                    "policy": ["적금 혜택 바뀌나", "가입 조건 뭐가 달라졌나", "이번엔 얼마나 더 붙나", "지금 챙길 포인트"],
                    "general": ["우대금리 얼마나", "적금 혜택 커지나", "이번엔 뭐가 달라졌나", "지금 챙길 포인트"],
                }
            else:
                phrase_bank = {
                "inflation": ["생활비가 왜 들썩이나", "이번 달 물가 변수", "뭐가 먼저 오르나", "장바구니 왜 불안한가"],
                "rates": ["금리 방향 또 바뀌나", "대출 숨통 트일까", "이번엔 금리 멈출까", "이자 부담 언제 풀리나"],
                "housing": ["부동산 변수 다시 커지나", "집값보다 이게 먼저", "전세 판이 또 흔들리나", "대출 규제 뭐가 바뀌나"],
                "markets": ["증시가 왜 출렁이나", "주가 왜 흔들리나", "시장 왜 흔들리나", "금융시장 왜 출렁이나"],
                "global": ["중동 변수 어디까지", "유가가 다시 흔들리나", "해외 변수 국내로 오나", "환율 불안 왜 커지나"],
                "policy": ["이번엔 뭐가 달라지나", "새 정책 어디부터 바뀌나", "세금 흐름 또 변하나", "생활 규제 뭐가 달라지나"],
                "general": ["지금 시장 뭐가 바뀌나", "오늘 경제 포인트", "이번 이슈 어디까지", "생활경제 왜 흔들리나"],
            }

        base_candidates = list(phrase_bank.get(bucket, phrase_bank["general"]))
        recent_bucket_count = self._recent_bucket_count(bucket, limit=6)
        if base_candidates and recent_bucket_count:
            shift = recent_bucket_count % len(base_candidates)
            base_candidates = base_candidates[shift:] + base_candidates[:shift]
        candidates = list(base_candidates)
        if label:
            headline = re.split(r"[,:|·!?]", label, maxsplit=1)[0].strip()
            headline = re.sub(r"\s+", " ", headline)
            if 6 <= len(headline) <= 18:
                candidates.append(headline)

        for point in detail_points[:2]:
            cleaned = self._clean_short_thumbnail_phrase(point)
            if cleaned and not self._is_generic_short_thumbnail_text(cleaned):
                candidates.append(cleaned)
        return candidates

    def _short_topic_bucket(self, topic: RankedTopic, detail_points: list[str]) -> str:
        return self._short_topic_bucket_from_parts(
            representative_title=topic.representative_title,
            keywords=topic.keywords,
            detail_points=detail_points,
        )

    def _short_topic_bucket_from_parts(
        self,
        *,
        representative_title: str,
        keywords: list[str],
        detail_points: list[str],
    ) -> str:
        title_haystack = self._short_text_key(" ".join([representative_title, *keywords]))
        detail_haystack = self._short_text_key(" ".join(detail_points[:3]))
        if self._preset().key == "welfare_news":
            bucket_map = {
                "application": ["신청", "접수", "마감", "기한", "기간", "복지로", "주민센터", "홈페이지"],
                "eligibility": ["대상", "연령", "소득", "가구", "조건", "누가", "해당"],
                "payment": ["지급", "입금", "금액", "지원금", "보조금", "혜택", "바우처", "환급"],
                "regional": ["지자체", "시민", "주민", "지역", "광명", "순천", "구별"],
            }
        else:
            bucket_map = {
                "inflation": ["물가", "인플레이션", "cpi", "생활비", "장바구니"],
                "rates": ["금리", "대출", "이자", "한은", "동결", "인하", "인상"],
                "housing": ["부동산", "집값", "주택", "전세", "주담대"],
                "markets": ["증시", "주가", "환율", "수출", "반도체", "무역"],
                "global": ["중동", "이란", "미국", "중국", "호르무즈", "원유", "유가"],
                "policy": ["정책", "세금", "교육", "교통", "규제", "개편", "발표"],
            }
        best_bucket = "general"
        best_score = 0
        for bucket, terms in bucket_map.items():
            score = 0
            for term in terms:
                term_key = self._short_text_key(term)
                if not term_key:
                    continue
                if term_key in title_haystack:
                    score += 2
                elif term_key in detail_haystack:
                    score += 1
            if score > best_score:
                best_bucket = bucket
                best_score = score
        return best_bucket

    def _recent_bucket_count(self, bucket: str, *, limit: int) -> int:
        if not bucket:
            return 0
        count = 0
        for item in self.repository.recent_processed(limit=limit):
            recent_bucket = self._short_topic_bucket_from_parts(
                representative_title=str(item.get("representative_title", "")),
                keywords=[str(value) for value in item.get("keywords", []) if str(value).strip()],
                detail_points=[],
            )
            if recent_bucket == bucket:
                count += 1
        return count

    def _recent_thumbnail_texts(self, *, limit: int) -> list[str]:
        items = self.repository.recent_processed(limit=limit)
        values: list[str] = []
        for item in items:
            thumbnail_text = str(item.get("thumbnail_text", "")).strip()
            if thumbnail_text:
                values.append(thumbnail_text)
        return values

    @staticmethod
    def _clean_short_thumbnail_phrase(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        cleaned = re.sub(r"^[\-\|\:\•·]+", "", cleaned).strip()
        cleaned = re.sub(r"[\"'`]", "", cleaned)
        cleaned = cleaned.strip(" -:|,.")
        if len(cleaned) > 18:
            compact = cleaned[:18].rsplit(" ", 1)[0].strip()
            cleaned = compact or cleaned[:18]
        return cleaned

    @staticmethod
    def _is_generic_short_thumbnail_text(text: str) -> bool:
        normalized = ContentGenerator._short_text_key(text)
        generic_phrases = {
            ContentGenerator._short_text_key("물가 왜 다시 오르나"),
            ContentGenerator._short_text_key("이번엔 누가 받나"),
            ContentGenerator._short_text_key("지금 신청 가능"),
            ContentGenerator._short_text_key("지금 해당되는지"),
            ContentGenerator._short_text_key("이번엔 뭐가 달라지나"),
        }
        return normalized in generic_phrases

    @staticmethod
    def _short_text_key(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").casefold()).strip()

    def _news_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "inflation":
            return (
                "Korean grocery aisle or kitchen table with price tags, receipt, shopping basket, and anxious reaction, "
                "consumer-cost pressure, cinematic news lighting"
            )
        if bucket == "rates":
            return (
                "Korean bank-loan or mortgage tension scene with loan papers, smartphone banking alert, calculator, "
                "serious adult reaction, crisp newsroom contrast"
            )
        if bucket == "housing":
            return (
                "Korean apartment skyline or home-loan consultation mood, contract papers, house model, "
                "tense realistic housing-market atmosphere"
            )
        if bucket == "markets":
            return (
                "Korean market-watch scene with exchange-rate or stock movement on phone screen, city night lights, "
                "high-volatility financial mood"
            )
        if bucket == "global":
            return (
                "global-tension news scene with oil, shipping route, or world-map crisis motif, "
                "split-focus confrontation atmosphere and dramatic broadcast lighting"
            )
        if bucket == "policy":
            return (
                "Korean public-policy reaction scene with document, briefing backdrop, urban daily-life setting, "
                "clear change-impact mood"
            )
        return (
            "Korean breaking-news atmosphere with a reacting adult, alert screen glow, dramatic newsroom lighting, "
            "high public-interest issue visual"
        )

    def _welfare_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "application":
            return (
                "Korean resident checking an application page, 주민센터 notice, or deadline alert on smartphone, "
                "urgent but trustworthy life-info mood"
            )
        if bucket == "eligibility":
            return (
                "middle-aged Korean adult reviewing eligibility papers, letter, or welfare criteria checklist at home, "
                "clear practical-life tension"
            )
        if bucket == "payment":
            return (
                "Korean phone payment alert, envelope, or support-message scene with surprised but hopeful reaction, "
                "practical benefit and money timing mood"
            )
        if bucket == "regional":
            return (
                "Korean neighborhood or local government service setting with resident checking region-specific benefit information, "
                "realistic civic-life atmosphere"
            )
        return (
            "middle-aged Korean person or couple reacting to a life-benefit message, warm home or desk setting, "
            "clear eligibility-and-benefit atmosphere"
        )

    @staticmethod
    def _strip_source_like_tokens(text: str) -> str:
        cleaned = text or ""
        cleaned = re.sub(r"https?://\S+|www\.\S+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\[[^\]]*(?:많이 본 경제기사|기사|보도|속보|뉴스|이슈)[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\b(?:연합뉴스TV|연합뉴스|연합인포맥스|매일경제|한국경제|서울경제|머니투데이|조선비즈|네이트|SBS\s*Biz|SBS|YTN|KBS|MBC|JTBC|MSN|KITA(?:\.NET)?|한국무역협회)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" -,:;_|")

    def _short_topic_label(self, topic: RankedTopic) -> str:
        label = self._strip_source_like_tokens(topic.representative_title.split(" - ")[0].strip())
        label = re.sub(r"\([^)]*\)", "", label).strip()
        label = re.sub(r"\[[^\]]+\]", "", label).strip()
        label = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", label)
        label = re.sub(
            r"^[가-힣A-Za-z0-9·&\s]{2,24}(?:부|청|처|원|본부|은행|증권|카드|보험|공사|협회|위원회|당국|정부|우본)\s*[,·:]\s*",
            "",
            label,
        )
        label = re.sub(
            r"^(?:정부|당국|업계|한은|우정사업본부|우본)\s+(?=[가-힣A-Za-z0-9])",
            "",
            label,
        )
        label = re.sub(r"[\"'“”‘’]", "", label)
        label = re.sub(r"\s+", " ", label).strip(" -,:;")
        words = label.split()
        if len(words) > 7:
            label = " ".join(words[:7]).strip()
        if len(label) > 42:
            label = label[:42].rstrip(" .,") + "..."
        return label or self._strip_source_like_tokens(topic.representative_title) or topic.representative_title

    def _resolve_title(self, title: str, topic: RankedTopic) -> str:
        manual_title = self.config.active_channel.manual_title.strip() if self.config.active_channel else ""
        if manual_title:
            return manual_title
        if self._preset().collection_mode == "news":
            return self._build_title(topic)
        candidate = self._strip_source_like_tokens(title or self._build_title(topic))
        candidate = re.sub(r"\s+", " ", candidate).strip(" -,:;_|")
        return candidate or self._build_title(topic)

    def _build_tags(self, topic: RankedTopic, preset_key: str) -> list[str]:
        preset = preset_by_key(preset_key)
        cleaned_title = self._tag_token(self._strip_source_like_tokens(self._short_topic_label(topic)))
        keyword_tags = [self._tag_token(self._strip_source_like_tokens(keyword)) for keyword in topic.keywords[:6]]
        tags = [
            cleaned_title,
            *keyword_tags,
            self._tag_token(self.config.generation.channel_name),
            self._tag_token(preset.label),
            "shorts" if preset.collection_mode != "stories" else "",
            "유튜브자동화",
        ]
        normalized = [self._normalize_tag(tag) for tag in unique_preserve_order(tags)]
        return [tag for tag in normalized if tag][: self.config.generation.max_tags]

    def _sanitize_description_points(self, detail_points: list[str]) -> list[str]:
        preset_key = self._preset().key
        cleaned_points: list[str] = []
        for point in detail_points:
            cleaned = self._sanitize_short_segment(point, preset_key=preset_key)
            if not cleaned or cleaned in cleaned_points:
                continue
            cleaned_points.append(cleaned)
        return cleaned_points[:4]

    def _clean_detail_fact(self, text: str) -> str:
        cleaned = text or ""
        cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
        cleaned = re.sub(r"\([^)]*\)", "", cleaned)
        cleaned = self._strip_source_like_tokens(cleaned)
        cleaned = re.sub(r"[\"'`]", "", cleaned)
        cleaned = re.sub(
            r"(검증에 사용한 핵심 사실 포인트|세부 내용은 공식 발표문/원문 확인 필요|공식 발표문|원문 확인 필요|공식 공고문 확인 필요|공식 사이트에서|공식 사이트|출처|원문 확인)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,:;")
        if len(cleaned) > 96:
            trimmed = cleaned[:96].rsplit(" ", 1)[0].strip() or cleaned[:96]
            cleaned = trimmed.rstrip(" ,") + "..."
        return self._sentenceize_fact(cleaned)

    def _resolve_description(
        self,
        description: str,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
    ) -> str:
        manual_description = self.config.active_channel.manual_description.strip() if self.config.active_channel else ""
        if manual_description:
            return manual_description

        safe_points = self._sanitize_description_points(detail_points)
        if self._preset().collection_mode == "news":
            label = self._short_topic_label(topic)
            if self._preset().key == "welfare_news":
                intro = f"{label} 정보를 대상, 혜택, 신청 포인트 중심으로 이해하기 쉽게 정리했습니다."
            else:
                intro = f"{label} 이슈를 핵심만 이해하기 쉽게 정리했습니다."
            base = self._build_description(
                topic=topic,
                detail_points=safe_points or [label],
                tags=tags,
                summary_intro=intro,
            )
        else:
            base = description or self._build_description(
                topic=topic,
                detail_points=safe_points or detail_points,
                tags=tags,
                summary_intro=f"{self._short_topic_label(topic)} 이슈를 짚고 빠르게 이해할 수 있도록 핵심만 정리했습니다.",
            )
        return self._apply_channel_description_rules(base, detail_points=safe_points or detail_points)

    def _extract_hashtags(self, text: str) -> list[str]:
        tags: list[str] = []
        for raw in re.findall(r"#[^\s#]+", text or ""):
            normalized = self._normalize_tag(self._tag_token(self._strip_source_like_tokens(raw)))
            if normalized and normalized not in tags:
                tags.append(normalized)
        if self._preset().collection_mode != "stories" and "#shorts" not in [tag.lower() for tag in tags]:
            tags.append("#shorts")
        return tags[: self.config.generation.max_tags]

    def _apply_channel_description_rules(self, description: str, *, detail_points: list[str]) -> str:
        text = str(description or "").strip()
        clean_points = self._sanitize_description_points(detail_points)
        hashtags = self._extract_hashtags(text)

        if self._preset().collection_mode == "news":
            label = self._short_topic_label(detail_points[0] if False else None)
            label = self._short_topic_label(self.config.active_channel and topic or topic) if False else ""
            label = self._short_topic_label(self.config.active_channel and detail_points and topic or topic) if False else ""
            label = self._short_topic_label(topic) if False else ""
            label = clean_points[0].rstrip(".") if clean_points else ""
            if self._preset().key == "welfare_news":
                lead = f"{self._short_topic_label(topic)} 정보를 대상, 혜택, 신청 포인트 중심으로 이해하기 쉽게 정리했습니다." if False else ""
            lead = ""
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("[") or line.startswith("#") or line == self.config.generation.channel_name:
                    continue
                cleaned = self._clean_detail_fact(line).rstrip(".")
                if cleaned:
                    lead = cleaned + "."
                    break
            topic_label = ""
            if clean_points:
                topic_label = self._strip_source_like_tokens(clean_points[0].rstrip("."))
            if not topic_label:
                topic_label = self.config.generation.channel_name
            if self._preset().key == "welfare_news":
                lead = f"{topic_label} 정보를 대상, 혜택, 신청 포인트 중심으로 이해하기 쉽게 정리했습니다."
            else:
                lead = f"{topic_label} 이슈를 핵심만 이해하기 쉽게 정리했습니다."

            blocks = [
                self.config.generation.channel_name,
                "",
                lead,
            ]
            if clean_points:
                blocks.extend(["", "[핵심 정리]", *[f"- {point.rstrip('.')}" for point in clean_points[:3]]])
            if self._preset().key == "welfare_news":
                blocks.extend(
                    [
                        "",
                        "[오해하기 쉬운 부분]",
                        "- 지역, 연령, 소득 기준에 따라 실제 대상이 달라질 수 있습니다.",
                        "- 신청 기간과 예산 상황에 따라 지급 여부와 시점이 달라질 수 있습니다.",
                    ]
                )
            blocks.extend(["", self.config.generation.call_to_action])
            if hashtags:
                blocks.extend(["", "[해시태그]", " ".join(hashtags)])
            return "\n".join(blocks).strip()

        return text

    def _apply_channel_description_rules(self, description: str, *, detail_points: list[str]) -> str:
        text = str(description or "").strip()
        clean_points = self._sanitize_description_points(detail_points)
        hashtags = self._extract_hashtags(text)

        if self._preset().collection_mode == "news":
            lead = ""
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if (
                    not line
                    or line.startswith("[")
                    or line.startswith("#")
                    or line == self.config.generation.channel_name
                    or "검증에 사용한 핵심 사실 포인트" in line
                    or "공식 발표문" in line
                    or "원문 확인" in line
                ):
                    continue
                cleaned = self._clean_detail_fact(line).rstrip(".")
                if cleaned:
                    lead = cleaned + "."
                    break

            if not lead:
                if self._preset().key == "welfare_news":
                    lead = "이번 복지 정보를 대상, 혜택, 신청 포인트 중심으로 이해하기 쉽게 정리했습니다."
                else:
                    lead = "이번 경제 뉴스에서 꼭 알아야 할 변화만 쉽게 정리했습니다."

            blocks = [
                self.config.generation.channel_name,
                "",
                lead,
            ]
            if clean_points:
                blocks.extend(["", "[핵심 정리]", *[f"- {point.rstrip('.')}" for point in clean_points[:3]]])
            if self._preset().key == "welfare_news":
                blocks.extend(
                    [
                        "",
                        "[오해하기 쉬운 부분]",
                        "- 지역, 연령, 소득 기준에 따라 실제 대상이 달라질 수 있습니다.",
                        "- 신청 기간과 예산 상황에 따라 지급 여부와 시점이 달라질 수 있습니다.",
                    ]
                )
            if self._preset().key == "welfare_news":
                blocks.extend(["", self.config.generation.call_to_action])
            else:
                blocks.extend(["", "핵심 경제 뉴스만 쉽게 받아보고 싶다면 구독과 좋아요 부탁드립니다."])
            if hashtags:
                blocks.extend(["", "[해시태그]", " ".join(hashtags)])
            return "\n".join(blocks).strip()

        return text

    def _build_story_hook(self, topic: RankedTopic, details: list[TopicDetail]) -> str:
        hook_detail = details[0].summary if details else "가족의 진심과 늦게 찾아온 위로"
        return (
            "황금시간의기록입니다. "
            f"그날 밤, 주인공은 평생 숨겨 온 사실 하나를 듣고 걸음을 멈췄습니다. "
            f"아무도 예상하지 못했던 선택은 {hook_detail}와 맞물려 모든 관계를 뒤흔들기 시작했습니다. "
            f"오늘 이야기는 {topic.representative_title}라는 문장에서 시작하지만, 결국 가장 늦은 후회와 가장 조용한 용서에 도착합니다."
        )

    def _fallback_story_narration(
        self,
        topic: RankedTopic,
        title: str,
        detail: str,
        index: int,
        scene_count: int,
    ) -> str:
        opening = (
            f"{title}. 이 장면에서 주인공은 {topic.representative_title}와 맞닿은 현실 앞에 서게 됩니다. "
            f"오랫동안 참고 견뎌온 시간은 있었지만, 누구에게도 꺼내지 못했던 마음은 점점 더 무거워졌습니다."
        )
        middle = (
            f"주변 사람들은 겉으로 드러난 상황만 보았지만, 실제로는 {detail} 같은 이유가 더 깊은 곳에서 자라고 있었습니다. "
            "식탁 위의 한숨, 병원 복도에 남은 발걸음, 통장 잔고를 계산하던 새벽, 오래된 사진첩을 다시 꺼내 보던 밤이 모두 하나의 사연으로 이어졌습니다. "
            "주인공은 자신의 선택이 이기적인지, 아니면 너무 늦은 자기 보호인지 스스로도 확신하지 못했습니다."
        )
        reflection = (
            "하지만 나이가 들수록 사람을 가장 힘들게 하는 것은 거대한 사건 하나보다도, 누구에게도 말하지 못한 작은 서운함이 오래 쌓이는 일이라는 사실을 그는 이미 알고 있었습니다. "
            "그래서 이번 장면은 단순한 갈등보다 더 조용하고 더 현실적인 무게를 가지게 됩니다. "
            "시청자는 이 대목에서 자신의 부모, 배우자, 혹은 미래의 자신을 떠올리게 됩니다."
        )
        closing = (
            f"이 장면은 전체 {scene_count}개 장면 중 {index}번째에 불과하지만, 이후 반전과 화해를 준비하는 가장 중요한 감정의 밑바탕이 됩니다. "
            "무너진 관계는 한순간에 회복되지 않지만, 어떤 진심은 아주 늦게라도 방향을 바꾸는 힘을 갖고 있다는 사실이 여기서부터 서서히 드러납니다."
        )
        return "\n\n".join([opening, middle, reflection, closing])

    def _story_extension(self, topic: RankedTopic, title: str, detail: str, scene_pointer: int) -> str:
        openings = [
            "그날 이후, 평범하던 일상은 더 이상 같은 모습으로 이어지지 않았습니다.",
            "시간이 조금 흐르자, 모두가 애써 모른 척하던 감정이 조용히 드러나기 시작했습니다.",
            "겉으로는 잠잠해 보였지만, 속에서는 이미 오래된 균열이 더 선명해지고 있었습니다.",
            "누구도 먼저 말을 꺼내지 않았지만, 마음속에서는 같은 질문이 계속 맴돌고 있었습니다.",
            "하루 이틀은 버틸 수 있었지만, 시간이 갈수록 외면하기 어려운 장면들이 쌓여 갔습니다.",
            "그 일을 지나고 나서야, 사람들은 당연하다고 믿던 관계를 다시 돌아보게 됐습니다.",
        ]
        daily_actions = [
            "아침 식탁에 앉는 순서 하나, 전화벨이 울릴 때 서로를 바라보는 눈빛 하나까지 전과는 달라졌습니다.",
            "짧게 건네는 인사에도 망설임이 묻어났고, 사소한 부탁조차 쉽게 꺼내지 못하는 시간이 이어졌습니다.",
            "집 안 공기는 조용했지만, 누구 하나 편하게 숨을 쉬지 못할 만큼 묵직한 긴장이 남아 있었습니다.",
            "늘 하던 일들을 그대로 반복하면서도, 사람들은 마음 한켠에서 같은 상처를 자꾸 떠올렸습니다.",
            "서로 모른 척 일상을 이어가려 했지만, 작은 표정 변화만으로도 감정이 흔들리는 날들이 많아졌습니다.",
        ]
        emotions = [
            "서운함은 쉽게 사라지지 않았고, 미안함은 늦게 찾아왔으며, 사람들은 그 사이에서 말보다 긴 침묵을 견뎌야 했습니다.",
            "누군가는 억울했고, 누군가는 겁이 났고, 또 누군가는 지금이라도 솔직해져야 한다는 마음을 품게 됐습니다.",
            "감정을 숨긴다고 해결되는 일은 아니었고, 오히려 감춘 시간만큼 오해가 더 깊어졌습니다.",
            "겉으로는 담담한 척했지만, 혼자 남는 순간마다 지난 말과 행동이 자꾸 마음을 찔렀습니다.",
            "자존심 때문에 멈춰 있던 관계는 있었지만, 한편으로는 아직 완전히 놓지 못한 마음도 남아 있었습니다.",
        ]
        bridges = [
            f"{detail} 같은 흐름은 단번에 끝나지 않았고, 이후의 선택 하나하나가 관계의 방향을 바꾸기 시작했습니다.",
            "그 일 이후 사람들은 예전처럼 돌아가길 바랐지만, 이미 서로에게 남은 감정은 쉽게 정리되지 않았습니다.",
            "그 순간에는 다들 버티는 것만 생각했지만, 시간이 지나자 무엇을 바로잡아야 하는지가 조금씩 보이기 시작했습니다.",
            "작은 계기 하나가 생각보다 큰 변화를 만들었고, 누구도 예상하지 못한 방향으로 마음이 움직이기 시작했습니다.",
            "결국 중요한 건 누가 먼저 이기느냐가 아니라, 이 관계를 어디까지 지켜낼 마음이 있느냐는 점이었습니다.",
        ]
        title_contexts = {
            "장례식": "마지막이라고 생각했던 순간 뒤에도, 정리되지 않은 말과 감정은 오래 남아 있었습니다.",
            "유언장": "종이 한 장이 전한 뜻보다, 그 말을 받아들이는 사람들의 마음이 더 큰 파문을 만들었습니다.",
            "요양병원": "누군가를 돌보는 시간은 단순한 의무가 아니라, 관계의 진심을 다시 묻는 시간이 되기도 했습니다.",
            "병원": "병실 안에서 오간 짧은 말들은 평소보다 훨씬 깊게 남아 사람들의 마음을 흔들었습니다.",
            "사기": "돈 문제는 숫자로 끝나지 않았고, 믿음과 체면까지 함께 흔드는 일이 되었습니다.",
            "노후 자금": "오랫동안 모아온 삶의 기반이 흔들리자, 가족 각자의 태도도 적나라하게 드러나기 시작했습니다.",
            "재혼": "늦은 나이에 시작한 선택은 축복보다 반대를 먼저 불러왔고, 그만큼 서로의 진심도 시험대에 올랐습니다.",
            "황혼": "나이가 들어 찾아온 감정일수록 가볍게 넘길 수 없었고, 그래서 더 큰 용기가 필요했습니다.",
            "가족": "가족이라는 말은 따뜻하지만, 때로는 가장 깊은 상처를 남기는 이유가 되기도 했습니다.",
            "부부": "오랜 세월을 함께 보냈다고 해서 모든 마음이 저절로 이해되는 것은 아니었습니다.",
        }
        opening = openings[scene_pointer % len(openings)]
        daily = daily_actions[(scene_pointer // 2) % len(daily_actions)]
        emotion = emotions[(scene_pointer // 3) % len(emotions)]
        bridge = bridges[(scene_pointer // 4) % len(bridges)]
        context = next((value for key, value in title_contexts.items() if key in title), f"{title} 이후의 변화는 생각보다 오래 이어졌고, 사람들의 마음에도 깊은 흔적을 남겼습니다.")
        return " ".join([opening, context, daily, emotion, bridge])
    def _short_topic_label(topic: RankedTopic) -> str:
        label = topic.representative_title.split(" - ")[0].strip()
        label = re.sub(r"\([^)]*\)", "", label).strip()
        words = label.split()
        if len(words) > 6:
            label = " ".join(words[:6]).strip()
        if len(label) > 28:
            label = label[:28].rstrip(" .,") + "..."
        return label or topic.representative_title

    @staticmethod
    def _strip_source_like_tokens(text: str) -> str:
        cleaned = text or ""
        cleaned = re.sub(r"https?://\S+|www\.\S+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bv[\s._-]*daum[\s._-]*net\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bdaum[\s._-]*net\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bv[\s._-]*naver[\s._-]*com\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bmk[\s._-]*co[\s._-]*kr\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bnate(?:\.com)?\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\s*-\s*(?:연합뉴스|뉴시스|뉴스1|뉴스핌|드림투데이|매일경제|한국경제|서울경제|아시아경제|이데일리|머니투데이|조선비즈|중앙일보|한겨레|경향신문|국민일보|문화일보|파이낸셜뉴스|헤럴드경제|SBS\s*Biz|SBS|KBS|MBC|JTBC|YTN|MSN|네이트)\b.*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:연합뉴스|뉴시스|뉴스1|뉴스핌|드림투데이|매일경제|한국경제|서울경제|아시아경제|이데일리|머니투데이|조선비즈|중앙일보|한겨레|경향신문|국민일보|문화일보|파이낸셜뉴스|헤럴드경제|SBS\s*Biz|SBS|KBS|MBC|JTBC|YTN|MSN|네이트)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\[[^\]]*(?:기사|보도|속보|뉴스)[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" -,:;_|")

    def _build_news_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        summaries = self._detail_headlines(details, minimum=4, fallback=topic.representative_title)
        label = self._short_topic_label(topic)
        segments = [
            f"지금 이 뉴스, 내 생활에 영향 있습니다. {label} 핵심만 보겠습니다.",
            f"무슨 일이냐면 {summaries[0]}",
            f"왜 중요하냐면 {summaries[1]}",
            f"영향을 받을 수 있는 쪽은 {summaries[2]}",
            f"지금은 {summaries[3]} 공식 발표와 원문을 꼭 확인하세요.",
        ]
        tags = self._build_tags(topic, self._preset().key)
        detail_points = [item.title for item in details[:5]] or [topic.representative_title]
        description = self._apply_channel_description_rules(
            self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{topic.representative_title} 이슈를 30~50초 안에 이해할 수 있도록 핵심만 다시 정리했습니다.",
            ),
            detail_points=detail_points,
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="자체 대본, 자체 나레이션, 자체 카드뉴스형 비주얼 구조로 재구성한 뉴스 쇼츠입니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
        )

    def _build_welfare_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        summaries = self._detail_headlines(details, minimum=3, fallback=topic.representative_title)
        label = self._short_topic_label(topic)
        segments = [
            "이거 해당되면 바로 챙겨야 합니다.",
            f"{label} 대상은 {summaries[0]}",
            f"혜택은 {summaries[1]}",
            f"신청이나 확인은 {summaries[2]}",
            "세부 요건과 마감은 공식 공고문을 꼭 확인하세요.",
        ]
        tags = self._build_tags(topic, self._preset().key)
        detail_points = [item.title for item in details[:5]] or [topic.representative_title]
        description = self._apply_channel_description_rules(
            self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{topic.representative_title} 정보를 놓치지 않도록 대상, 혜택, 신청 포인트만 짧게 정리했습니다.",
            ),
            detail_points=detail_points,
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="공식 정보를 바탕으로 자체 카드뉴스형 문구와 내레이션을 구성했습니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
        )

    def _build_quotes_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        quote = topic.representative_title
        detail_points = [item.summary for item in details[:4]] or [quote]
        reality_line = detail_points[0] if detail_points else "지금 마음을 바로 붙잡아 주는 문장입니다."
        action_line = detail_points[1] if len(detail_points) > 1 else "감정이 흔들릴수록 말보다 태도를 먼저 지키는 게 중요합니다."
        segments = [
            f"{quote}",
            f"이 말이 중요한 이유는 {reality_line}",
            f"현실에서는 {action_line}",
            "오늘 이 문장이 마음에 남았다면 저장해 두고, 필요한 사람에게 조용히 보내 보세요.",
        ]
        tags = self._build_tags(topic, self._preset().key)
        description = self._build_description(
            topic=topic,
            detail_points=detail_points,
            tags=tags,
            summary_intro=f"{quote}을 오늘의 감정과 현실에 맞게 짧고 깊게 풀어낸 쇼츠입니다.",
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="짧은 공감형 통찰 문장을 자체 제작 내레이션과 감성 비주얼로 재구성했습니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
        )

    def _resolve_description(
        self,
        description: str,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
    ) -> str:
        manual_description = self.config.active_channel.manual_description.strip() if self.config.active_channel else ""
        if manual_description:
            return manual_description
        base = description or self._build_description(
            topic=topic,
            detail_points=detail_points,
            tags=tags,
            summary_intro=f"{topic.representative_title} 이슈를 짧고 빠르게 이해할 수 있도록 핵심만 정리했습니다.",
        )
        return self._apply_channel_description_rules(base, detail_points=detail_points)

    def _supplementary_segment(
        self,
        *,
        next_index: int,
        content: GeneratedContent,
        detail_summaries: list[str],
        keywords: str,
    ) -> str:
        preset_key = self._preset().key
        if preset_key == "quotes_daily":
            fallbacks = [
                "지금 이 말이 마음에 걸린다면, 아마 지금 내 상황에도 닿아 있다는 뜻입니다.",
                "짧은 한 문장이지만, 오늘의 태도를 바꾸기엔 충분할 수 있습니다.",
                "이 문장은 조용하지만 오래 남는 힘이 있습니다.",
            ]
        elif preset_key == "welfare_news":
            fallbacks = [
                "금액보다 더 중요한 건 내가 대상인지와 신청 기간이 아직 열려 있는지입니다.",
                "지역, 연령, 소득 기준은 제도마다 다르니 마지막 확인이 꼭 필요합니다.",
                "놓치기 쉬운 혜택일수록 공식 사이트에서 대상 여부를 먼저 확인해 보세요.",
            ]
        else:
            fallbacks = [
                f"핵심은 숫자보다 실제 영향입니다. {keywords} 관련 후속 발표까지 같이 보는 게 중요합니다.",
                "속보 제목만 보고 단정하기보다, 발표 내용과 실제 적용 시점을 함께 확인해 보세요.",
                "반응이 큰 이슈일수록 공식 발표와 후속 정정 여부를 같이 보는 게 안전합니다.",
            ]
        if detail_summaries:
            detail = detail_summaries[next_index % len(detail_summaries)].strip()
            if preset_key == "quotes_daily":
                return f"현실로 가져오면 {detail}"
            if preset_key == "welfare_news":
                return f"추가로 확인할 부분은 {detail}"
            return f"지금 같이 봐야 할 포인트는 {detail}"
        return fallbacks[next_index % len(fallbacks)]

    @staticmethod
    def _detail_summaries(details: list[TopicDetail], *, minimum: int, fallback: str) -> list[str]:
        values = [item.summary.strip() for item in details if item.summary.strip()]
        while len(values) < minimum:
            values.append(f"{fallback} 관련 핵심 내용은 공식 발표와 후속 안내를 함께 확인해야 합니다.")
        return values[:minimum]

    @staticmethod
    def _detail_headlines(details: list[TopicDetail], *, minimum: int, fallback: str) -> list[str]:
        values: list[str] = []
        for item in details:
            headline = item.title.split(" - ")[0].strip()
            headline = re.sub(r"^\[[^\]]+\]", "", headline).strip()
            headline = re.sub(r"\([^)]*\)", "", headline).strip()
            headline = re.sub(r"\s+", " ", headline)
            if len(headline) > 30:
                headline = headline[:30].rstrip(" .,") + "..."
            if headline:
                values.append(headline)
        fallback_label = re.sub(r"\s+", " ", fallback.split(" - ")[0]).strip() or fallback
        if len(fallback_label) > 30:
            fallback_label = fallback_label[:30].rstrip(" .,") + "..."
        while len(values) < minimum:
            values.append(fallback_label)
        return values[:minimum]

    def _apply_channel_description_rules(self, description: str, *, detail_points: list[str]) -> str:
        text = description.strip()
        preset_key = self._preset().key

        if preset_key == "welfare_news":
            blocks = [text]
            if "[핵심 확인 포인트]" not in text:
                blocks.extend(["", "[핵심 확인 포인트]", *[f"- {point}" for point in detail_points[:3]]])
            if "[오해하기 쉬운 부분]" not in text:
                blocks.extend(
                    [
                        "",
                        "[오해하기 쉬운 부분]",
                        "- 지역, 연령, 소득 기준에 따라 실제 대상이 달라질 수 있습니다.",
                        "- 신청 기간과 예산 소진 여부는 수시로 바뀔 수 있습니다.",
                    ]
                )
            if "세부 요건은 공식 공고문 확인 필요" not in text:
                blocks.extend(["", "세부 요건은 공식 공고문 확인 필요"])
            return "\n".join(blocks)

        if self._preset().collection_mode == "news":
            blocks = [text]
            if "[검증에 사용한 핵심 사실 포인트]" not in text:
                blocks.extend(["", "[검증에 사용한 핵심 사실 포인트]", *[f"- {point}" for point in detail_points[:3]]])
            notice = "주의: 이 영상은 정보 요약이며, 세부 내용은 공식 발표문/원문 확인 필요"
            if notice not in text:
                blocks.extend(["", notice])
            return "\n".join(blocks)

        return text

    def _fallback_short_content_for_preset(
        self,
        topic: RankedTopic,
        details: list[TopicDetail],
        *,
        preset_key: str,
    ) -> GeneratedContent:
        if preset_key == "welfare_news":
            return self._build_welfare_content(topic, details)
        if preset_key == "quotes_daily":
            return self._build_quotes_content(topic, details)
        if self._preset().collection_mode == "poems":
            return self._build_poem_content(topic, details)
        return self._build_news_content(topic, details)

    def _finalize_short_content(self, content: GeneratedContent, details: list[TopicDetail]) -> GeneratedContent:
        target_seconds = max(24, int(self.config.generation.target_duration_seconds * 0.8))
        preset_key = self._preset().key
        if self._preset().collection_mode == "news" and preset_key != "welfare_news":
            news_content = self._build_news_content(content.topic, details)
            description_segments = self._news_segments_from_description(content.description)
            if len(description_segments) >= 4:
                news_content.segments = description_segments
                news_content.script = " ".join(description_segments)
                news_content.estimated_duration_seconds = max(
                    news_content.estimated_duration_seconds,
                    self._estimate_seconds(description_segments),
                )
            content.segments = news_content.segments
            content.script = news_content.script
            content.detail_points = news_content.detail_points
            content.description = news_content.description
            content.estimated_duration_seconds = max(
                content.estimated_duration_seconds,
                news_content.estimated_duration_seconds,
            )
            return content

        segments = [
            cleaned
            for cleaned in (
                self._sanitize_short_segment(segment, preset_key=preset_key)
                for segment in (content.segments or [content.script])
            )
            if cleaned
        ]

        if len(segments) < 4:
            rebuilt = self._fallback_short_content_for_preset(content.topic, details, preset_key=preset_key)
            content = rebuilt
            segments = [
                cleaned
                for cleaned in (
                    self._sanitize_short_segment(segment, preset_key=preset_key)
                    for segment in rebuilt.segments
                )
                if cleaned
            ]

        detail_summaries = [
            fact
            for fact in (self._clean_detail_fact(item.summary or item.title) for item in details)
            if fact
        ]
        keywords = ", ".join(content.topic.keywords[:4] or [content.topic.representative_title])

        while self._estimate_seconds(segments) < target_seconds:
            extra = self._sanitize_short_segment(
                self._supplementary_segment(
                    next_index=len(segments),
                    content=content,
                    detail_summaries=detail_summaries,
                    keywords=keywords,
                ),
                preset_key=preset_key,
            )
            if not extra or extra in segments:
                break
            segments.append(extra)
            if len(segments) >= 10:
                break

        content.segments = segments
        content.script = " ".join(segments)
        content.estimated_duration_seconds = max(content.estimated_duration_seconds, self._estimate_seconds(segments))
        return content

    def _build_story_fallback(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        scene_count = max(1, int(self.config.generation.story_scene_count))
        hook_script = self._build_story_hook(topic, details)
        detail_texts = self._story_detail_fragments(details, topic, minimum=scene_count)
        scene_titles = [
            "평범했던 저녁",
            "숨기고 있던 균열",
            "한마디가 남긴 상처",
            "돌이킬 수 없는 선택",
            "늦게 도착한 진심",
            "다시 마주한 가족",
            "조용한 용서",
        ][:scene_count]
        scenes: list[StoryScene] = []
        for index, title in enumerate(scene_titles, start=1):
            detail = detail_texts[(index - 1) % len(detail_texts)]
            narration = self._fallback_story_narration(topic, title, detail, index, scene_count)
            scenes.append(
                StoryScene(
                    index=index,
                    title=title,
                    summary=detail,
                    narration=narration,
                    image_prompt=self._story_image_prompt(topic, title, detail),
                )
            )

        tags = self._build_tags(topic, self._preset().key)
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script="",
            description=self._build_story_description(
                topic=topic,
                detail_points=[scene.summary for scene in scenes[:5]],
                tags=tags,
            ),
            tags=tags,
            segments=[],
            content_format="longform_story",
            detail_points=[scene.summary for scene in scenes[:5]],
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=True,
            altered_content_reason="시니어 인생사연 롱폼 구성과 AI 이미지 연출을 함께 사용합니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
            hook_title="처음 30초의 충격",
            hook_script=hook_script,
            hook_image_prompt=self._story_image_prompt(topic, "hook", hook_script[:180]),
            scenes=scenes,
        )

    def _finalize_story_content(self, content: GeneratedContent, details: list[TopicDetail]) -> GeneratedContent:
        target_seconds = max(1200, int(self.config.generation.target_duration_seconds))
        hook_script = self._sanitize_story_text(content.hook_script.strip() or self._build_story_hook(content.topic, details))
        scenes = list(content.scenes)
        if not scenes:
            return self._build_story_fallback(content.topic, details)

        scenes = self._normalize_story_scenes(content.topic, scenes)
        hook_script, scenes, seen_blocks = self._dedupe_story_scenes(hook_script, content.topic, scenes)

        if self._story_needs_refresh(hook_script, scenes, target_seconds):
            expanded_payload = self.ai.expand_story_package(
                topic=content.topic,
                details=details,
                hook_script=hook_script,
                scenes=scenes,
                current_thumbnail_text=content.thumbnail_text,
            )
            if expanded_payload:
                expanded_content = self._story_from_ai_payload(content.topic, expanded_payload, details)
                if expanded_content is not None:
                    content = expanded_content
                    hook_script = self._sanitize_story_text(
                        expanded_content.hook_script.strip() or self._build_story_hook(content.topic, details)
                    )
                    scenes = self._normalize_story_scenes(content.topic, list(expanded_content.scenes))
                    hook_script, scenes, seen_blocks = self._dedupe_story_scenes(hook_script, content.topic, scenes)

        min_target = int(target_seconds * 0.92)
        detail_cycle = self._story_detail_fragments(details, content.topic, minimum=max(4, len(scenes)))
        scene_pointer = 0
        while self._estimate_story_seconds(hook_script, scenes) < min_target:
            detail = detail_cycle[scene_pointer % len(detail_cycle)]
            scene = scenes[scene_pointer % len(scenes)]
            extension = self._sanitize_story_text(self._story_extension(content.topic, scene.title, detail, scene_pointer))
            signature = self._story_signature(extension)
            if extension and signature and signature not in seen_blocks and extension not in scene.narration:
                scene.narration = f"{scene.narration}\n\n{extension}".strip()
                seen_blocks.add(signature)
            scene_pointer += 1
            if scene_pointer > len(scenes) * 12:
                break

        hook_duration = self._resolve_story_hook_duration(hook_script)
        scene_weights = [max(1, self._estimate_story_segment_seconds(scene.narration)) for scene in scenes]
        remaining = max(1, target_seconds - hook_duration)
        weight_total = sum(scene_weights) or len(scene_weights)
        for scene, weight in zip(scenes, scene_weights):
            scene.duration_seconds = max(120, int(remaining * (weight / weight_total)))

        content.hook_script = hook_script
        content.hook_duration_seconds = hook_duration
        content.segments = [hook_script, *[scene.narration for scene in scenes]]
        content.script = "\n\n".join(content.segments)
        content.scenes = scenes
        content.detail_points = [scene.summary for scene in scenes[:5]]
        content.estimated_duration_seconds = hook_duration + sum(scene.duration_seconds for scene in scenes)
        content.content_format = "longform_story"
        content.thumbnail_text = self._resolve_thumbnail_text(content.thumbnail_text, content.topic)
        content.thumbnail_prompt = self._resolve_thumbnail_prompt(content.thumbnail_prompt, content.topic)
        return content

    def _normalize_story_scenes(self, topic: RankedTopic, scenes: list[StoryScene]) -> list[StoryScene]:
        scene_count = max(1, len(scenes))
        normalized: list[StoryScene] = []
        for raw_scene in scenes[: self.config.generation.story_scene_count]:
            summary = self._sanitize_story_text(raw_scene.summary or raw_scene.title) or raw_scene.title
            fallback_narration = self._sanitize_story_text(
                self._fallback_story_narration(topic, raw_scene.title, summary, raw_scene.index, scene_count)
            )
            narration = self._sanitize_story_text(raw_scene.narration or summary or raw_scene.title) or fallback_narration
            normalized.append(
                StoryScene(
                    index=raw_scene.index,
                    title=raw_scene.title,
                    summary=summary,
                    narration=narration,
                    image_prompt=self._sanitize_story_image_prompt(
                        raw_scene.image_prompt or self._story_image_prompt(topic, raw_scene.title, summary)
                    ),
                    duration_seconds=int(raw_scene.duration_seconds or 0),
                    visual_hint=raw_scene.visual_hint,
                )
            )
        return normalized

    def _dedupe_story_scenes(
        self,
        hook_script: str,
        topic: RankedTopic,
        scenes: list[StoryScene],
    ) -> tuple[str, list[StoryScene], set[str]]:
        seen_blocks = {signature for signature in self._story_signatures(hook_script) if signature}
        deduped_scenes: list[StoryScene] = []
        scene_count = max(1, len(scenes))

        for scene in scenes:
            fallback_text = self._sanitize_story_text(
                self._fallback_story_narration(topic, scene.title, scene.summary, scene.index, scene_count)
            )
            chosen_blocks: list[str] = []
            for source_text in (scene.narration, fallback_text):
                for block in self._story_blocks(source_text):
                    signature = self._story_signature(block)
                    if not signature or signature in seen_blocks:
                        continue
                    chosen_blocks.append(block)
                    seen_blocks.add(signature)
                if chosen_blocks:
                    break

            if not chosen_blocks:
                fallback_blocks = self._story_blocks(fallback_text)
                if fallback_blocks:
                    chosen_blocks = fallback_blocks[:1]
                    signature = self._story_signature(chosen_blocks[0])
                    if signature:
                        seen_blocks.add(signature)

            scene.narration = "\n\n".join(chosen_blocks).strip() or fallback_text
            deduped_scenes.append(scene)

        return hook_script, deduped_scenes, seen_blocks

    def _story_needs_refresh(self, hook_script: str, scenes: list[StoryScene], target_seconds: int) -> bool:
        estimated = self._estimate_story_seconds(hook_script, scenes)
        if estimated < int(target_seconds * 0.92):
            return True
        if self._story_has_repetition(hook_script, scenes):
            return True
        return any(len(re.sub(r"\s+", "", scene.narration or "")) < 1400 for scene in scenes)

    def _story_has_repetition(self, hook_script: str, scenes: list[StoryScene]) -> bool:
        seen = {signature for signature in self._story_signatures(hook_script) if signature}
        duplicate_count = 0
        block_count = 0
        for scene in scenes:
            blocks = self._story_blocks(scene.narration)
            if len(blocks) < 3:
                return True
            for signature in self._story_signatures(scene.narration):
                if not signature:
                    continue
                block_count += 1
                if signature in seen:
                    duplicate_count += 1
                else:
                    seen.add(signature)
        return duplicate_count > 0 or block_count < max(18, len(scenes) * 3)

    @staticmethod
    def _story_blocks(text: str) -> list[str]:
        return [block.strip() for block in re.split(r"\n{2,}", (text or "").strip()) if block.strip()]

    def _story_signatures(self, text: str) -> list[str]:
        return [signature for signature in (self._story_signature(block) for block in self._story_blocks(text)) if signature]

    @staticmethod
    def _story_signature(text: str) -> str:
        return re.sub(r"[^\w]+", "", (text or "").lower())[:160]

    def _build_story_hook(self, topic: RankedTopic, details: list[TopicDetail]) -> str:
        fact = self._story_detail_fragments(details, topic, minimum=1)[0].rstrip(".")
        return (
            "황금시간의기록입니다. "
            "그날 저녁, 어머니가 끝내 숨기지 않겠다고 말했을 때 식탁 위 공기는 순식간에 식어 버렸습니다. "
            f"{fact} "
            "아무도 먼저 말을 잇지 못했고, 늦게 꺼낸 진심 하나가 오래 눌러 둔 상처를 흔들기 시작했습니다. "
            f"오늘 이야기는 {topic.representative_title}에서 출발해, 멀어진 마음이 다시 가까워질 수 있는지 차분히 따라가 보겠습니다."
        )

    def _fallback_story_narration(
        self,
        topic: RankedTopic,
        title: str,
        detail: str,
        index: int,
        scene_count: int,
    ) -> str:
        openings = [
            f"{title}. 그날 저녁, 집으로 돌아오는 발걸음은 평소보다 훨씬 무거웠습니다. {detail} 하루아침에 생긴 일은 아니었고, 오래 눌러 둔 마음이 그날에서야 소리를 내기 시작했습니다.",
            f"{title}. 다음 날 아침, 평소처럼 밥상을 차려도 집 안의 공기는 전날과 전혀 다르지 않았습니다. {detail} 아무 말 없이 수저만 놓는 손끝에서 이미 흔들린 마음이 드러났습니다.",
            f"{title}. 낮이 되자 참아 두었던 말들이 하나둘 밖으로 나오기 시작했습니다. {detail} 애써 넘기려던 오해가 그 순간에는 더는 숨길 수 없는 문제처럼 커졌습니다.",
            f"{title}. 서랍 깊숙이 넣어 둔 오래된 사진과 봉투가 다시 꺼내진 건 바로 그날 밤이었습니다. {detail} 지나간 시간은 끝난 줄 알았지만, 정작 마음은 아직 그 자리에 머물러 있었습니다.",
            f"{title}. 누군가는 늦었다고 말했고, 누군가는 이제라도 솔직해야 한다고 말했습니다. {detail} 서로 다른 체면과 상처가 한집 안에서 정면으로 부딪히기 시작했습니다.",
            f"{title}. 멀어진 사람끼리 다시 마주 앉는 일은 생각보다 더 어렵고 더 조심스러웠습니다. {detail} 그래도 누구 한 사람 먼저 진심을 꺼내지 않으면 이 집의 시간은 더 앞으로 나갈 수 없었습니다.",
            f"{title}. 결국 남은 것은 누가 더 옳았는지가 아니라, 누가 더 오래 아파했는지에 대한 조용한 깨달음이었습니다. {detail} 그제야 모두가 같은 상처를 다른 말로 견뎌 왔다는 사실이 보이기 시작했습니다.",
        ]
        middles = [
            "식탁에는 따뜻한 국이 올라와 있었지만, 누구도 먼저 숟가락을 들지 못했습니다. 겉으로는 평소와 다를 것 없어 보여도, 한숨 한 번과 시선 한 번마다 서운함이 더 또렷하게 남았습니다.",
            "현관에 벗어 둔 운동화와 반쯤 접힌 우산, 싱크대 옆 약봉지와 밀린 고지서가 그 집의 현실을 조용히 증명하고 있었습니다. 사소한 생활의 흔적들이 오히려 큰 갈등보다 더 아프게 다가왔습니다.",
            "말을 아끼면 덜 다칠 줄 알았지만, 그 침묵은 오히려 각자의 상상을 키웠습니다. 아무렇지 않은 척 건넨 짧은 대답 하나도 그날은 쉽게 흘려보낼 수가 없었습니다.",
            "가까운 사이일수록 설명하지 않아도 알 거라고 믿었지만 바로 그 믿음이 서운함을 키웠습니다. 마음을 다 말하지 못한 사람일수록 속으로 더 오래 무너지고 있었습니다.",
        ]
        reflections = [
            "누구를 먼저 탓해야 할지 정하기도 어려웠습니다. 남을 원망하면 마음이 편해질 것 같다가도, 지나온 시간을 돌아보면 미안함이 더 크게 밀려왔기 때문입니다.",
            "그 집에서 가장 힘들었던 사람은 큰소리를 낸 사람이 아니라 오래 참은 사람이었는지도 몰랐습니다. 그래서 더 늦기 전에 누군가는 멈춰 서서 다른 마음을 들여다봐야 했습니다.",
            "사람은 나이가 들수록 거창한 사건보다도 오래 쌓인 작은 서운함에 더 깊이 다친다는 걸 모두가 조금씩 깨닫고 있었습니다. 이번 갈등도 결국은 오래 미뤄 둔 말 한마디에서 시작된 일이었습니다.",
            "자존심을 지키려던 마음과 가족을 놓치고 싶지 않은 마음이 같은 자리에서 부딪히고 있었습니다. 그 둘 사이에서 누구도 쉽게 먼저 손을 내밀지 못했습니다.",
        ]
        closings = [
            "그날 이후 같은 집 안에서도 마음의 거리가 분명해졌습니다. 그리고 그 거리를 다시 줄이기까지, 생각보다 긴 시간이 필요하다는 사실도 천천히 드러나기 시작했습니다.",
            "그 순간부터 관계는 예전으로 돌아갈 수 없을 것처럼 보였습니다. 하지만 바로 그래서, 아주 작은 진심 하나가 나중에는 더 크게 마음을 움직일 수 있는 준비가 되기 시작했습니다.",
            "누군가는 돌아섰고, 누군가는 끝내 뒷모습만 바라봤습니다. 그렇지만 그 침묵이 끝이라고 단정하기엔 아직 남아 있는 마음이 너무 많았습니다.",
            "그날 밤은 끝났지만 감정은 끝나지 않았습니다. 오히려 그 밤 이후에야 서로가 놓치고 있던 진짜 사정이 조금씩 모습을 드러내기 시작했습니다.",
        ]
        opening = openings[(index - 1) % len(openings)]
        middle = middles[(index - 1) % len(middles)]
        reflection = reflections[(index - 1) % len(reflections)]
        closing = closings[(index - 1) % len(closings)]
        return "\n\n".join([opening, middle, reflection, closing])

    def _story_extension(self, topic: RankedTopic, title: str, detail: str, scene_pointer: int) -> str:
        openings = [
            "?? ?, ??? ?? ?? ?? ?? ? ?? ??? ?????.",
            "??? ????? ? ?? ??? ??? ??? ???? ?????.",
            "???? ??? ?? ? ?? ???, ?????? ?? ?? ?? ???? ?????.",
            "?? ???? ??? ?, ? ?? ??? ??? ?? ??? ???? ??????.",
            "?? ?? ?? ?? ??? ????, ??? ? ?? ??? ?? ???????.",
            "???? ?? ??? ??? ????? ?? ???? ?????.",
        ]
        daily_actions = [
            "??? ??? ??? ???, ??? ???? ?? ???? ??? ?? ?? ??? ??? ??????.",
            "??? ??? ??? ???? ??? ????, ??? ???? ??? ?? ??? ?????.",
            "???? ??? ?? ?????, ???? ??? ?? ?? ??? ?? ??????.",
            "? ??? ??? ??? ??? ?? ??? ?? ?? ? ???? ??? ??? ???? ??????.",
            "?? ? ?? ???? ??? ??? ?? ??? ? ??? ??? ? ? ??? ???????.",
        ]
        emotions = [
            "???? ??? ??, ????? ??? ??? ??, ?? ??? ?? ??? ???? ???? ?????.",
            "?? ??? ?? ???? ????? ??? ? ?? ????? ??? ??? ??????.",
            "??? ???? ??? ??? ????? ??? ?? ?? ???? ??? ?????.",
            "????? ?? ?? ?? ? ???? ??? ? ?? ???? ?? ?????.",
            "??? ??? ?????, ?? ?? ?? ??? ?? ???? ???? ?? ???????.",
        ]
        bridges = [
            f"{detail} ??? ??? ??? ??? ???? ???, ??? ??? ?? ???? ???? ??????.",
            "??? ??? ???? ??? ????, ?? ?? ??? ? ?? ?? ??? ??? ??? ?????.",
            "?? ?? ???? ?? ?? ?? ???? ????, ??? ? ??? ???? ?? ?? ??????.",
            "??? ??? ??? ?? ?????, ??? ??? ??? ??? ???? ??? ???????.",
            "?? ??? ???? ? ? ??? ???, ???? ??? ????? ??? ??? ?????.",
        ]
        title_contexts = {
            "???? ??": "??? ?? ?? ?? ????, ??? ??? ?? ?? ??? ?????.",
            "???? ??": "??? ?? ?? ?? ????, ??? ??? ?? ?? ??? ?????.",
            "??? ?? ??": "??? ????? ?? ??? ?? ???? ? ?? ?? ?????.",
            "?? ? ???": "??? ????? ?? ??? ?? ???? ? ?? ?? ?????.",
            "???? ?? ??": "??? ??? ?? ???, ??? ?? ?? ?? ??? ?? ??? ??????.",
            "??? ?? ??": "??? ??? ?? ???, ??? ?? ?? ?? ??? ?? ??? ??????.",
            "??? ? ?? ??": "?????? ??????, ?? ?????? ?? ?? ?? ? ???????.",
            "?? ??? ??": "?? ??? ?? ???? ??? ??? ?? ???? ??? ? ?? ??????.",
            "?? ??? ??": "?? ??? ?? ???? ??? ??? ?? ???? ??? ? ?? ??????.",
            "?? ??? ??": "???? ?? ??? ?? ?? ????? ?? ??? ??????.",
            "??? ??": "? ?? ??? ??? ?? ? ??, ??? ? ???? ? ?? ????.",
            "??? ??": "? ?? ??? ??? ?? ? ??, ??? ? ???? ? ?? ????.",
        }
        opening = openings[scene_pointer % len(openings)]
        daily = daily_actions[(scene_pointer // 2) % len(daily_actions)]
        emotion = emotions[(scene_pointer // 3) % len(emotions)]
        bridge = bridges[(scene_pointer // 4) % len(bridges)]
        context = title_contexts.get(title, f"{title}?? ?? ??? ???? ?? ? ??? ?? ?????.")
        return " ".join([opening, context, daily, emotion, bridge])

    def _short_topic_label(self, topic: RankedTopic) -> str:
        raw_title = topic.representative_title.strip()
        context_label = ""
        bracket_match = re.match(r"^\[([^\]]+)\]\s*(.+)$", raw_title)
        if bracket_match:
            raw_context = bracket_match.group(1).strip()
            raw_title = bracket_match.group(2).strip()
            context_map = {
                "亞증시-종합": "아시아 증시",
                "亞증시": "아시아 증시",
                "뉴욕증시-종합": "뉴욕 증시",
                "뉴욕증시": "뉴욕 증시",
                "코스피": "코스피",
                "코스닥": "코스닥",
                "환율-마감": "환율 마감",
                "채권-마감": "채권 마감",
            }
            context_label = context_map.get(raw_context, raw_context)

        label = raw_title.split(" - ")[0].strip()
        label = self._strip_source_like_tokens(label)
        label = re.sub(r"\([^)]*\)", "", label).strip()
        label = re.sub(r"\[[^\]]+\]", "", label).strip()
        label = re.sub(r"https?://\S+|www\.\S+", "", label, flags=re.IGNORECASE)
        label = re.sub(r"\b[a-z0-9-]+\.(?:com|net|kr)\b", "", label, flags=re.IGNORECASE)
        label = self._strip_source_like_tokens(label)
        label = re.sub(
            r"(연합뉴스|뉴시스|뉴스1|YTN|KBS|MBC|SBS|JTBC|한국경제|매일경제|머니투데이|조선비즈|서울경제|MSN|SBS Biz)\b",
            "",
            label,
            flags=re.IGNORECASE,
        )
        label = re.sub(r"\[[^\]]*(뉴스|속보|단독|기사|많이 본|경제기사)[^\]]*\]", "", label, flags=re.IGNORECASE)
        label = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", label)
        label = re.sub(
            r"^[가-힣A-Za-z0-9·&\s]{2,24}(?:부|청|처|원|본부|은행|증권|카드|보험|공사|협회|위원회|당국|정부|우본)\s*[,·:]\s*",
            "",
            label,
        )
        label = re.sub(
            r"^(?:정부|당국|업계|한은|우정사업본부|우본)\s+(?=[가-힣A-Za-z0-9])",
            "",
            label,
        )
        label = re.sub(r"[\"'“”‘’]", "", label)
        label = re.sub(r"\s+", " ", label).strip(" -,:;")
        if context_label:
            context_label = re.sub(r"\s+", " ", context_label).strip(" -,:;")
            if context_label and context_label not in label:
                label = f"{context_label} {label}".strip()
        words = label.split()
        if len(words) > 7:
            label = " ".join(words[:7]).strip()
        if len(label) > 42:
            label = label[:42].rstrip(" .,") + "..."
        return label or topic.representative_title

    def _build_news_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        facts = self._detail_headlines(details, minimum=4, fallback=topic.representative_title)
        label = self._short_topic_label(topic)
        closing = facts[3]
        if any(token in closing for token in ("출처", "브리핑", "검색", "분류", "공식", "원문")):
            closing = "세부 기준과 후속 발표 내용은 곧 더 분명해질 가능성이 있습니다."

        segments = [
            f"{label} 관련 소식이 나왔습니다.",
            facts[0],
            f"이번 이슈가 커진 이유는 {facts[1]}",
            facts[2],
            closing,
        ]
        tags = self._build_tags(topic, self._preset().key)
        detail_points = facts[:5]
        description = self._apply_channel_description_rules(
            self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{label} 이슈의 핵심 내용과 쟁점을 짧게 정리했습니다.",
            ),
            detail_points=detail_points,
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="?먯껜 ?蹂? ?먯껜 ?섎젅?댁뀡, ?먯껜 移대뱶?댁뒪??鍮꾩＜??援ъ“濡??ш뎄?깊븳 ?댁뒪 ?쇱툩?낅땲??",
            thumbnail_text=self._resolve_short_thumbnail_text(topic, detail_points),
        )

    def _build_welfare_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        facts = self._detail_headlines(details, minimum=4, fallback=topic.representative_title)
        label = self._short_topic_label(topic)
        target_fact = self._pick_welfare_fact(facts, ("대상", "연령", "소득", "가구", "누가"))
        benefit_fact = self._pick_welfare_fact(facts, ("지급", "지원", "금액", "혜택"))
        application_fact = self._pick_welfare_fact(facts, ("신청", "접수", "복지로", "주민센터", "행정복지센터", "홈페이지"))
        timing_fact = self._pick_welfare_fact(facts, ("마감", "시행", "기한", "기간", "예산", "추경"))

        if target_fact and benefit_fact and application_fact:
            segments = [
                f"{label} 혜택은 조건이 맞으면 꼭 확인할 만합니다.",
                f"대상은 {target_fact}",
                f"혜택 내용은 {benefit_fact}",
                f"신청하거나 확인할 곳은 {application_fact}",
                f"마감이나 예외 조건은 {timing_fact or '세부 공고가 나오면 함께 확인해야 합니다.'}",
            ]
            detail_points = [target_fact, benefit_fact, application_fact, timing_fact or "세부 공고가 나오면 함께 확인해야 합니다."]
        else:
            segments = [
                f"{label} 관련 논의가 다시 나오고 있습니다.",
                "다만 대상과 지급 방식은 아직 공식 발표를 끝까지 확인해야 합니다.",
                "지급 금액과 시행 시기는 추경안이나 세부 공고가 확정돼야 분명해집니다.",
                "신청 창구가 열리면 정부나 지자체 공식 사이트에서 대상 여부를 먼저 확인해야 합니다.",
                "세부 요건은 공식 공고문 확인 필요",
            ]
            detail_points = [
                f"{label} 관련 논의가 이어지고 있습니다.",
                "대상과 지급 방식은 공식 발표를 확인해야 합니다.",
                "신청 창구와 시행 시기는 공고문에서 다시 봐야 합니다.",
            ]
        tags = self._build_tags(topic, self._preset().key)
        description = self._apply_channel_description_rules(
            self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{label} 한 가지 주제를 기준으로 대상, 혜택, 확인 포인트를 쉽게 정리했습니다.",
            ),
            detail_points=detail_points,
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="怨듭떇 ?뺣낫瑜?諛뷀깢?쇰줈 ?먯껜 移대뱶?댁뒪??臾멸뎄? ?대젅?댁뀡??援ъ꽦?덉뒿?덈떎.",
            thumbnail_text=self._resolve_short_thumbnail_text(topic, detail_points),
        )

    def _supplementary_segment(
        self,
        *,
        next_index: int,
        content: GeneratedContent,
        detail_summaries: list[str],
        keywords: str,
    ) -> str:
        preset_key = self._preset().key
        if detail_summaries:
            detail = detail_summaries[next_index % len(detail_summaries)].strip()
            if preset_key == "quotes_daily":
                return f"결국 이 문장은 {detail}"
            if preset_key == "welfare_news":
                return f"또 하나 중요한 점은 {detail}"
            return detail

        if preset_key == "quotes_daily":
            fallbacks = [
                "지금 마음이 흔들릴수록 짧고 단단한 문장이 오래 남습니다.",
                "오늘 필요한 건 거창한 위로보다 바로 붙잡을 한 줄일 수 있습니다.",
                "짧은 문장 하나가 하루의 태도를 바꾸는 날도 있습니다.",
            ]
        elif preset_key == "welfare_news":
            fallbacks = [
                "지원 대상과 신청 기한은 꼭 따로 다시 확인해 두는 편이 안전합니다.",
                "금액보다 중요한 건 실제로 내가 해당되는지 먼저 확인하는 일입니다.",
                "조건이 조금씩 달라질 수 있어 마지막 공고문 확인이 필요합니다.",
            ]
        else:
            fallbacks = [
                f"{keywords} 흐름은 추가 발표에 따라 체감이 달라질 수 있습니다.",
                "핵심 쟁점은 그대로지만 세부 기준은 더 보완될 수 있습니다.",
                "후속 발표가 이어지면 해석도 조금씩 달라질 가능성이 있습니다.",
            ]
        return fallbacks[next_index % len(fallbacks)]

    def _detail_headlines(self, details: list[TopicDetail], *, minimum: int, fallback: str) -> list[str]:
        values: list[str] = []
        signatures: set[str] = set()
        for item in details:
            candidates = (item.summary, item.title) if self._preset().key == "welfare_news" else (item.title, item.summary)
            for candidate in candidates:
                fact = self._clean_detail_fact(candidate)
                signature = self._fact_signature(fact)
                if not fact or fact in values or re.search(r"(보도|기자|최신 기사)", fact):
                    continue
                if signature and signature in signatures:
                    continue
                if self._preset().key == "welfare_news" and not self._is_practical_welfare_fact(fact):
                    continue
                if fact:
                    values.append(fact)
                    if signature:
                        signatures.add(signature)
                    break
        fallback_fact = self._clean_detail_fact(fallback) or fallback.strip()
        while len(values) < minimum:
            values.append(fallback_fact)
        return values[:minimum]

    def _apply_channel_description_rules(self, description: str, *, detail_points: list[str]) -> str:
        text = description.strip()
        preset_key = self._preset().key

        if preset_key == "welfare_news":
            blocks = [text]
            if "[핵심 확인 포인트]" not in text:
                blocks.extend(["", "[핵심 확인 포인트]", *[f"- {point}" for point in detail_points[:3]]])
            if "[오해하기 쉬운 부분]" not in text:
                blocks.extend(
                    [
                        "",
                        "[오해하기 쉬운 부분]",
                        "- 지역, 연령, 소득 기준에 따라 실제 대상이 달라질 수 있습니다.",
                        "- 신청 기간과 예산 소진 여부에 따라 지급 여부가 달라질 수 있습니다.",
                    ]
                )
            return "\n".join(blocks)

        if self._preset().collection_mode == "news":
            blocks = [text]
            if "[핵심 정리]" not in text:
                blocks.extend(["", "[핵심 정리]", *[f"- {point}" for point in detail_points[:3]]])
            return "\n".join(blocks)

        return text

    @staticmethod
    def _sentenceize_fact(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        cleaned = ContentGenerator._dedupe_adjacent_words(cleaned)
        if not cleaned:
            return ""
        if cleaned.startswith("#"):
            return cleaned.rstrip(" ,;:")
        if cleaned[-1] in ".!?":
            return cleaned
        return cleaned.rstrip(" ,;:") + "."

    @staticmethod
    def _dedupe_adjacent_words(text: str) -> str:
        tokens = (text or "").split()
        if not tokens:
            return ""
        deduped: list[str] = []
        previous = ""
        for token in tokens:
            normalized = re.sub(r"[^\w가-힣]", "", token).lower()
            if normalized and normalized == previous:
                continue
            deduped.append(token)
            if normalized:
                previous = normalized
        return " ".join(deduped).strip()

    @staticmethod
    def _fact_signature(text: str) -> str:
        return re.sub(r"[^0-9a-z가-힣]+", "", (text or "").lower())

    @staticmethod
    def _fact_tokens_for_similarity(text: str) -> set[str]:
        normalized = str(text or "").lower()
        normalized = re.sub(
            r"(우정사업본부|우본|정부|당국|업계|발표|확대|제공|준다|소식|관련|이슈|오늘|이번|최대|연|기준|우대|금리|뉴스)",
            " ",
            normalized,
        )
        normalized = re.sub(r"[^0-9a-z가-힣]+", " ", normalized)
        return {
            token
            for token in normalized.split()
            if len(token) >= 2 or token.isdigit()
        }

    def _facts_are_similar(self, left: str, right: str) -> bool:
        left_tokens = self._fact_tokens_for_similarity(left)
        right_tokens = self._fact_tokens_for_similarity(right)
        if not left_tokens or not right_tokens:
            return False
        overlap = len(left_tokens & right_tokens)
        return overlap / min(len(left_tokens), len(right_tokens)) >= 0.55

    def _clean_detail_fact(self, text: str) -> str:
        cleaned = re.sub(r"\[[^\]]+\]", "", text or "")
        cleaned = re.sub(r"\([^)]*\)", "", cleaned)
        cleaned = re.sub(r"https?://\S+|www\.\S+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b[a-z0-9-]+\.(?:com|net|kr)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*-\s*[A-Za-z0-9가-힣·\.\-]+$", "", cleaned)
        cleaned = re.sub(
            r"(연합뉴스|뉴시스|뉴스1|YTN|KBS|MBC|SBS|JTBC|한국경제|매일경제|머니투데이|조선비즈|서울경제)\s*(에 따르면|보도에 따르면)?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(KITA(?:\.NET)?|한국무역협회|연합인포맥스|에너지경제신문|문화일보|직썰|비즈한국|매일신문|전자신문)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"[가-힣A-Za-z]+\s*기자", "", cleaned)
        cleaned = re.sub(r"^(보도 기준으로|보도 기사로|보도에 따르면|에 따르면)\s*", "", cleaned)
        cleaned = re.sub(r"^.+?(보도 기준으로|보도 기사로|보도에 따르면|에 따르면)\s*", "", cleaned)
        cleaned = re.sub(r"(관련\s*)?(전달\s*내용|내용)?을?\s*확인한\s*최신\s*기사입니다\.?$", "", cleaned)
        cleaned = re.sub(r"최신\s*기사입니다\.?$", "", cleaned)
        cleaned = re.sub(r"^관련\s*", "", cleaned)
        cleaned = re.sub(r"\s*[:|]\s*경제\s*$", "", cleaned)
        cleaned = re.sub(r"\s*[:|]\s*증권\s*$", "", cleaned)
        cleaned = re.sub(r"\s*[:|]\s*생활경제\s*$", "", cleaned)
        cleaned = re.sub(r"\s*…\s*\.\.\.\s*", " ", cleaned)
        cleaned = re.sub(r"\s*\.{3,}\s*", " ", cleaned)
        cleaned = re.sub(r"\[[^\]]*(뉴스|속보|단독|기사|많이 본|경제기사)[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(주제 검색|분류 규칙|제작 설명|제작 과정|프롬프트|출력 형식|출처)", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,:;")
        if len(cleaned) > 96:
            trimmed = cleaned[:96].rsplit(" ", 1)[0].strip() or cleaned[:96]
            cleaned = trimmed.rstrip(" ,") + "..."
        return self._sentenceize_fact(cleaned)

    def _sanitize_short_segment(self, text: str, *, preset_key: str) -> str:
        cleaned = self._clean_detail_fact(text)
        if not cleaned:
            return ""
        banned_patterns = [
            r"오늘 브리핑",
            r"이번 브리핑",
            r"주제 후보",
            r"분류 규칙",
            r"제작",
            r"과정",
            r"절차",
            r"프롬프트",
            r"검색",
            r"출처",
            r"원문",
            r"연합뉴스",
            r"뉴스1",
            r"뉴시스",
            r"이 뉴스.*영향",
            r"나한테",
            r"내 생활에 영향",
        ]
        if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in banned_patterns):
            return ""
        if preset_key == "welfare_news" and "제목" in cleaned:
            return ""
        return cleaned

    def _sanitize_story_text(self, text: str) -> str:
        parts = re.split(r"(?<=[.!?])\s+|\n+", re.sub(r"\s+", " ", text or "").strip())
        kept: list[str] = []
        for part in parts:
            cleaned = self._sanitize_story_line(part)
            if cleaned and cleaned not in kept:
                kept.append(cleaned)
        return "\n\n".join(kept).strip()

    def _sanitize_story_line(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if not cleaned:
            return ""
        banned_patterns = [
            r"시청자",
            r"장면",
            r"\bscene\b",
            r"\b씬\b",
            r"주인공",
            r"오프닝",
            r"후킹",
            r"정책",
            r"제작",
            r"프롬프트",
            r"자막",
            r"화면",
            r"이미지",
            r"썸네일",
            r"대본",
            r"채널명",
            r"20~40초",
            r"3분",
            r"전체 \d+개",
            r"떠올리게 됩니다",
            r"현실은 더 이상 외면할 수 없는 문제",
            r"누구의 잘못을 따지기보다",
            r"이 이야기는",
            r"남은 \d+개",
            r"1번",
            r"2번",
            r"3번",
            r"고조회수",
            r"조회수",
            r"제목 패턴",
            r"제목 구조",
            r"초반 훅",
            r"중반 전개",
            r"후반 해소",
            r"차별화 포인트",
            r"인기 영상",
            r"인기 사연",
            r"구조만 참고",
            r"각색",
            r"베끼지",
            r"클릭",
        ]
        if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in banned_patterns):
            return ""
        return self._sentenceize_fact(cleaned)

    def _story_image_prompt(self, topic: RankedTopic, title: str, detail: str) -> str:
        summary = self._sanitize_story_text(detail or title or topic.representative_title)
        title_text = self._sanitize_story_text(title or topic.representative_title)
        topic_text = self._sanitize_story_text(topic.representative_title)
        lower_title = title_text.lower()
        if "저녁" in title_text or "평범" in title_text:
            composition = "wide dinner table composition, lived-in apartment interior, husband and wife framed with quiet distance"
        elif "균열" in title_text or "상처" in title_text:
            composition = "tense two-shot, one person in sharp focus and the other blurred behind, emotional distance visible"
        elif "선택" in title_text or "사기" in topic_text:
            composition = "important paperwork, bankbook or phone screen on the table, anxious hands, financial tension"
        elif "진심" in title_text:
            composition = "close emotional reunion, teary eyes, hesitant expression, warmer late-evening light"
        elif "가족" in title_text:
            composition = "family living room confrontation, multiple generations in frame, one protagonist isolated in foreground"
        elif "용서" in title_text:
            composition = "quiet final reconciliation, softened expression, calm home interior, emotional release after conflict"
        elif "hook" in lower_title:
            composition = "high-impact opening frame, strongest emotional moment, dramatic close-up, suspenseful atmosphere"
        else:
            composition = "cinematic Korean family drama composition, layered subjects and emotional distance"
        return self._sanitize_story_image_prompt(
            (
                f"photorealistic Korean drama still, cinematic 16:9 frame, emotionally grounded senior life story, "
                f"scene title: {title_text}, story context: {topic_text}, key moment: {summary}, "
                f"{composition}, "
                "Korean middle-aged or senior characters, realistic home or hospital or neighborhood setting, "
                "natural skin texture, expressive eyes, layered depth, moody but warm lighting, "
                "premium television drama composition, detailed wardrobe, believable gestures, "
                "subtle tension, high realism, shallow depth of field"
            )
        )

    def _sanitize_story_image_prompt(self, prompt: str) -> str:
        cleaned = re.sub(r"\s+", " ", prompt or "").strip().rstrip(",")
        if "no text" not in cleaned.lower():
            cleaned = f"{cleaned}, no text, no captions, no poster design, no collage".strip(", ")
        return cleaned

    def _story_detail_fragments(self, details: list[TopicDetail], topic: RankedTopic, *, minimum: int) -> list[str]:
        values: list[str] = []
        for item in details:
            if self._is_story_meta_detail(item.title, item.summary):
                continue
            for candidate in (item.summary, item.title):
                cleaned = self._sanitize_story_line(candidate)
                if cleaned and cleaned not in values:
                    values.append(cleaned)
                    break

        fallbacks = [
            "끝내 입 밖에 내지 못한 사정 하나가 오래된 침묵을 흔들기 시작했습니다.",
            "가족들은 같은 집 안에 있었지만 서로의 마음을 끝까지 다 알지 못한 채 버티고 있었습니다.",
            "늦게 꺼낸 한마디가 참아 온 감정을 한꺼번에 밀어 올렸습니다.",
            "작은 오해처럼 보였던 일이 사실은 오래 쌓인 상처와 연결되어 있었습니다.",
            "결국 더 아픈 사람은 큰소리친 사람이 아니라 오래 침묵한 사람이었습니다.",
        ]
        while len(values) < minimum:
            values.append(fallbacks[(len(values)) % len(fallbacks)])
        return values[:minimum]

    @staticmethod
    def _is_story_meta_detail(title: str, summary: str) -> bool:
        combined = re.sub(r"\s+", " ", f"{title} {summary}".strip()).lower()
        meta_patterns = (
            "고조회수",
            "조회수",
            "제목 패턴",
            "제목 구조",
            "초반 훅",
            "중반 전개",
            "후반 해소",
            "차별화 포인트",
            "인기 영상",
            "인기 사연",
            "구조를 참고",
            "베끼지 않습니다",
            "각색",
            "마무리한다",
            "시작합니다",
            "클릭",
        )
        return any(pattern in combined for pattern in meta_patterns)

    def _resolve_story_hook_duration(self, hook_script: str) -> int:
        estimated = self._estimate_story_segment_seconds(hook_script)
        configured = max(20, int(self.config.generation.hook_duration_seconds or 40))
        return max(20, min(configured, estimated))

    @staticmethod
    def _is_practical_welfare_fact(text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "")
        entertainment_noise_tokens = (
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
        )
        if any(token in normalized for token in entertainment_noise_tokens):
            return False
        tokens = (
            "대상",
            "신청",
            "지급",
            "지원",
            "혜택",
            "금액",
            "조건",
            "소득",
            "연령",
            "가구",
            "복지로",
            "주민센터",
            "행정복지센터",
            "홈페이지",
            "접수",
            "마감",
            "시행",
            "예산",
            "추경",
        )
        return any(token in normalized for token in tokens)

    @staticmethod
    def _pick_welfare_fact(facts: list[str], tokens: tuple[str, ...]) -> str:
        best_fact = ""
        best_score = 0
        for fact in facts:
            normalized = re.sub(r"\s+", "", fact or "")
            score = sum(2 for token in tokens if token in normalized)
            if "최대" in normalized or "만원" in normalized or "원" in normalized:
                score += 1
            if any(token in normalized for token in ("사용처", "제외", "논의", "검토", "종합", "총정리", "q&a")):
                score -= 1
            if "누가받고얼마받나" in normalized:
                score -= 2
            if score > best_score:
                best_fact = fact
                best_score = score
        return best_fact if best_score > 0 else ""

    @staticmethod
    def _format_money_phrase(text: str) -> str:
        normalized = re.sub(r"\s+", "", text or "")
        max_match = re.search(r"최대\s*([\d,]+)\s*만\s*원", normalized)
        if max_match:
            return f"최대 {max_match.group(1)}만 원"
        amount_match = re.search(r"([\d,]+)\s*만\s*원", normalized)
        if amount_match:
            return f"{amount_match.group(1)}만 원"
        won_match = re.search(r"([\d,]+)\s*원", normalized)
        if won_match:
            return f"{won_match.group(1)}원"
        return ""

    def _welfare_policy_label(self, topic: RankedTopic, facts: list[str]) -> str:
        label = self._strip_source_like_tokens(self._short_topic_label(topic))
        match = re.search(
            r"([가-힣A-Za-z0-9\s]{2,30}?(?:피해지원금|민생지원금|생활지원금|지원금|바우처|급여|수당|연금|감면|환급|지원))",
            label,
        )
        if match:
            label = match.group(1).strip()
        if not label and facts:
            label = re.sub(r"\s+", " ", facts[0]).strip()[:24]
        label = re.sub(r"\s+", " ", label).strip(" -,:;")
        return label or "복지 지원 정책"

    def _welfare_target_phrase(self, facts: list[str], target_fact: str) -> str:
        haystack = " ".join(facts)
        parts: list[str] = []
        if "취약계층" in haystack:
            parts.append("취약계층")
        if "기초생활수급자" in haystack:
            parts.append("기초생활수급자")
        if "차상위" in haystack:
            parts.append("차상위계층")
        if "한부모" in haystack:
            parts.append("한부모가족")
        if "어르신" in haystack:
            parts.append("어르신")
        if "청년" in haystack:
            parts.append("청년")
        income_match = re.search(r"소득\s*하위\s*(\d{1,3})\s*%", haystack)
        if income_match:
            parts.append(f"소득 하위 {income_match.group(1)}% 가구")
        parts = unique_preserve_order(parts)
        if parts:
            if len(parts) == 1:
                joined = parts[0]
            else:
                last_char = parts[0][-1]
                code = ord(last_char) - ord("가")
                has_batchim = code >= 0 and code % 28 != 0
                connector = "과" if has_batchim else "와"
                joined = f"{parts[0]}{connector} {parts[1]}" if len(parts) == 2 else f"{', '.join(parts[:-1])}과 {parts[-1]}"
            return self._sentenceize_fact(f"먼저 대상은 {joined}입니다")
        return self._compose_short_segment(
            "먼저 대상은 ",
            target_fact,
            preset_key=self._preset().key,
            fallback="누가 해당되는지부터 바로 짚어드리겠습니다",
        )

    def _welfare_benefit_phrase(self, facts: list[str], benefit_fact: str) -> str:
        haystack = " ".join(facts)
        amount_phrase = self._format_money_phrase(haystack) or self._format_money_phrase(benefit_fact)
        if amount_phrase:
            return self._sentenceize_fact(f"받을 수 있는 혜택은 가구 상황에 따라 {amount_phrase}까지 지원된다는 점입니다")
        return self._compose_short_segment(
            "받을 수 있는 혜택은 ",
            benefit_fact,
            preset_key=self._preset().key,
            fallback="이번 지원이 생활비 부담을 얼마나 덜어주는지가 핵심입니다",
        )

    def _welfare_access_phrase(
        self,
        facts: list[str],
        application_fact: str,
        restriction_fact: str,
        timing_fact: str,
    ) -> str:
        haystack = " ".join(facts)
        explicit_application_tokens = ("복지로", "정부24", "주민센터", "행정복지센터", "온라인", "방문", "접수", "신청")
        if "복지로" in haystack and "주민센터" in haystack:
            return self._sentenceize_fact("신청은 복지로나 주민센터 가운데 편한 방법으로 진행하시면 됩니다")
        if "복지로" in haystack:
            return self._sentenceize_fact("신청은 복지로에서 대상 여부와 제출 서류부터 확인하시면 가장 빠릅니다")
        if "정부24" in haystack:
            return self._sentenceize_fact("신청은 정부24에서 대상 여부와 제출 서류를 먼저 확인하시면 됩니다")
        if "행정복지센터" in haystack or "주민센터" in haystack:
            return self._sentenceize_fact("신청은 가까운 행정복지센터나 주민센터에서 진행하시면 됩니다")
        if restriction_fact:
            return self._sentenceize_fact("이번 지원은 사용 가능한 곳과 제외되는 곳을 함께 확인하는 것이 중요합니다")
        if application_fact and any(token in application_fact for token in explicit_application_tokens):
            return self._compose_short_segment(
                "신청이나 확인은 ",
                application_fact,
                preset_key=self._preset().key,
                fallback="신청 창구와 지급 기준을 함께 확인하는 것이 좋습니다",
            )
        if timing_fact or any(token in haystack for token in ("1차 지급", "순차 지급", "내달", "부터")):
            return self._sentenceize_fact("이번 지원은 신청 여부보다 대상 포함 여부와 지급 순서를 먼저 확인하는 것이 좋습니다")
        return self._sentenceize_fact("이번 지원은 대상 기준과 지급 방식을 함께 확인하면 이해가 훨씬 쉬워집니다")

    def _welfare_timing_phrase(self, facts: list[str], timing_fact: str) -> str:
        haystack = " ".join(facts)
        first_date = re.search(r"(\d{1,2})일(?:부터)?", haystack)
        next_date = re.search(r"내달\s*(\d{1,2})일", haystack)
        if "취약계층" in haystack and first_date and next_date:
            return self._sentenceize_fact(
                f"지급 시기는 취약계층이 {first_date.group(1)}일부터 먼저 받고, 나머지 대상은 다음 달 {next_date.group(1)}일부터 순차 지급됩니다"
            )
        if first_date and next_date:
            return self._sentenceize_fact(
                f"시기는 {first_date.group(1)}일부터 1차 지급이 시작되고, 다음 달 {next_date.group(1)}일부터 추가 지급이 이어집니다"
            )
        if first_date:
            return self._sentenceize_fact(f"시기는 {first_date.group(1)}일부터 순차 지급이 시작된다는 점까지 기억해두시면 좋겠습니다")
        if timing_fact:
            return self._compose_short_segment(
                "놓치면 안 되는 시기는 ",
                timing_fact,
                preset_key=self._preset().key,
                fallback="시행 시기와 마감 일정도 함께 챙겨두는 것이 좋습니다",
            )
        return ""

    def _build_welfare_video_title(self, policy_label: str, benefit_line: str, access_line: str) -> str:
        channel = self.config.active_channel
        prefix = (channel.title_prefix if channel and channel.title_prefix else self.config.generation.title_prefix).strip()
        if "최대" in benefit_line and "원" in benefit_line:
            core = f"{policy_label}, 누가 얼마나 받나"
        elif "신청" in access_line:
            core = f"{policy_label}, 신청 대상 정리"
        else:
            core = f"{policy_label}, 대상과 혜택 정리"
        core = re.sub(r"\s+", " ", core).strip(" -,:;_|")
        if len(core) > 34:
            core = core[:34].rstrip(" .,") + "..."
        return " ".join(piece for piece in (prefix, core) if piece).strip()

    def _build_title(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        manual_title = channel.manual_title.strip() if channel and channel.manual_title else ""
        if manual_title:
            return manual_title
        base_title = self._strip_source_like_tokens(self._short_topic_label(topic))
        if self._preset().key in {"economy_news", "welfare_news"}:
            compact_title = re.sub(r"\.{2,}", " ", base_title)
            compact_title = re.sub(r"\s+", " ", compact_title).strip(" -,:;_|")
            if len(compact_title) > 34:
                compact_title = compact_title[:34].rstrip(" .,") + "..."
            prefix = (channel.title_prefix if channel and channel.title_prefix else self.config.generation.title_prefix).strip()
            return " ".join(piece for piece in (prefix, compact_title) if piece).strip()
        pieces = [
            (channel.title_prefix if channel and channel.title_prefix else self.config.generation.title_prefix).strip(),
            base_title,
            (channel.title_suffix if channel and channel.title_suffix else self.config.generation.title_suffix).strip(),
        ]
        return " ".join(piece for piece in pieces if piece)

    def _build_tags(self, topic: RankedTopic, preset_key: str) -> list[str]:
        preset = preset_by_key(preset_key)
        cleaned_title = self._tag_token(self._short_topic_label(topic))
        keyword_tags = [self._tag_token(keyword) for keyword in topic.keywords[:6]]
        tags = [
            cleaned_title,
            *keyword_tags,
            self._tag_token(self.config.generation.channel_name),
            self._tag_token(preset.label),
            "유튜브자동화",
        ]
        normalized = [self._normalize_tag(tag) for tag in unique_preserve_order(tags)]
        return [tag for tag in normalized if tag][: self.config.generation.max_tags]

    @staticmethod
    def _tag_token(value: str) -> str:
        cleaned = re.sub(r"[\"“”‘’#]", "", value or "")
        cleaned = re.sub(r"[^\w가-힣]", "", cleaned)
        return cleaned.strip()

    def _build_background_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_background_prompt.strip():
            return channel.manual_background_prompt.strip()
        title = self._short_topic_label(topic)
        preset_key = self._preset().key
        if preset_key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts background. Primary request: create a high-CTR vertical news background about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: one dominant focal reaction or a two-side conflict composition, large on screen, clean subject separation. "
                "Style/medium: photoreal Korean breaking-news visual, premium broadcast graphic feel. "
                "Composition/framing: 9:16 vertical, center-weighted, dramatic foreground, strong depth, clean space for headline text overlay. "
                "Lighting/mood: urgent, cinematic, high contrast red and blue news lighting. "
                "Constraints: no logos, no article screenshot, no watermark, no embedded text."
            )
        if preset_key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts background. Primary request: create a high-CTR vertical welfare information background about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: one middle-aged Korean person or couple reacting strongly to a phone alert, support notice, or payment message. "
                "Style/medium: photoreal Korean life-information visual, polished Shorts card-news feel. "
                "Composition/framing: 9:16 vertical, dominant close-up subject, supporting prop in hand, clear readable negative space for headline text overlay. "
                "Lighting/mood: bright but urgent, trustworthy, practical, crisp contrast. "
                "Constraints: no government logo, no watermark, no embedded text."
            )
        return (
            f"Premium vertical editorial artwork for '{title}', "
            f"style: {self._preset().visual_style}, cinematic lighting, high detail, no text"
        )

    def _build_thumbnail_prompt(self, topic: RankedTopic) -> str:
        channel = self.config.active_channel
        if channel and channel.manual_thumbnail_prompt.strip():
            return channel.manual_thumbnail_prompt.strip()
        title = self._short_topic_label(topic)
        if self._preset().collection_mode == "stories":
            return (
                f"Cinematic Korean YouTube thumbnail for a senior life story about '{title}', "
                "photorealistic close-up of a Korean senior protagonist, highly emotional expression, "
                "dramatic relationship tension in the background, premium Korean drama look, strong face focus, "
                "clean left and lower text-safe space, high contrast, shallow depth of field, no text, no watermark, no poster, no collage"
            )
        if self._preset().key == "economy_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._news_visual_scene(topic)}. "
                "Subject: dramatic reaction shot, strong facial expression, or face-off composition when the issue implies conflict. "
                "Style/medium: photoreal Korean premium Shorts thumbnail, TV news urgency without looking fake. "
                "Composition/framing: 9:16, large face or dominant subject, bold central focus, dramatic depth, leave top and lower-center text-safe space. "
                "Lighting/mood: intense red-blue breaking news lighting, sharp contrast, emotionally charged. "
                "Constraints: no article screenshot, no logos, no watermark, no embedded text."
            )
        if self._preset().key == "welfare_news":
            return (
                f"Use case: photorealistic-natural. Asset type: Korean YouTube Shorts thumbnail background. Primary request: create a viral-looking vertical thumbnail image about '{title}'. "
                f"Scene/backdrop: {self._welfare_visual_scene(topic)}. "
                "Subject: shocked or focused middle-aged Korean person looking at a payment message, envelope, or benefit notice, with a clear practical-life prop. "
                "Style/medium: photoreal Korean life-info thumbnail, crisp and trustworthy with strong curiosity. "
                "Composition/framing: 9:16, large upper-body close-up, prop visible, warm interior depth, strong text-safe space at top and lower-center. "
                "Lighting/mood: high clarity, warm plus alert contrast, emotionally engaging. "
                "Constraints: no government logo, no watermark, no embedded text."
            )
        return (
            f"Premium YouTube thumbnail background for '{title}', "
            f"style: {self._preset().visual_style}, dramatic focal point, no text"
        )

    def _resolve_thumbnail_text(self, text: str, topic: RankedTopic) -> str:
        candidate = self._strip_source_like_tokens((text or self._short_topic_label(topic)).strip())
        if self._preset().collection_mode == "stories":
            candidate = re.split(r"[|:!?]", candidate, maxsplit=1)[0].strip()
            candidate = re.sub(r"\s+", " ", candidate)
            words = candidate.split()
            if len(words) > 4:
                candidate = " ".join(words[:4])
            return candidate[:18].strip()
        return candidate[:26]

    def _resolve_short_thumbnail_text(self, topic: RankedTopic, detail_points: list[str]) -> str:
        return self._pick_short_thumbnail_text(topic, detail_points, preferred_text="")

    def _news_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "inflation":
            return (
                "Korean grocery aisle or kitchen table with price tags, receipt, shopping basket, and anxious reaction, "
                "consumer-cost pressure, cinematic news lighting"
            )
        if bucket == "rates":
            return (
                "Korean bank-loan or mortgage tension scene with loan papers, smartphone banking alert, calculator, "
                "serious adult reaction, crisp newsroom contrast"
            )
        if bucket == "housing":
            return (
                "Korean apartment skyline or home-loan consultation mood, contract papers, house model, "
                "tense realistic housing-market atmosphere"
            )
        if bucket == "markets":
            return (
                "Korean market-watch scene with exchange-rate or stock movement on phone screen, city night lights, "
                "high-volatility financial mood"
            )
        if bucket == "global":
            return (
                "global-tension news scene with oil, shipping route, or world-map crisis motif, "
                "split-focus confrontation atmosphere and dramatic broadcast lighting"
            )
        if bucket == "policy":
            return (
                "Korean public-policy reaction scene with document, briefing backdrop, urban daily-life setting, "
                "clear change-impact mood"
            )
        return (
            "Korean breaking-news atmosphere with a reacting adult, alert screen glow, dramatic newsroom lighting, "
            "high public-interest issue visual"
        )

    def _welfare_visual_scene(self, topic: RankedTopic) -> str:
        bucket = self._short_topic_bucket(topic, [])
        if bucket == "application":
            return (
                "Korean resident checking an application page, 주민센터 notice, or deadline alert on smartphone, "
                "urgent but trustworthy life-info mood"
            )
        if bucket == "eligibility":
            return (
                "middle-aged Korean adult reviewing eligibility papers, letter, or welfare criteria checklist at home, "
                "clear practical-life tension"
            )
        if bucket == "payment":
            return (
                "Korean phone payment alert, envelope, or support-message scene with surprised but hopeful reaction, "
                "practical benefit and money timing mood"
            )
        if bucket == "regional":
            return (
                "Korean neighborhood or local government service setting with resident checking region-specific benefit information, "
                "realistic civic-life atmosphere"
            )
        return (
            "middle-aged Korean person or couple reacting to a life-benefit message, warm home or desk setting, "
            "clear eligibility-and-benefit atmosphere"
        )

    def _sanitize_short_segment(self, text: str, *, preset_key: str) -> str:
        cleaned = self._clean_detail_fact(text)
        if not cleaned:
            return ""

        replacements = [
            (r"\s*관련 핵심\.?$", ""),
            (r"^또 하나 중요한 점은\s*", ""),
            (r"^이번 이슈가 커진 이유는\s*", ""),
        ]
        for pattern, repl in replacements:
            cleaned = re.sub(pattern, repl, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bheadline\s*vs\.?\s*core\b", "체감물가와 근원물가", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bheadline(?=과)", "체감물가", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bcore\b", "근원물가", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bheadline\b", "체감물가", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"근원물가\s*물가", "근원물가", cleaned)
        cleaned = re.sub(r"\bCPI\b", "소비자물가", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'[\"“”‘’]', "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,:;")

        banned_patterns = [
            r"브리핑",
            r"주제 후보",
            r"분류 규칙",
            r"제작",
            r"과정",
            r"프로젝트",
            r"검색",
            r"출처",
            r"원문",
            r"연합뉴스",
            r"뉴스1",
            r"한겨레",
            r"매일경제",
            r"서울경제",
            r"한국경제",
            r"이 뉴스",
            r"나한테 영향",
            r"공식 사이트",
            r"공고문",
            r"원문 확인",
            r"사이트에서",
            r"참고하",
            r"시청자",
            r"장면",
            r"\bscene\b",
            r"\b씬\b",
        ]
        if any(re.search(pattern, cleaned, re.IGNORECASE) for pattern in banned_patterns):
            return ""
        if preset_key == "welfare_news" and "제목" in cleaned:
            return ""
        return self._sentenceize_fact(cleaned)

    def _compose_short_segment(
        self,
        prefix: str,
        fact: str,
        *,
        preset_key: str,
        fallback: str,
        max_chars: int = 72,
    ) -> str:
        base = self._sanitize_short_segment(fact, preset_key=preset_key).rstrip(".")
        if not base:
            return self._sentenceize_fact(fallback)
        if len(base) > max_chars:
            trimmed = base[:max_chars].rsplit(" ", 1)[0].strip() or base[:max_chars]
            base = trimmed.rstrip(" ,") + "..."
        if prefix:
            return self._sentenceize_fact(f"{prefix}{base}")
        return self._sentenceize_fact(base)

    def _news_gap_fill(self, topic: RankedTopic, next_index: int) -> str:
        label = self._short_topic_label(topic)
        candidates = [
            "관련 수치와 기준이 함께 나와야 전체 흐름이 더 또렷해질 수 있습니다",
            "발표 시점보다 세부 적용 범위가 실제 반응을 더 가를 수 있습니다",
            "후속 발표가 이어질수록 해석은 조금씩 더 구체화될 수 있습니다",
        ]
        if "물가" in label:
            candidates[0] = "물가 지표는 유가와 환율 같은 외부 변수까지 함께 봐야 흐름이 읽힙니다"
        if any(token in label for token in ("증시", "코스피", "코스닥", "주가")):
            candidates[0] = "지금은 낙폭이 더 커질지, 외국인 수급이 다시 돌아올지가 핵심입니다"
        if any(token in label for token in ("포상금", "신고", "탈세")):
            candidates[0] = "신고가 실제 포상금 지급으로 이어지려면 적용 기준과 입증 자료가 중요합니다"
        if any(token in label for token in ("규제", "다주택자", "부동산")):
            candidates[1] = "규제는 대상 범위와 적용 시점이 함께 나와야 실제 반응이 분명해집니다"
        return self._sentenceize_fact(candidates[next_index % len(candidates)])

    @staticmethod
    def _direction_bucket(text: str) -> str:
        haystack = str(text or "")
        up_terms = ("상승", "급등", "오름", "반등", "강세", "올라", "뛰어", "회복", "탈환", "돌파")
        down_terms = ("하락", "급락", "후퇴", "약세", "내려", "밀려", "부진", "주저앉", "반납")
        if any(term in haystack for term in up_terms):
            return "up"
        if any(term in haystack for term in down_terms):
            return "down"
        return ""

    def _news_topic_overlap_tokens(self, topic: RankedTopic) -> list[str]:
        seed_text = " ".join([self._short_topic_label(topic), *topic.keywords[:6]])
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9가-힣]{2,}", normalize_text(seed_text))
            if token
            and token not in {"오늘", "뉴스", "이슈", "브리핑", "핵심", "경제", "시장", "금융", "흐름"}
        ]
        return unique_preserve_order(tokens)

    def _news_fact_candidates(self, topic: RankedTopic, details: list[TopicDetail], *, minimum: int, fallback: str) -> list[str]:
        values: list[str] = []
        signatures: set[str] = set()
        label_signature = self._fact_signature(self._short_topic_label(topic))
        label_direction = self._direction_bucket(self._short_topic_label(topic))
        topic_overlap_tokens = self._news_topic_overlap_tokens(topic)
        followup_tokens = tuple(normalize_text(token) for token in ("적용 대상", "우대 조건", "시행 시점", "반등 흐름", "실적 기대", "대출 규제"))
        for item in details:
            for candidate in (item.summary, item.title):
                fact = self._sanitize_short_segment(candidate, preset_key=self._preset().key)
                signature = self._fact_signature(fact)
                if not fact or fact in values:
                    continue
                if signature and signature in signatures:
                    continue
                if signature and label_signature and signature == label_signature:
                    continue
                if any(self._facts_are_similar(fact, existing) for existing in values):
                    continue
                normalized_fact = normalize_text(fact)
                if topic_overlap_tokens and not any(token in normalized_fact for token in topic_overlap_tokens):
                    if not any(token in normalized_fact for token in followup_tokens):
                        continue
                fact_direction = self._direction_bucket(fact)
                if label_direction and fact_direction and label_direction != fact_direction:
                    continue
                if len(fact) < 10:
                    continue
                if len(fact.split()) <= 2 and any(
                    token in fact for token in ("연합뉴스", "뉴스1", "한국무역협회", "KITA", "일보", "신문", "방송", "기자")
                ):
                    continue
                values.append(fact)
                if signature:
                    signatures.add(signature)
                break
            if len(values) >= minimum:
                break
        while len(values) < minimum:
            gap_fill = self._news_gap_fill(topic, len(values))
            signature = self._fact_signature(gap_fill)
            if signature and signature not in signatures:
                values.append(gap_fill)
                signatures.add(signature)
                continue
            values.append(self._sentenceize_fact(fallback))
        return values[:minimum]

    def _build_news_briefing_segments(self, topic: RankedTopic, details: list[TopicDetail]) -> list[str]:
        facts = self._news_fact_candidates(topic, details, minimum=4, fallback=topic.representative_title)
        return self._news_segments_from_detail_points(topic, facts) or [
            self._sentenceize_fact(self._news_hook_line(topic, bucket=self._short_topic_bucket(topic, facts))),
            self._sentenceize_fact(facts[0].rstrip(".")),
            self._sentenceize_fact(self._news_explainer_line(topic, facts, bucket=self._short_topic_bucket(topic, facts))),
            self._sentenceize_fact(self._news_reaction_line(topic, facts, bucket=self._short_topic_bucket(topic, facts))),
            self._sentenceize_fact(self._news_watch_line(topic, facts, bucket=self._short_topic_bucket(topic, facts))),
            self._sentenceize_fact(self._news_cta_line()),
        ]

    def _news_hook_line(self, topic: RankedTopic, *, bucket: str) -> str:
        label = self._short_topic_label(topic)
        saving_terms = ("적금", "예금", "저축", "우대금리", "청약")
        if any(term in label for term in saving_terms):
            return f"오늘 생활경제 뉴스는 {label} 소식부터 전해드리겠습니다"
        hook_map = {
            "inflation": f"오늘 경제 뉴스는 {label} 흐름부터 짚어보겠습니다",
            "rates": f"오늘 경제 뉴스는 {label} 변수부터 살펴보겠습니다",
            "housing": f"오늘 부동산 뉴스는 {label} 이슈가 중심입니다",
            "markets": f"오늘 금융시장에서는 {label} 이슈가 가장 크게 주목받고 있습니다",
            "global": f"오늘 경제 뉴스는 {label} 흐름부터 보겠습니다",
            "policy": f"오늘은 {label} 이슈부터 차근히 정리해드리겠습니다",
        }
        return hook_map.get(bucket, f"오늘 경제 뉴스, {label} 이슈부터 바로 정리해드리겠습니다")

    def _news_cta_line(self) -> str:
        channel_name = (self.config.generation.channel_name or "").strip()
        if channel_name:
            return f"지금까지 {channel_name}였습니다. 도움이 되셨다면 구독과 좋아요 부탁드립니다"
        return "지금까지 전해드린 경제 뉴스가 도움이 되셨다면 구독과 좋아요 부탁드립니다"

    @staticmethod
    def _is_plain_explanatory_news_fact(fact: str) -> bool:
        cleaned = str(fact or "").strip().rstrip(".!?")
        if len(cleaned) < 12:
            return False
        sentence_markers = (
            "입니다",
            "합니다",
            "됩니다",
            "있습니다",
            "나옵니다",
            "보입니다",
            "커집니다",
            "높아집니다",
            "낮아집니다",
            "이어집니다",
            "꼽힙니다",
            "거론됩니다",
            "읽힙니다",
            "전망됩니다",
            "전해집니다",
            "나타납니다",
            "움직입니다",
            "확대됩니다",
            "축소됩니다",
            "확인해야 합니다",
            "볼 필요가 있습니다",
            "중요합니다",
        )
        if any(marker in cleaned for marker in sentence_markers):
            return True
        headline_like_terms = ("단독", "속보", "무게", "필요", "주목", "될 듯", "올릴 수도")
        return not any(term in cleaned for term in headline_like_terms)

    def _news_summary_line(self, topic: RankedTopic, facts: list[str], *, bucket: str) -> str:
        label = self._short_topic_label(topic)
        haystack = " ".join([label, *facts])
        if facts and self._is_plain_explanatory_news_fact(facts[0]):
            return facts[0].rstrip(".")
        if any(term in haystack for term in ("적금", "예금", "저축", "우대금리", "청약")):
            return "핵심은 우대 조건이 달라지면서 실제로 받을 수 있는 혜택 차이가 커질 수 있다는 점입니다"
        if bucket == "inflation":
            if any(term in haystack for term in ("유가", "환율", "원달러", "중동")):
                return "유가와 환율이 흔들리면 장바구니 물가까지 다시 밀어 올릴 수 있다는 우려가 커지고 있습니다"
            return "최근에는 생활물가가 다시 들썩일 수 있다는 전망이 나오면서 부담이 커질 수 있다는 목소리가 나옵니다"
        if bucket == "rates":
            if "동결" in haystack and "인하" in haystack:
                return "시장에서는 기준금리를 바로 내리기보다 일단 동결 쪽에 무게를 두는 분위기입니다"
            if any(term in haystack for term in ("인상", "올릴 수도", "통화정책 대응")):
                return "전쟁 변수와 물가 불안이 겹치면서 금리를 쉽게 낮추기 어려운 분위기가 강해지고 있습니다"
            return "한국은행이 금리 방향을 쉽게 바꾸지 않고 신중하게 판단할 거라는 전망이 우세합니다"
        if bucket == "housing":
            return "부동산 시장에서는 규제와 대출 조건이 실제 체감 부담을 얼마나 바꾸는지가 핵심으로 떠오르고 있습니다"
        if bucket == "markets":
            if any(term in haystack for term in ("삼성전자", "하이닉스", "반도체")):
                return "반도체와 대형주 흐름이 증시 전체 분위기를 좌우하는 장면이 이어지고 있습니다"
            return "지금 금융시장은 하루 변동보다 다음 흐름이 이어질지에 더 민감하게 반응하고 있습니다"
        if bucket == "global":
            return "해외 변수 하나가 국내 물가와 금융시장까지 연쇄적으로 흔들 수 있다는 우려가 커지고 있습니다"
        if bucket == "policy":
            return f"{label} 이슈는 발표보다 실제 적용 대상과 시점이 어떻게 잡히느냐가 더 중요해지고 있습니다"
        return f"{label} 관련해서는 숫자 자체보다 실제 체감 변화가 더 중요하다는 반응이 나오고 있습니다"

    def _news_explainer_line(self, topic: RankedTopic, facts: list[str], *, bucket: str) -> str:
        if len(facts) > 1 and facts[1] and self._is_plain_explanatory_news_fact(facts[1]) and not self._facts_are_similar(facts[0], facts[1]):
            return f"쉽게 말해, {facts[1].rstrip('.')}"
        haystack = " ".join([self._short_topic_label(topic), *facts])
        if any(term in haystack for term in ("적금", "예금", "저축", "우대금리", "청약")):
            return "쉽게 말해, 같은 기간 저축해도 실제로 쌓이는 혜택이 더 커질 수 있다는 뜻입니다"
        if bucket == "inflation":
            return "쉽게 말해, 장바구니 물가와 생활비 부담을 다시 자극할 수 있다는 뜻입니다"
        if bucket == "rates":
            if any(term in haystack for term in ("전쟁", "중동", "유가", "환율", "물가")):
                return "쉽게 말해, 전쟁과 유가, 환율 같은 변수가 남아 있으면 금리를 섣불리 낮추기 어렵다는 뜻입니다"
            return "쉽게 말해, 금리 방향이 바뀌면 대출 부담과 소비 심리도 함께 흔들릴 수 있다는 뜻입니다"
        if bucket == "housing":
            return "쉽게 말해, 실수요자의 집 구하기 부담과 대출 여건이 달라질 수 있다는 뜻입니다"
        if bucket == "markets":
            return "쉽게 말해, 환율과 주가 흐름이 기업 실적과 투자 심리에 바로 번질 수 있다는 뜻입니다"
        if bucket == "global":
            return "쉽게 말해, 해외 변수 하나가 국내 물가와 금융시장까지 흔들 수 있다는 뜻입니다"
        if bucket == "policy":
            return "쉽게 말해, 제도 변화가 실제 생활비와 선택에 바로 영향을 줄 수 있다는 뜻입니다"
        return "쉽게 말해, 이번 변화가 숫자에 그치지 않고 실제 체감으로 이어질 수 있다는 뜻입니다"

    def _news_reaction_line(self, topic: RankedTopic, facts: list[str], *, bucket: str) -> str:
        if len(facts) > 2 and facts[2] and self._is_plain_explanatory_news_fact(facts[2]) and not self._facts_are_similar(facts[0], facts[2]):
            return f"관심이 큰 이유는 {facts[2].rstrip('.')}"
        haystack = " ".join([self._short_topic_label(topic), *facts])
        if any(term in haystack for term in ("적금", "예금", "저축", "우대금리", "청약")):
            return "관심이 큰 이유는 가입 조건이 맞는 사람에겐 실제 수익 차이가 생각보다 크게 벌어질 수 있어서입니다"
        if bucket == "inflation":
            return "관심이 큰 이유는 물가가 오르면 식비와 교통비처럼 자주 쓰는 항목부터 바로 체감되기 때문입니다"
        if bucket == "rates":
            if "동결" in haystack:
                return "관심이 큰 이유는 금리 한 번의 판단이 대출 이자와 소비 심리에 바로 영향을 주기 때문입니다"
            return "관심이 큰 이유는 금리 신호 하나로 대출 이자와 투자 판단이 함께 바뀔 수 있기 때문입니다"
        if bucket == "housing":
            return "관심이 큰 이유는 규제와 대출 조건 변화가 실수요자의 움직임을 바로 바꿀 수 있기 때문입니다"
        if bucket == "markets":
            return "관심이 큰 이유는 기업 실적 전망과 투자 심리가 동시에 흔들릴 수 있기 때문입니다"
        if bucket == "global":
            return "관심이 큰 이유는 해외 변수 하나가 유가와 환율, 물가로 이어질 수 있기 때문입니다"
        if bucket == "policy":
            return "관심이 큰 이유는 발표 문구보다 실제 적용 대상과 시점이 더 중요하게 작용하기 때문입니다"
        return "관심이 큰 이유는 숫자 변화보다 실제 생활에 닿는 체감 차이가 더 크게 느껴질 수 있기 때문입니다"

    def _news_watch_line(self, topic: RankedTopic, facts: list[str], *, bucket: str) -> str:
        if len(facts) > 3 and facts[3] and self._is_plain_explanatory_news_fact(facts[3]) and not any(self._facts_are_similar(facts[index], facts[3]) for index in range(min(3, len(facts)))):
            return f"지금 같이 볼 포인트는 {facts[3].rstrip('.')}"
        haystack = " ".join([self._short_topic_label(topic), *facts])
        if any(term in haystack for term in ("적금", "예금", "저축", "우대금리", "청약")):
            return "지금 같이 볼 포인트는 실제 적용 대상과 우대 조건이 어떻게 달라지는지입니다"
        if any(term in haystack for term in ("삼성전자", "하이닉스", "반도체", "코스피", "코스닥")):
            return "지금 같이 볼 포인트는 이 반등 흐름이 하루짜리인지, 실적 기대까지 이어지는지입니다"
        if any(term in haystack for term in ("환율", "유가", "원달러", "달러")):
            return "지금 같이 볼 포인트는 환율과 유가 움직임이 다음 거래일까지 안정되는지입니다"
        if bucket == "inflation":
            return "지금 같이 볼 포인트는 유가와 환율 움직임이 생활물가로 얼마나 이어지는지입니다"
        if bucket == "rates":
            return "지금 같이 볼 포인트는 기준금리 전망과 시중 대출 금리 반영 속도입니다"
        if bucket == "housing":
            return "지금 같이 볼 포인트는 대출 규제와 실수요자 부담 변화가 어디까지 이어지는지입니다"
        if bucket == "markets":
            return "지금 같이 볼 포인트는 환율과 증시 반응이 하루짜리인지 추세로 이어지는지입니다"
        if bucket == "global":
            return "지금 같이 볼 포인트는 해외 변수가 국내 물가와 시장으로 얼마나 번지는지입니다"
        if bucket == "policy":
            return "지금 같이 볼 포인트는 실제 대상과 시행 시점이 언제 확정되는지입니다"
        return "지금 같이 볼 포인트는 다음 발표에서 숫자와 적용 범위가 얼마나 또렷해지는지입니다"

    def _news_segments_from_description(self, description: str) -> list[str]:
        lead_lines: list[str] = []
        for raw_line in description.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("[") or line.startswith("#"):
                break
            if self.config.generation.channel_name and line == self.config.generation.channel_name:
                continue
            lead_lines.append(line)
        lead_text = " ".join(lead_lines).strip()
        if not lead_text:
            return []

        candidates = re.split(r"(?<=[.!?])\s+", lead_text)
        segments = [
            cleaned
            for cleaned in (
                self._sanitize_short_segment(sentence, preset_key=self._preset().key)
                for sentence in candidates
            )
            if cleaned
        ]
        return segments[:7]

    def _news_segments_from_detail_points(self, topic: RankedTopic, detail_points: list[str]) -> list[str]:
        cleaned_points = [
            cleaned
            for cleaned in (
                self._sanitize_short_segment(point, preset_key=self._preset().key)
                for point in detail_points[:4]
            )
            if cleaned and len(cleaned) >= 12
        ]
        if len(cleaned_points) < 3:
            return []

        bucket = self._short_topic_bucket(topic, cleaned_points)
        segments = [
            self._sentenceize_fact(self._news_hook_line(topic, bucket=bucket)),
            self._sentenceize_fact(self._news_summary_line(topic, cleaned_points, bucket=bucket)),
            self._sentenceize_fact(self._news_explainer_line(topic, cleaned_points, bucket=bucket)),
            self._sentenceize_fact(self._news_reaction_line(topic, cleaned_points, bucket=bucket)),
        ]
        segments.append(self._sentenceize_fact(self._news_watch_line(topic, cleaned_points, bucket=bucket)))
        segments.append(self._sentenceize_fact(self._news_cta_line()))
        return segments[:6]

    def _build_news_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        label = self._short_topic_label(topic)
        preset_key = self._preset().key
        segments = self._build_news_briefing_segments(topic, details)
        tags = self._build_tags(topic, preset_key)
        detail_points = self._news_fact_candidates(topic, details, minimum=4, fallback=label)
        description = self._apply_channel_description_rules(
            self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{label} 이슈를 실제 뉴스처럼 쉽고 또렷하게 풀어 정리한 쇼츠입니다.",
            ),
            detail_points=detail_points,
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=preset_key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="자체 대본과 자체 내레이션 구조로 최신 이슈를 한 주제씩 재구성한 뉴스 쇼츠입니다.",
            thumbnail_text=self._resolve_short_thumbnail_text(topic, detail_points),
        )

    def _build_quotes_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        quote = self._short_topic_label(topic)
        cleaned_points: list[str] = []
        for item in details:
            for candidate in (item.summary, item.title):
                cleaned = self._sanitize_short_segment(candidate, preset_key=self._preset().key)
                if not cleaned:
                    continue
                if any(
                    token in cleaned
                    for token in ("주제", "구성", "해석형", "원문", "저장하고 싶은", "핵심 문장", "감정 연결", "한 줄로 정리", "실전 적용")
                ):
                    continue
                cleaned_points.append(cleaned)
                break
        reality_line = cleaned_points[0] if cleaned_points else "버티는 날이 길어질수록 결국 사람을 살리는 건 마지막까지 놓지 않는 마음입니다"
        action_line = cleaned_points[1] if len(cleaned_points) > 1 else "조급할수록 눈앞의 속도보다 오늘 내가 지킬 태도를 먼저 붙잡는 것이 더 중요합니다"
        ending_line = cleaned_points[2] if len(cleaned_points) > 2 else "흔들려도 멈추지 않는 사람이 결국 자기 시간을 다시 가져오게 됩니다"
        segments = [
            self._sentenceize_fact(quote),
            self._sentenceize_fact(f"이 말이 필요한 순간은 {reality_line.rstrip('.')}"),
            self._sentenceize_fact(f"현실에서는 {action_line.rstrip('.')}"),
            self._sentenceize_fact(ending_line),
            self._sentenceize_fact("오늘 이 문장이 마음에 남았다면 저장해 두고, 필요한 사람에게 조용히 보내 보세요"),
        ]
        tags = self._build_tags(topic, self._preset().key)
        detail_points = cleaned_points[:4] or [quote]
        description = self._build_description(
            topic=topic,
            detail_points=detail_points,
            tags=tags,
            summary_intro=f"{quote}을 오늘의 감정과 현실에 맞게 짧고 깊게 풀어낸 쇼츠입니다.",
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=self._preset().key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="짧은 공감형 통찰 문장을 자체 제작 내레이션과 감성 비주얼로 재구성했습니다.",
            thumbnail_text=self._resolve_thumbnail_text(topic.representative_title, topic),
        )

    def _build_welfare_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        facts: list[str] = []
        for item in details:
            for candidate in (item.summary, item.title):
                cleaned = self._clean_detail_fact(candidate).rstrip(".")
                if not cleaned or not self._is_practical_welfare_fact(cleaned):
                    continue
                if cleaned in facts:
                    continue
                facts.append(cleaned)
                break
        while len(facts) < 4:
            fallback = self._clean_detail_fact(topic.representative_title).rstrip(".")
            if not fallback or fallback in facts:
                break
            facts.append(fallback)
        label = self._short_topic_label(topic)
        preset_key = self._preset().key
        bucket = self._short_topic_bucket(topic, facts)
        target_fact = self._pick_welfare_fact(
            facts,
            ("대상", "연령", "소득", "가구", "차상위", "수급자", "어르신", "청년", "시민", "주민", "취약계층"),
        )
        benefit_fact = self._pick_welfare_fact(
            facts,
            ("지급", "지원", "금액", "혜택", "바우처", "수당", "연금", "감면", "환급", "급여", "최대", "만원"),
        )
        application_fact = self._pick_welfare_fact(
            facts,
            ("신청", "접수", "복지로", "정부24", "주민센터", "행정복지센터", "홈페이지", "온라인", "방문"),
        )
        timing_fact = self._pick_welfare_fact(facts, ("마감", "시행", "기한", "기간", "예산", "추경", "부터", "까지"))
        restriction_fact = self._pick_welfare_fact(facts, ("사용처", "제외", "제한", "불가", "가능"))

        segments = [
            self._sentenceize_fact(self._welfare_hook_line(label, bucket=bucket)),
            self._compose_short_segment(
                "먼저 대상은 ",
                target_fact,
                preset_key=preset_key,
                fallback="누가 해당되는지부터 바로 짚어드리겠습니다",
            ),
            self._compose_short_segment(
                "받을 수 있는 내용은 ",
                benefit_fact,
                preset_key=preset_key,
                fallback="이번 혜택이 생활비 부담을 얼마나 덜어주는지가 핵심입니다",
            ),
        ]
        if application_fact:
            segments.append(
                self._compose_short_segment(
                    "신청 방법은 ",
                    application_fact,
                    preset_key=preset_key,
                    fallback="온라인과 방문 접수 가운데 어디로 신청하는지 먼저 확인하는 것이 좋습니다",
                )
            )
        elif restriction_fact:
            segments.append(
                self._compose_short_segment(
                    "같이 확인할 부분은 ",
                    restriction_fact,
                    preset_key=preset_key,
                    fallback="이번 지원은 사용 가능한 곳과 제외되는 경우도 함께 봐야 합니다",
                )
            )
        else:
            segments.append(
                self._sentenceize_fact("이번 혜택은 누가 해당되는지와 함께 지급 방식까지 차근히 보는 것이 중요합니다")
            )
        if timing_fact:
            segments.append(
                self._compose_short_segment(
                    "놓치면 안 되는 시기는 ",
                    timing_fact,
                    preset_key=preset_key,
                    fallback="시행 시기와 마감 일정도 함께 챙겨두는 것이 좋습니다",
                )
            )
        segments.append(self._sentenceize_fact(self._welfare_cta_line()))

        detail_points = [
            self._sanitize_short_segment(target_fact, preset_key=preset_key),
            self._sanitize_short_segment(benefit_fact, preset_key=preset_key),
            self._sanitize_short_segment(application_fact or restriction_fact, preset_key=preset_key),
            self._sanitize_short_segment(timing_fact, preset_key=preset_key) if timing_fact else "",
        ]
        if not any(detail_points):
            detail_points = [
                self._sentenceize_fact(f"{label}에서 가장 중요한 건 누가 대상인지입니다"),
                self._sentenceize_fact("혜택 내용과 신청 창구를 한 번에 이해하는 것이 핵심입니다"),
                self._sentenceize_fact("오늘은 실제로 챙길 수 있는 순서대로만 짚어드리겠습니다"),
            ]
        detail_points = [fact for fact in detail_points if fact]

        tags = self._build_tags(topic, preset_key)
        description = self._apply_channel_description_rules(
            self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{label} 한 가지 주제를 기준으로 대상, 혜택, 신청 방법을 이해하기 쉽게 정리했습니다.",
            ),
            detail_points=detail_points,
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_title(topic),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=preset_key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="?⑤벊???類ｋ궖??獄쏅?源??곗쨮 ?癒?퍥 燁삳?諭??곷뮞???얜㈇??? ?????곷???닌딄쉐??됰뮸??덈뼄.",
            thumbnail_text=self._resolve_short_thumbnail_text(topic, detail_points),
        )

    @staticmethod
    def _welfare_hook_line(label: str, *, bucket: str) -> str:
        hook_map = {
            "application": f"오늘 복지 소식은 {label}입니다. 신청 기준부터 바로 짚어드리겠습니다",
            "eligibility": f"오늘 복지 소식은 {label}입니다. 어떤 분들이 해당되는지부터 보겠습니다",
            "payment": f"오늘 복지 소식은 {label}입니다. 실제 혜택이 어디까지인지부터 정리해드리겠습니다",
            "regional": f"오늘 복지 소식은 {label}입니다. 우리 지역에도 적용되는지부터 확인해보겠습니다",
        }
        return hook_map.get(bucket, f"오늘 복지 소식은 {label}입니다. 대상과 혜택을 차근차근 정리해드리겠습니다")

    def _welfare_cta_line(self) -> str:
        return "생활에 도움이 되는 복지 정보를 계속 쉽게 받으시려면 구독과 좋아요 부탁드립니다"

    def _build_welfare_content(self, topic: RankedTopic, details: list[TopicDetail]) -> GeneratedContent:
        facts: list[str] = []
        for item in details:
            for candidate in (item.summary, item.title):
                cleaned = self._clean_detail_fact(candidate).rstrip(".")
                if not cleaned or not self._is_practical_welfare_fact(cleaned):
                    continue
                if cleaned in facts:
                    continue
                facts.append(cleaned)
                break

        while len(facts) < 4:
            fallback = self._clean_detail_fact(topic.representative_title).rstrip(".")
            if not fallback or fallback in facts:
                break
            facts.append(fallback)

        label = self._welfare_policy_label(topic, facts)
        preset_key = self._preset().key
        bucket = self._short_topic_bucket(topic, facts)
        target_fact = self._pick_welfare_fact(
            facts,
            ("대상", "연령", "소득", "가구", "차상위", "수급자", "어르신", "청년", "시민", "주민", "취약계층"),
        )
        benefit_fact = self._pick_welfare_fact(
            facts,
            ("지급", "지원", "금액", "혜택", "바우처", "수당", "연금", "감면", "환급", "급여", "최대", "만원"),
        )
        application_fact = self._pick_welfare_fact(
            facts,
            ("신청", "접수", "복지로", "정부24", "주민센터", "행정복지센터", "홈페이지", "온라인", "방문"),
        )
        timing_fact = self._pick_welfare_fact(facts, ("마감", "시행", "기한", "기간", "예산", "추경", "부터", "까지"))
        restriction_fact = self._pick_welfare_fact(facts, ("사용처", "제외", "제한", "불가", "가능"))

        target_line = self._welfare_target_phrase(facts, target_fact)
        benefit_line = self._welfare_benefit_phrase(facts, benefit_fact)
        access_line = self._welfare_access_phrase(facts, application_fact, restriction_fact, timing_fact)
        timing_line = self._welfare_timing_phrase(facts, timing_fact)

        segments = [
            self._sentenceize_fact(self._welfare_hook_line(label, bucket=bucket)),
            target_line,
            benefit_line,
            access_line,
        ]
        if timing_line:
            segments.append(timing_line)
        segments.append(self._sentenceize_fact(self._welfare_cta_line()))

        detail_points = [
            self._sentenceize_fact(re.sub(r"^먼저 대상은\s*", "대상은 ", target_line).rstrip(".")),
            self._sentenceize_fact(re.sub(r"^받을 수 있는 혜택은\s*", "혜택은 ", benefit_line).rstrip(".")),
            self._sentenceize_fact(re.sub(r"^(신청이나 확인은|신청은|이번 지원은)\s*", "확인 포인트는 ", access_line).rstrip(".")),
        ]
        if timing_line:
            detail_points.append(
                self._sentenceize_fact(re.sub(r"^(지급 시기는|시기는|놓치면 안 되는 시기는)\s*", "시기는 ", timing_line).rstrip("."))
            )
        detail_points = unique_preserve_order([fact for fact in detail_points if fact])
        if not detail_points:
            detail_points = [
                self._sentenceize_fact(f"대상은 {label} 혜택이 실제로 적용되는 분들입니다"),
                self._sentenceize_fact("혜택은 금액과 지급 방식까지 함께 이해하는 것이 핵심입니다"),
                self._sentenceize_fact("확인 포인트는 신청 창구와 시행 시기를 먼저 챙기는 것입니다"),
            ]

        tags = self._build_tags(topic, preset_key)
        description = self._apply_channel_description_rules(
            self._build_description(
                topic=topic,
                detail_points=detail_points,
                tags=tags,
                summary_intro=f"{label} 한 가지 주제를 기준으로 대상, 혜택, 지급 흐름을 이해하기 쉽게 정리했습니다.",
            ),
            detail_points=detail_points,
        )
        return GeneratedContent(
            topic=topic,
            video_title=self._build_welfare_video_title(label, benefit_line, access_line),
            script=" ".join(segments),
            description=description,
            tags=tags,
            segments=segments,
            content_format="short",
            detail_points=detail_points,
            estimated_duration_seconds=self.config.generation.target_duration_seconds,
            preset_key=preset_key,
            background_prompt=self._build_background_prompt(topic),
            thumbnail_prompt=self._build_thumbnail_prompt(topic),
            contains_synthetic_media=decide_contains_synthetic_media(self.config, topic=topic),
            altered_content_reason="공적 근거가 있는 최신 복지 이슈를 바탕으로 대상, 혜택, 지급 흐름 중심의 자체 설명형 대본으로 재구성했습니다.",
            thumbnail_text=self._resolve_short_thumbnail_text(topic, detail_points),
        )

    def _finalize_short_content(self, content: GeneratedContent, details: list[TopicDetail]) -> GeneratedContent:
        target_seconds = max(24, int(self.config.generation.target_duration_seconds * 0.8))
        preset_key = self._preset().key
        if self._preset().collection_mode == "news" and preset_key != "welfare_news":
            news_content = self._build_news_content(content.topic, details)
            detail_point_segments = self._news_segments_from_detail_points(content.topic, content.detail_points)
            chosen_segments = detail_point_segments if len(detail_point_segments) >= 5 else news_content.segments
            news_content.segments = chosen_segments
            news_content.script = " ".join(chosen_segments)
            news_content.estimated_duration_seconds = max(
                news_content.estimated_duration_seconds,
                self._estimate_seconds(chosen_segments),
            )
            content.segments = news_content.segments
            content.script = news_content.script
            content.detail_points = news_content.detail_points
            content.description = news_content.description
            content.video_title = news_content.video_title
            content.thumbnail_text = news_content.thumbnail_text
            content.thumbnail_prompt = news_content.thumbnail_prompt
            content.background_prompt = news_content.background_prompt
            content.estimated_duration_seconds = max(
                content.estimated_duration_seconds,
                self._estimate_seconds(news_content.segments),
            )
            return content

        segments = [
            cleaned
            for cleaned in (
                self._sanitize_short_segment(segment, preset_key=preset_key)
                for segment in (content.segments or [content.script])
            )
            if cleaned
        ]

        if len(segments) < 4:
            rebuilt = self._fallback_short_content_for_preset(content.topic, details, preset_key=preset_key)
            content = rebuilt
            segments = [
                cleaned
                for cleaned in (
                    self._sanitize_short_segment(segment, preset_key=preset_key)
                    for segment in rebuilt.segments
                )
                if cleaned
            ]

        if preset_key != "welfare_news":
            detail_summaries = [
                fact
                for fact in (self._clean_detail_fact(item.summary or item.title) for item in details)
                if fact
            ]
            keywords = ", ".join(content.topic.keywords[:4] or [content.topic.representative_title])

            while self._estimate_seconds(segments) < target_seconds:
                extra = self._sanitize_short_segment(
                    self._supplementary_segment(
                        next_index=len(segments),
                        content=content,
                        detail_summaries=detail_summaries,
                        keywords=keywords,
                    ),
                    preset_key=preset_key,
                )
                if not extra or extra in segments:
                    break
                segments.append(extra)
                if len(segments) >= 8:
                    break

        content.segments = segments
        content.script = " ".join(segments)
        content.estimated_duration_seconds = max(content.estimated_duration_seconds, self._estimate_seconds(segments))
        return content

    def _supplementary_segment(
        self,
        *,
        next_index: int,
        content: GeneratedContent,
        detail_summaries: list[str],
        keywords: str,
    ) -> str:
        preset_key = self._preset().key
        if preset_key == "quotes_daily":
            fallbacks = [
                "지금 필요한 건 오래 버티는 말보다 마음을 붙잡아 주는 한 문장입니다.",
                "오늘 필요한 건 거창한 위로보다 바로 붙잡히는 현실적인 한마디일 수 있습니다.",
                "짧은 문장 하나가 흐트러진 마음을 다시 세우는 때도 있습니다.",
            ]
            return fallbacks[next_index % len(fallbacks)]

        if preset_key == "welfare_news":
            return ""

        if self._preset().collection_mode == "news":
            fallbacks = [
                "지금은 자극적인 해석보다 실제 발표와 변화 흐름을 차분하게 보는 것이 중요합니다.",
                "숫자 하나보다 그 숫자가 어디까지 이어지는지가 이번 이슈의 핵심입니다.",
                "추가 발표가 붙으면 시장 해석도 달라질 수 있어 마지막 기준까지 확인이 필요합니다.",
            ]
            return fallbacks[next_index % len(fallbacks)]

        fallbacks = [
            f"{self._short_topic_label(content.topic)} 관련 후속 발표와 세부 수치는 더 확인할 부분입니다.",
            "핵심 쟁점은 그대로지만 세부 해석은 추가 발표에 따라 달라질 수 있습니다.",
            "지금은 배경과 후속 조치를 함께 보는 흐름이 이어지고 있습니다.",
        ]
        return fallbacks[next_index % len(fallbacks)]

    def _resolve_description(
        self,
        description: str,
        topic: RankedTopic,
        detail_points: list[str],
        tags: list[str],
    ) -> str:
        manual_description = self.config.active_channel.manual_description.strip() if self.config.active_channel else ""
        if manual_description:
            return manual_description

        safe_points = self._sanitize_description_points(detail_points)
        label = self._short_topic_label(topic)
        if self._preset().collection_mode == "news":
            if self._preset().key == "welfare_news":
                intro = f"{label} 정보를 대상, 혜택, 신청 포인트 순서로 짧고 쉽게 정리했습니다."
            else:
                intro = f"{label} 이슈를 한 번에 이해할 수 있게 핵심만 정리했습니다."
            base = self._build_description(
                topic=topic,
                detail_points=safe_points or [label],
                tags=tags,
                summary_intro=intro,
            )
        else:
            base = description or self._build_description(
                topic=topic,
                detail_points=safe_points or detail_points,
                tags=tags,
                summary_intro=f"{label} 이슈를 짧고 자연스럽게 이해할 수 있도록 핵심만 정리했습니다.",
            )
        return self._apply_channel_description_rules(base, detail_points=safe_points or detail_points)

    def _apply_channel_description_rules(self, description: str, *, detail_points: list[str]) -> str:
        text = str(description or "").strip()
        clean_points = [
            point
            for point in self._sanitize_description_points(detail_points)
            if point
            and "검증에 사용한 핵심 사실 포인트" not in point
            and "공식 발표문" not in point
            and "원문 확인 필요" not in point
        ]
        hashtags = self._extract_hashtags(text)
        if self._preset().collection_mode != "stories" and "#shorts" not in [tag.lower() for tag in hashtags]:
            hashtags.append("#shorts")

        if self._preset().collection_mode != "news":
            return text

        blocked_markers = (
            "검증에 사용한 핵심 사실 포인트",
            "공식 발표문",
            "원문 확인 필요",
            "공식 사이트",
            "출처",
            "v.daum.net",
            "mk.co.kr",
            "nate.com",
            "네이트",
        )
        lead = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line == self.config.generation.channel_name:
                continue
            if line.startswith("[") or line.startswith("#"):
                continue
            if any(marker.lower() in line.lower() for marker in blocked_markers):
                continue
            cleaned = self._clean_detail_fact(self._strip_source_like_tokens(line)).rstrip(".")
            if cleaned:
                lead = cleaned + "."
                break

        if not lead:
            if self._preset().key == "welfare_news":
                lead = "이번 복지 정보를 대상, 혜택, 신청 순서로 이해하기 쉽게 정리했습니다."
            else:
                lead = "이번 뉴스 이슈를 한 번에 이해할 수 있게 핵심만 정리했습니다."

        blocks = [
            self.config.generation.channel_name,
            "",
            lead,
        ]
        if clean_points:
            point_limit = 4 if self._preset().key == "welfare_news" else 3
            blocks.extend(["", "[핵심 정리]", *[f"- {point.rstrip('.')}" for point in clean_points[:point_limit]]])
        if self._preset().key == "welfare_news":
            blocks.extend(["", "오늘 영상이 도움이 되셨다면 구독과 좋아요 부탁드립니다."])
        else:
            blocks.extend(["", self.config.generation.call_to_action])
        if hashtags:
            blocks.extend(["", " ".join(hashtags[: self.config.generation.max_tags])])
        return "\n".join(blocks).strip()
