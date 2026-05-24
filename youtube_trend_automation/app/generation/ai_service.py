from __future__ import annotations

import base64
import hashlib
import http.client
import json
from io import BytesIO
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from app.config import AppConfig
from app.models import RankedTopic, TopicDetail
from app.studio.presets import preset_by_key
from app.utils.logging import get_logger

LOGGER = get_logger(__name__)


class OpenAIContentService:
    """Generate richer text and images with OpenAI when available."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def available(self) -> bool:
        return self.config.allow_network and self.config.ai.enabled and bool(self._api_key())

    def generate_briefing(
        self,
        *,
        topic: RankedTopic,
        details: list[TopicDetail],
    ) -> dict[str, Any] | None:
        if not self.config.ai.enabled or not self.config.ai.use_text_generation:
            return None

        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("economy_news")
        detail_lines = "\n".join(
            f"- 제목: {item.title}\n  요약: {item.summary}"
            for item in details[:4]
        )
        prompt = self._build_shorts_prompt(
            preset=preset,
            topic=topic,
            detail_lines=detail_lines,
        )
        return self._generate_json(prompt)

    def generate_story_package(
        self,
        *,
        topic: RankedTopic,
        details: list[TopicDetail],
    ) -> dict[str, Any] | None:
        if not self.config.ai.enabled or not self.config.ai.use_text_generation:
            return None

        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key(
            "senior_story_longform"
        )
        scene_count = max(1, int(self.config.generation.story_scene_count))
        hook_seconds = max(20, min(40, int(self.config.generation.hook_duration_seconds or 40)))
        detail_lines = "\n".join(
            f"- title: {item.title}\n  summary: {item.summary}"
            for item in details[:6]
        )
        prompt = f"""
You write Korean longform YouTube stories for viewers in their 50s and 60s.
Return one JSON object only.

Channel name: {self.config.generation.channel_name or '황금시간의기록'}
Preset: {preset.label}
Topic seed: {topic.representative_title}
Audience: {self.config.generation.audience_hint or 'Korean viewers in their 50s and 60s'}
Target runtime: about {self.config.generation.target_duration_seconds // 60} minutes
Exact scene count: {scene_count}
Hook length: {hook_seconds} to 40 seconds
Extra channel notes: {self.config.active_channel.extra_instructions if self.config.active_channel else ""}

Reference details:
{detail_lines}

Rules:
- Write one single fictional story only.
- All title, description, thumbnail_text, hook_script, summaries, and narrations must be in Korean.
- Each image_prompt must be in English.
- No production notes, no scene labels inside narration, no viewer guidance, no policy wording.
- The hook must open with the strongest emotional rupture or conflict.
- Every scene must advance the plot with new events, new clues, new reactions, or new dialogue turns.
- Do not repeat the same paragraph or the same emotional summary across scenes.
- Keep the story easy to follow, emotionally strong, and natural for senior listeners.
- The final package should comfortably reach 59 minutes 30 seconds to 61 minutes 30 seconds in Korean TTS.
- Each scene narration should be long enough for that total runtime, with concrete everyday details and clear progression.
- thumbnail_text should feel highly clickable but dignified, 10 to 18 Korean characters.
- thumbnail_prompt should describe a photoreal Korean emotional thumbnail with close-up emotion, dramatic lighting, and no text.
- hook_image_prompt and every image_prompt must include 16:9, photorealistic Korean everyday life, no text, no captions, no poster.
- altered_content_answer should be "yes".

Required fields:
title, description, tags, thumbnail_text, thumbnail_prompt, background_prompt, hook_title, hook_script, hook_image_prompt, scenes, altered_content_answer, altered_content_reason

Each item in scenes must contain:
- title
- summary
- narration
- image_prompt

Narration length guide:
- hook_script: roughly 350 to 650 Korean characters
- each scene narration: roughly 2200 to 3200 Korean characters
"""
        return self._generate_json(prompt)

        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("senior_story_longform")
        scene_count = max(1, int(self.config.generation.story_scene_count))
        hook_minutes = max(1, int(self.config.generation.hook_duration_seconds / 60))
        detail_lines = "\n".join(
            f"- 제목: {item.title}\n  요약: {item.summary}\n  출처: {item.source}"
            for item in details[:8]
        )
        prompt = f"""
당신은 50대~60대 시청자 대상의 한국어 유튜브 롱폼 영상 전문 제작 AI입니다.
채널명은 "황금시간의기록"이며, 반드시 JSON 객체 하나만 반환하세요.

[채널 정보]
- 채널명: 황금시간의기록
- 현재 표시 채널명: {self.config.generation.channel_name}
- 프리셋: {preset.label}
- 주제 시드: {topic.representative_title}
- 타깃 시청자: {self.config.generation.audience_hint or '50대~60대 시니어 시청자'}
- 목표 길이: 약 {self.config.generation.target_duration_seconds // 60}분
- 목표 실제 길이: 59분 30초 ~ 61분 30초
- 장면 수: 정확히 {scene_count}개
- 훅 구간: 약 {hook_minutes}분
- 추가 지시: {self.config.active_channel.extra_instructions if self.config.active_channel else ""}

[상세 자료]
{detail_lines}

[핵심 제작 규칙]
1. 영상에는 반드시 한 가지 이야기만 사용합니다. 다른 사건이나 다른 이야기로 넘어가지 마세요.
2. 국내 일상 기반 소재를 우선합니다. 가족, 이웃, 돈 문제, 오해, 배신, 효도, 자존심, 반전 같은 공감형 소재를 선호합니다.
3. 자극적이되 저속하지 않게 구성합니다. 선정성, 노골적 성적 표현, 과도한 폭력, 혐오, 실존 인물 비방은 금지입니다.
4. 실화처럼 보이는 기사 요약이 아니라, 주제 시드를 바탕으로 한 자연스러운 오리지널 스토리 콘텐츠여야 합니다.
5. hook_script는 영상 초반 약 {hook_minutes}분 분량이며, 시작 20~40초 안에 가장 강한 갈등이나 감정 폭발 직전 장면을 먼저 보여줘야 합니다.
6. 훅 뒤에는 본 이야기로 자연스럽게 진입해야 하며, 2~3분 간격으로 작은 반전, 감정 포인트, 궁금증 장치를 계속 넣으세요.
7. scenes는 정확히 {scene_count}개이며, 도입-전개-갈등심화-반전/사이다-정리 흐름이 분명해야 합니다.
8. 각 scene.narration은 제작 지시문 없이 바로 TTS에 넣을 수 있는 자연스러운 한국어 나레이션이어야 하며, 각 씬이 충분히 길어서 전체 합이 59분 30초~61분 30초에 가깝게 나오도록 써야 합니다.
9. narration에는 "지금 화면에는", "1번 장면", "이 장면에서는" 같은 제작용 문장을 절대 넣지 마세요.
10. 문장은 쉬운 한국어, 안정적인 호흡, 명확한 감정선으로 작성하세요. 너무 빠르거나 복잡한 표현은 금지입니다.
11. 영상 도입과 마무리에는 채널명 "황금시간의기록"을 자연스럽게 포함하고, 구독/좋아요/알림 유도는 따뜻하고 과하지 않게 넣으세요.
12. 마지막 scene의 후반부는 감정적으로 정리되며 다음 이야기가 궁금해지는 여운을 남겨야 합니다.
13. 각 scene.narration에는 실제 사건, 대화, 행동, 생활 디테일을 풍부하게 넣고, 감상문·해설문처럼 같은 문장을 반복하지 마세요.

[비주얼 / 자막 규칙]
1. 모든 scene.image_prompt와 hook_image_prompt는 반드시 영어로 작성합니다.
2. 모든 이미지 프롬프트는 16:9 landscape 영상용입니다.
3. 한국 일상 분위기를 우선하고, 아파트, 골목, 시장, 식당, 병원, 가족 거실, 회사, 동네 풍경 같은 생활형 배경을 적극 반영합니다.
4. 이미지 프롬프트에는 인물 외형, 감정, 장소, 시간대, 조명, 카메라 구도, 분위기를 구체적으로 넣고, 동일 인물은 외형 일관성을 유지하세요.
5. 너무 만화 같거나 장난스러운 비주얼은 금지하고, 현실적이고 감정이 느껴지는 포토리얼 스타일을 우선합니다.
6. narration은 자막으로 전부 옮기기 쉬운 군더더기 문장보다, 이해가 쉬운 짧고 선명한 문장 중심으로 작성하세요.

[출력 키 규칙]
반드시 아래 키만 사용합니다:
title, description, tags, thumbnail_text, thumbnail_prompt, background_prompt, hook_title, hook_script, hook_image_prompt, scenes, altered_content_answer, altered_content_reason

[필드 작성 규칙]
1. title은 시니어 시청자가 쉽게 이해하면서도 클릭하고 싶어지는 한국어 제목 1개입니다.
2. description은 유튜브 설명란에 바로 넣을 수 있는 자연스러운 한국어 설명입니다.
3. thumbnail_text는 짧고 강한 한국어 문구 1개이며 24자 이내입니다.
4. thumbnail_prompt와 background_prompt는 영어 프롬프트입니다.
5. altered_content_answer는 "yes"로 작성합니다.
6. altered_content_reason에는 AI 나레이션, AI 이미지, 재현형 연출 사용 이유를 짧게 설명합니다.

[scenes 형식]
[
  {{
    "title": "씬 제목",
    "summary": "한두 문장 요약",
    "narration": "길고 자연스러운 한국어 나레이션 본문",
    "image_prompt": "16:9 cinematic photoreal prompt for Korean everyday life story ..."
  }}
]
"""
        return self._generate_json(prompt)

    def generate_image(self, *, prompt: str, output_path: Path) -> bool:
        normalized_prompt = self._normalize_prompt(prompt)
        if not self.config.ai.enabled or not self.config.ai.use_image_generation or not normalized_prompt:
            return False

        cached_path = self._image_cache_path(normalized_prompt)
        if cached_path.exists() and cached_path.stat().st_size > 0:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached_path, output_path)
            return True

        if self._should_use_free_story_images():
            if self._generate_free_story_image(prompt=normalized_prompt, output_path=output_path, cached_path=cached_path):
                return True
            if not self._allow_paid_story_image_fallback():
                return False

        if not self.available():
            return False

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("OpenAI SDK unavailable for images: %s", exc)
            return False

        try:
            client = OpenAI(api_key=self._api_key())
            response = client.responses.create(
                model=self.config.ai.image_model,
                input=normalized_prompt,
                tools=[{"type": "image_generation"}],
            )
            image_data = _extract_image_base64(response)
            if not image_data:
                LOGGER.warning("OpenAI image response had no image payload.")
                return False
            image_bytes = base64.b64decode(image_data)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(image_bytes)
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            if not cached_path.exists():
                cached_path.write_bytes(image_bytes)
            return True
        except Exception as exc:  # pragma: no cover - external path
            LOGGER.warning("OpenAI image generation failed: %s", exc)
            return False

    def _should_use_free_story_images(self) -> bool:
        if not self.config.allow_network:
            return False
        channel = self.config.active_channel
        if channel is None:
            return False
        preset = preset_by_key(channel.preset_key)
        return preset.content_format == "longform_story"

    @staticmethod
    def _allow_paid_story_image_fallback() -> bool:
        return os.getenv("YTA_ALLOW_PAID_STORY_IMAGE_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}

    def _generate_free_story_image(self, *, prompt: str, output_path: Path, cached_path: Path) -> bool:
        width = max(1280, int(self.config.render.width or 1280))
        height = max(720, int(self.config.render.height or 720))
        encoded_prompt = urllib.parse.quote(prompt, safe="")
        url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width={width}&height={height}&model=flux&nologo=true"
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/*",
            },
        )
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=75) as response:
                    content_type = (response.headers.get("Content-Type", "") or "").lower()
                    raw_bytes = response.read()
                if not content_type.startswith("image/") or len(raw_bytes) < 512:
                    LOGGER.warning(
                        "Free story image provider returned unexpected payload on attempt %s.",
                        attempt + 1,
                    )
                    time.sleep(1.2 * (attempt + 1))
                    continue
                image = Image.open(BytesIO(raw_bytes)).convert("RGB")
                image = self._sanitize_free_story_image(image)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(output_path, format="PNG", optimize=True)
                cached_path.parent.mkdir(parents=True, exist_ok=True)
                if not cached_path.exists():
                    image.save(cached_path, format="PNG", optimize=True)
                return True
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.IncompleteRead) as exc:
                LOGGER.warning(
                    "Free story image provider failed on attempt %s: %s",
                    attempt + 1,
                    exc,
                )
                time.sleep(1.4 * (attempt + 1))
        return False

    @staticmethod
    def _sanitize_free_story_image(image: Image.Image) -> Image.Image:
        width, height = image.size
        trim_height = max(0, int(height * 0.045))
        if trim_height <= 0:
            return image
        cropped = image.crop((0, 0, width, height - trim_height))
        return cropped.resize((width, height), Image.Resampling.LANCZOS)

    def _generate_json(self, prompt: str) -> dict[str, Any] | None:
        normalized_prompt = self._normalize_prompt(prompt)
        if not normalized_prompt:
            return None

        cached_path = self._text_cache_path(normalized_prompt)
        if cached_path.exists():
            try:
                return json.loads(cached_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached_path.unlink(missing_ok=True)

        if not self.available():
            return None

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("OpenAI SDK unavailable: %s", exc)
            return None

        try:
            client = OpenAI(api_key=self._api_key())
            response = client.responses.create(
                model=self.config.ai.text_model,
                input=normalized_prompt,
            )
            output_text = getattr(response, "output_text", "") or ""
            payload = _extract_json_object(output_text)
            if payload is None:
                LOGGER.warning("OpenAI response was not valid JSON.")
                return None
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            cached_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload
        except Exception as exc:  # pragma: no cover - external path
            LOGGER.warning("OpenAI text generation failed: %s", exc)
            return None

    @staticmethod
    def _api_key() -> str:
        return os.getenv("OPENAI_API_KEY", "")

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        return re.sub(r"\s+", " ", prompt or "").strip()

    def _text_cache_path(self, prompt: str) -> Path:
        return self.config.openai_text_cache_dir / f"{self._cache_key('text', self.config.ai.text_model, prompt)}.json"

    def _image_cache_path(self, prompt: str) -> Path:
        return self.config.openai_image_cache_dir / f"{self._cache_key('image', self.config.ai.image_model, prompt)}.png"

    def _cache_key(self, kind: str, model: str, prompt: str) -> str:
        channel_id = self.config.active_channel.id if self.config.active_channel else "default"
        digest = hashlib.sha256(f"{kind}|{channel_id}|{model}|{prompt}".encode("utf-8")).hexdigest()
        return digest[:24]

    def _build_shorts_prompt(
        self,
        *,
        preset,
        topic: RankedTopic,
        detail_lines: str,
    ) -> str:
        channel_specific_rules = self._channel_specific_shorts_rules(preset.key)
        return f"""
당신은 여러 개의 유튜브 Shorts 채널을 동시에 운영하는 전문 AI 콘텐츠 디렉터입니다.
반드시 JSON 객체 하나만 반환하세요.

[공통 절대 원칙]
1. 모든 영상은 YouTube Shorts 전용입니다.
2. 화면 비율은 반드시 9:16 세로형입니다.
3. 영상 길이는 기본 25초~55초, 최대 60초 이내입니다.
4. 첫 1~2초 안에 강한 훅을 배치합니다.
5. 영상 하나당 메시지는 반드시 1개만 전달합니다.
6. 대본은 짧고 이해 쉬운 한국어로, 한 문장 한 메시지 원칙을 지킵니다.
7. 자막은 모바일에서 읽기 쉽게 중하단 중심으로 구성합니다.
8. 제목은 짧고 강하게, 설명은 검색 키워드와 맥락 중심으로 작성합니다.
9. 기사/타 채널 문장을 그대로 베끼지 말고 완전히 새로 재구성합니다.
10. 허위정보, 과장광고, 명예훼손 위험 표현, 재사용 콘텐츠형 구성을 피합니다.

[채널 정보]
- 채널명: {self.config.generation.channel_name}
- 프리셋: {preset.label}
- 주제: {topic.representative_title}
- 목표 길이: {self.config.generation.target_duration_seconds}초
- 추가 지시: {self.config.active_channel.extra_instructions if self.config.active_channel else ""}

[상세 자료]
{detail_lines}

[공통 작성 규칙]
1. title은 짧고 강한 한국어 제목 1개입니다.
2. description은 유튜브 설명란에 그대로 넣을 수 있게 작성합니다.
3. segments는 5~8개로 만들고, 각 항목은 1문장 위주로 짧게 작성합니다.
4. detail_points는 핵심 포인트 3~5개입니다.
5. thumbnail_text는 24자 이내입니다.
6. background_prompt와 thumbnail_prompt는 이미지 생성용 영어 프롬프트로 작성합니다.
7. thumbnail_prompt와 background_prompt는 AI 카드뉴스/자체 생성 스타일로, 타사 기사 스크린샷 복제처럼 보이면 안 됩니다.
8. altered_content_answer는 AI 이미지/내레이션 사용 여부에 맞게 판단하되, 재현형 인물 이미지나 합성형 카드뉴스면 기본적으로 "yes"를 우선 검토합니다.
9. 반드시 아래 키만 사용합니다:
title, description, tags, segments, detail_points, thumbnail_text, thumbnail_prompt, background_prompt, altered_content_answer, altered_content_reason

[채널 전용 규칙]
{channel_specific_rules}
"""

    @staticmethod
    def _channel_specific_shorts_rules(preset_key: str) -> str:
        if preset_key == "welfare_news":
            return """
1. 반드시 최신 복지/지원금/생활혜택 정보를 우선 반영합니다.
2. 제도명, 대상자, 신청 조건, 신청 방법, 시행/마감 시기를 확인 가능한 범위에서 분리해 요약합니다.
3. 첫 문장은 "해당되면 바로 챙겨야 하는 혜택"처럼 실익을 먼저 제시합니다.
4. 금액, 날짜, 대상 조건은 쉬운 말로 풀고, "무조건 지급"처럼 단정하지 않습니다.
5. 지역 한정 제도면 지역 한정임을 분명히 적습니다.
6. 마지막 문장은 반드시 "세부 요건은 공식 공고문 확인 필요" 취지로 마무리합니다.
7. 썸네일 문구는 금액, 대상자, 마감 중 하나가 바로 보이게 작성합니다.
"""
        if preset_key == "quotes_daily":
            return """
1. 반응이 높을 감정 주제를 먼저 잡고, 명언 하나가 아니라 "지금 상황에 꽂히는 한 메시지"로 구성합니다.
2. 너무 흔한 문장을 그대로 반복하지 말고, 현대어 재해석 또는 실전형 통찰 문장으로 새롭게 씁니다.
3. 첫 문장은 찔리는 한 줄, 둘째 문장은 왜 중요한지, 셋째 문장은 현실 감정 연결, 마지막은 저장하고 싶은 문장으로 구성합니다.
4. 길이는 20~40초 감각으로 짧고 리듬감 있게 유지합니다.
5. 출처가 불명확한 문구는 특정 인물 명언처럼 단정하지 않습니다.
6. 비주얼은 도시 야경, 실내 조명, 창가, 뒷모습, 일상 감성 배경 중심으로 구성합니다.
"""
        return """
1. 반드시 오늘 기준 최신 뉴스/이슈를 우선 반영합니다.
2. 실제 뉴스 전문 아나운서가 차분하고 또렷하게 말하듯 자연스러운 한국어 브리핑 톤으로 작성합니다.
3. 첫 문장은 뉴스 제목의 핵심 결과나 변화부터 바로 말하고, "나한테 영향", "내 생활에 영향" 같은 표현은 쓰지 않습니다.
4. 30~50초 안에 하나의 주제만 선택해서, 무슨 일이 일어났는지, 왜 주목받는지, 지금 무엇을 같이 봐야 하는지를 일반인이 바로 이해할 수 있게 쉬운 말로 풀어 설명합니다.
5. 확인 안 된 추측은 금지하고, 확정 전 정보는 "검토 중", "발표 예정", "가능성"처럼 구분합니다.
6. 공공 관심도와 실익이 높은 생활 밀착형 사회 이슈, 정책 변화, 소비자 관심 이슈를 우선합니다.
7. 기사 제목을 여러 개 읽거나 출처명을 직접 말하지 말고, 선택된 주제 한 가지를 자연스러운 뉴스 브리핑으로 재구성합니다.
8. "자세한 내용은 확인 필요", "공식 발표문/원문 확인 필요" 같은 식상한 마무리 문장은 쓰지 않습니다.
9. 마지막 문장은 자연스럽게 "구독과 좋아요"를 포함한 뉴스 채널 클로징으로 끝냅니다.
10. 썸네일 문구는 3~6단어 이내, 제목과 중복되지 않게 작성합니다.
"""

    def generate_story_package(
        self,
        *,
        topic: RankedTopic,
        details: list[TopicDetail],
    ) -> dict[str, Any] | None:
        if not self.config.ai.enabled or not self.config.ai.use_text_generation:
            return None

        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key("senior_story_longform")
        scene_count = max(1, int(self.config.generation.story_scene_count))
        hook_seconds = max(20, min(40, int(self.config.generation.hook_duration_seconds or 40)))
        detail_lines = "\n".join(
            f"- 제목: {item.title}\n  요약: {item.summary}"
            for item in details[:6]
        )
        prompt = f"""
당신은 50대~60대 시청자 대상의 한국어 유튜브 롱폼 영상 전문 제작 AI입니다.
채널명은 "황금시간의기록"입니다.
반드시 JSON 객체 하나만 반환하세요.

[채널 정보]
- 채널명: 황금시간의기록
- 현재 표시 채널명: {self.config.generation.channel_name}
- 프리셋: {preset.label}
- 주제 시드: {topic.representative_title}
- 대상 시청자: {self.config.generation.audience_hint or '50대~60대 시니어 시청자'}
- 목표 길이: 약 {self.config.generation.target_duration_seconds // 60}분
- 목표 실제 길이: 59분 30초 ~ 61분 30초
- 장면 수: 정확히 {scene_count}개
- 훅 길이: 20초 ~ {hook_seconds}초
- 추가 지시: {self.config.active_channel.extra_instructions if self.config.active_channel else ""}

[상세 자료]
{detail_lines}

[필수 규칙]
1. 한 영상에는 반드시 하나의 이야기만 사용합니다.
2. 제작 설명, 정책 문구, 장면 번호 설명, 시청자에게 설명하는 메타 문장은 절대 넣지 마세요.
3. narration에는 "지금 화면에는", "이 장면에서는", "시청자는", "장면별로", "후킹", "오프닝" 같은 문구를 넣지 마세요.
4. hook_script는 영상 시작 20초~{hook_seconds}초 안에 들어갈 강한 갈등 장면만 짧고 선명하게 작성합니다.
5. scenes는 정확히 {scene_count}개이며, 각 scene.narration은 자연스러운 한국어 서술형 대본이어야 합니다.
6. 2~3분 간격으로 감정 포인트, 반전, 사이다, 궁금증 포인트가 이어지는 흐름으로 씁니다.
7. 선정적 표현, 과도한 폭력, 실존 인물 비방, 허위 사실처럼 보이는 표현은 금지합니다.
8. 전체 톤은 시니어가 편하게 들을 수 있게 쉽고 안정적인 한국어로 유지합니다.
9. image_prompt는 반드시 영어로 쓰되, 16:9 cinematic photorealistic Korean everyday life scene으로 작성하세요.
10. image_prompt에는 text, subtitle, caption, policy poster가 보이지 않도록 명시하세요.
11. thumbnail_text는 짧고 강하게 24자 이내로 작성하세요.
12. altered_content_answer는 "yes"로 작성하세요.

[반환 필드]
title, description, tags, thumbnail_text, thumbnail_prompt, background_prompt, hook_title, hook_script, hook_image_prompt, scenes, altered_content_answer, altered_content_reason
"""
        return self._generate_json(prompt)

    def expand_story_package(
        self,
        *,
        topic: RankedTopic,
        details: list[TopicDetail],
        hook_script: str,
        scenes: list[Any],
        current_thumbnail_text: str,
    ) -> dict[str, Any] | None:
        if not self.config.ai.enabled or not self.config.ai.use_text_generation:
            return None

        scene_count = max(1, int(self.config.generation.story_scene_count))
        detail_lines = "\n".join(
            f"- title: {item.title}\n  summary: {item.summary}"
            for item in details[:6]
        )
        scene_lines = "\n".join(
            (
                f"{index}. title: {getattr(scene, 'title', '')}\n"
                f"   summary: {getattr(scene, 'summary', '')}\n"
                f"   current_excerpt: {str(getattr(scene, 'narration', '')).strip()[:260]}"
            )
            for index, scene in enumerate(scenes[:scene_count], start=1)
        )
        prompt = f"""
Rewrite and expand this Korean longform YouTube story package.
Return one JSON object only.

Topic seed: {topic.representative_title}
Channel: {self.config.generation.channel_name or '황금시간의기록'}
Target runtime: about {self.config.generation.target_duration_seconds // 60} minutes
Scene count: {scene_count}
Current thumbnail text: {current_thumbnail_text}

Problems to fix:
- repeated paragraphs
- repeated emotional summaries
- not enough unique events
- thumbnail is not strong enough
- the setup feels too similar to other recent senior life-story videos

Reference details:
{detail_lines}

Current hook:
{hook_script[:700]}

Current scene map:
{scene_lines}

Rules:
- Keep one story only.
- All narration must be Korean and natural for senior listeners.
- No production notes, no viewer guidance, no scene numbering inside narration.
- Each scene must contain clearly different events from previous scenes.
- Add fresh concrete details, misunderstandings, discoveries, confrontation, and emotional release.
- Never repeat the same paragraph or the same sentence structure across scenes.
- Use only structural inspiration from high-performing Korean life-story videos that often reach hundreds of thousands to around one million views.
- Borrow pacing, title framing, hook strength, curiosity gaps, reversals, and emotional release patterns only.
- Never copy any actual video title, thumbnail phrase, opening line, scene order, plot twist, character setup, or dialogue from an existing YouTube video.
- Rotate away from overused "family sacrifice only" formulas and make this package feel materially different from generic senior melodrama.
- Prefer clear story buckets such as inheritance/property conflict, late-life romance or reunion, workplace or neighborhood humiliation and reversal, caregiving or health burden, retirement money or guarantee trouble, sibling conflict, or filial duty versus self-respect.
- Every scene must introduce a new action, new decision, new reveal, or a real relationship shift, not just another reflection about the same pain.
- Make thumbnail_text more clickable and dramatic, but still dignified.
- thumbnail_prompt must describe a photoreal Korean emotional thumbnail with close-up human emotion, strong contrast, and no text.
- Every image_prompt must be English, 16:9, photorealistic Korean everyday life, no text, no captions, no poster.
- altered_content_answer should be "yes".

Required fields:
title, description, tags, thumbnail_text, thumbnail_prompt, background_prompt, hook_title, hook_script, hook_image_prompt, scenes, altered_content_answer, altered_content_reason
"""
        return self._generate_json(prompt)

    def generate_story_package(
        self,
        *,
        topic: RankedTopic,
        details: list[TopicDetail],
    ) -> dict[str, Any] | None:
        if not self.config.ai.enabled or not self.config.ai.use_text_generation:
            return None

        preset = preset_by_key(self.config.active_channel.preset_key) if self.config.active_channel else preset_by_key(
            "senior_story_longform"
        )
        scene_count = max(1, int(self.config.generation.story_scene_count))
        hook_seconds = max(20, min(40, int(self.config.generation.hook_duration_seconds or 40)))
        detail_lines = "\n".join(
            f"- title: {item.title}\n  summary: {item.summary}"
            for item in details[:6]
        )
        prompt = f"""
You write Korean longform YouTube stories for viewers in their 50s and 60s.
Return one JSON object only.

Channel name: {self.config.generation.channel_name or '황금시간의기록'}
Preset: {preset.label}
Topic seed: {topic.representative_title}
Audience: {self.config.generation.audience_hint or 'Korean viewers in their 50s and 60s'}
Target runtime: about {self.config.generation.target_duration_seconds // 60} minutes
Exact scene count: {scene_count}
Hook length: {hook_seconds} to 40 seconds
Extra channel notes: {self.config.active_channel.extra_instructions if self.config.active_channel else ""}

Reference details:
{detail_lines}

Rules:
- Write one single fictional story only.
- All title, description, thumbnail_text, hook_script, summaries, and narrations must be in Korean.
- Each image_prompt must be in English.
- No production notes, no scene labels inside narration, no viewer guidance, no policy wording.
- Use only structural inspiration from high-performing Korean life-story videos that often reach hundreds of thousands to around one million views.
- Borrow only the broad patterns that make those videos work: emotionally precise title framing, a decisive hook inside the first 20 to 40 seconds, clear relationship stakes, fresh reveals in the middle, and a strong emotional payoff near the end.
- Never copy any real YouTube title, thumbnail phrase, script line, plot sequence, character setup, or scene progression from an existing video.
- Make this story feel original even if it belongs to a familiar genre.
- The hook must open with the strongest emotional rupture or conflict.
- Every scene must advance the plot with new events, new clues, new reactions, or new dialogue turns.
- Do not repeat the same paragraph or the same emotional summary across scenes.
- Rotate across different high-performing story buckets instead of repeating the same sacrifice-and-regret family template.
- Prefer materially different setups such as inheritance or property conflict, late-life romance or reunion, workplace or neighborhood humiliation and reversal, caregiving burden, retirement money trouble, sibling conflict, blended family tension, or self-respect after years of sacrifice.
- Every scene must contain a real state change: a decision, a reveal, a confrontation, a discovery, a loss, a recovery, or a relationship shift.
- Keep the story easy to follow, emotionally strong, and natural for senior listeners.
- The final package should comfortably reach 59 minutes 30 seconds to 61 minutes 30 seconds in Korean TTS.
- Each scene narration should be long enough for that total runtime, with concrete everyday details and clear progression.
- thumbnail_text should feel highly clickable but dignified, 10 to 18 Korean characters.
- thumbnail_prompt should describe a photoreal Korean emotional thumbnail with close-up emotion, dramatic lighting, and no text.
- hook_image_prompt and every image_prompt must include 16:9, photorealistic Korean everyday life, no text, no captions, no poster.
- altered_content_answer should be "yes".

Required fields:
title, description, tags, thumbnail_text, thumbnail_prompt, background_prompt, hook_title, hook_script, hook_image_prompt, scenes, altered_content_answer, altered_content_reason

Each item in scenes must contain:
- title
- summary
- narration
- image_prompt

Narration length guide:
- hook_script: roughly 350 to 650 Korean characters
- each scene narration: roughly 2200 to 3200 Korean characters
"""
        return self._generate_json(prompt)

    def _build_shorts_prompt(
        self,
        *,
        preset,
        topic: RankedTopic,
        detail_lines: str,
    ) -> str:
        channel_specific_rules = self._channel_specific_shorts_rules(preset.key)
        return f"""
당신은 여러 개의 유튜브 Shorts 채널을 동시에 운영하는 전문 AI 콘텐츠 디렉터입니다.
반드시 JSON 객체 하나만 반환하세요.

[공통 절대 원칙]
1. 모든 영상은 YouTube Shorts 전용입니다.
2. 화면 비율은 반드시 9:16 세로형입니다.
3. 영상 길이는 25초~55초를 우선하며 최대 60초 이내입니다.
4. 영상 하나당 메시지는 반드시 1개만 전달합니다.
5. 자막은 짧고 쉽게, 모바일에서 읽기 좋게 구성합니다.
6. 기사 문장, 타 채널 문구, 출처 문장을 그대로 베끼지 않습니다.
7. 제작 과정, 검색 과정, 분류 규칙, 프롬프트 설명, 출처 이름을 대본에 넣지 않습니다.
8. 타인의 영상 일부를 가져와 편집하는 재사용 전략을 쓰지 않습니다.
9. 뉴스/정보 콘텐츠는 사실 검증 후 제작하며 허위 정보와 단정 표현을 피합니다.
10. 결과물은 바로 자동 업로드 가능한 구조여야 합니다.

[채널 정보]
- 채널명: {self.config.generation.channel_name}
- 프리셋: {preset.label}
- 주제: {topic.representative_title}
- 목표 길이: {self.config.generation.target_duration_seconds}초
- 추가 지시: {self.config.active_channel.extra_instructions if self.config.active_channel else ""}

[상세 자료]
{detail_lines}

[공통 작성 규칙]
1. title은 짧고 강한 제목 1개입니다.
2. segments는 5~8개, 각 항목은 한 화면 한 메시지 원칙으로 짧게 씁니다.
3. detail_points는 핵심 사실 3~5개입니다.
4. description은 업로드용 설명문 그대로 작성합니다.
5. thumbnail_text는 24자 이내의 짧은 문구입니다.
6. background_prompt와 thumbnail_prompt는 저작권 안전한 자체 생성 카드뉴스 스타일의 영어 프롬프트입니다.
7. 기사 캡처, 언론사 로고, 화면 캡처 복제처럼 보이지 않게 작성합니다.
8. 반환 필드는 title, description, tags, segments, detail_points, thumbnail_text, thumbnail_prompt, background_prompt, altered_content_answer, altered_content_reason 만 사용합니다.

[채널 전용 규칙]
{channel_specific_rules}
"""

    @staticmethod
    def _channel_specific_shorts_rules(preset_key: str) -> str:
        if preset_key == "welfare_news":
            return """
1. 최신 복지, 지원금, 생활혜택 중 한 가지 주제만 선택해 설명합니다.
2. 첫 문장에서 실익을 먼저 말하고, 바로 대상자와 혜택 내용을 설명합니다.
3. 대본은 대상자, 혜택, 신청/확인 방법, 마감/시기를 순서대로 정리합니다.
4. 여러 제목을 나열하지 말고 한 가지 제도를 끝까지 설명합니다.
5. "무조건 지급" 같은 단정 표현은 금지하고, 조건과 예외가 있으면 분리해 설명합니다.
6. 마지막 문장은 적용 시기, 예외 조건, 확인해야 할 핵심 포인트로 마무리합니다.
"""
        if preset_key == "quotes_daily":
            return """
1. 오늘 반응이 높을 감정 주제를 먼저 잡고, 한 편에 한 메시지만 사용합니다.
2. 첫 문장은 지금 내 상황을 찌르는 한 줄이어야 합니다.
3. 대본은 명언 원문, 해석형 대사, 저장하고 싶은 마무리 한 줄 흐름으로 만듭니다.
4. 지나치게 오글거리거나 과장된 자기계발 문구는 금지합니다.
5. 특정 유명인의 명언처럼 단정할 수 없는 문장은 attribution 하지 않습니다.
"""
        return """
1. 오늘 기준 최신 뉴스/이슈만 우선 사용합니다.
2. 첫 문장은 "이 뉴스 나한테 영향" 같은 표현 없이 곧바로 핵심 사실로 시작합니다.
3. 대본은 실제 뉴스 내용만 보도하듯 자연스럽게 작성하고, 브리핑 과정 설명은 넣지 않습니다.
4. 연합뉴스, 뉴스1, 뉴시스 같은 출처 이름을 대본에 넣지 않습니다.
5. 30초~50초 안에 무슨 일이 일어났는지, 왜 화제인지, 지금 무엇을 보면 되는지 순서대로 정리합니다.
6. 마지막 문장은 자연스럽게 채널 클로징과 함께 구독과 좋아요를 부탁하는 멘트로 마무리합니다.
"""


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _extract_image_base64(response: Any) -> str | None:
    if hasattr(response, "output"):
        outputs = getattr(response, "output")
        for item in outputs:
            if getattr(item, "type", "") == "image_generation_call":
                return getattr(item, "result", None)

    if hasattr(response, "model_dump"):
        payload = response.model_dump()
        for item in payload.get("output", []):
            if item.get("type") == "image_generation_call":
                return item.get("result")
    return None
