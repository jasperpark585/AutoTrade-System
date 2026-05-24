from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PresetDefinition:
    key: str
    label: str
    group: str
    description: str
    collection_mode: str
    visual_style: str
    category_id: str
    title_prefix: str
    title_suffix: str
    call_to_action: str
    content_duration_seconds: int
    topic_include_keywords: tuple[str, ...] = ()
    topic_exclude_keywords: tuple[str, ...] = ()
    synthetic_media_default: bool = False
    content_format: str = "short"
    scene_count: int = 0
    hook_duration_seconds: int = 0
    audience_hint: str = ""


PRESET_DEFINITIONS: dict[str, PresetDefinition] = {
    "economy_news": PresetDefinition(
        key="economy_news",
        label="경제 뉴스 브리핑",
        group="news",
        description="증시, 금리, 부동산, 기업 이슈를 짧고 빠르게 요약하는 채널입니다.",
        collection_mode="news",
        visual_style="premium_news_graphic",
        category_id="25",
        title_prefix="[경제 브리핑]",
        title_suffix="",
        call_to_action="지금까지 NewsTrend였습니다. 도움이 되셨다면 구독과 좋아요 부탁드립니다.",
        content_duration_seconds=45,
        topic_include_keywords=("증시", "주가", "반도체", "금리", "부동산", "환율", "배당", "경제", "코스피"),
    ),
    "entertainment_news": PresetDefinition(
        key="entertainment_news",
        label="연예 뉴스 브리핑",
        group="news",
        description="연예, 방송, 영화, 화제 이슈를 짧고 선명하게 요약하는 채널입니다.",
        collection_mode="news",
        visual_style="premium_news_graphic",
        category_id="24",
        title_prefix="[연예 브리핑]",
        title_suffix="오늘 핵심 쇼츠",
        call_to_action="연예 소식을 빠르게 받고 싶다면 구독과 알림 설정을 눌러주세요.",
        content_duration_seconds=175,
        topic_include_keywords=("연예", "배우", "가수", "방송", "영화", "드라마", "아이돌", "예능"),
    ),
    "health_news": PresetDefinition(
        key="health_news",
        label="건강 뉴스 브리핑",
        group="news",
        description="건강, 의학, 진단, 생활 관리 이슈를 쉽게 설명하는 채널입니다.",
        collection_mode="news",
        visual_style="premium_news_graphic",
        category_id="27",
        title_prefix="[건강 브리핑]",
        title_suffix="오늘 핵심 3분",
        call_to_action="건강 정보를 놓치고 싶지 않다면 구독과 알림 설정을 눌러주세요.",
        content_duration_seconds=45,
        topic_include_keywords=("건강", "의학", "질환", "병원", "운동", "다이어트", "영양", "보험"),
    ),
    "welfare_news": PresetDefinition(
        key="welfare_news",
        label="복지 정보 브리핑",
        group="news",
        description="복지 정책, 지원금, 생활 혜택 변화를 빠르게 정리하는 채널입니다.",
        collection_mode="news",
        visual_style="premium_news_graphic",
        category_id="25",
        title_prefix="[복지 브리핑]",
        title_suffix="오늘 핵심 3분",
        call_to_action="생활에 도움이 되는 복지 정보를 계속 받고 싶다면 구독과 좋아요 부탁드립니다.",
        content_duration_seconds=45,
        topic_include_keywords=("복지", "지원금", "보조금", "정책", "혜택", "연금", "정부", "생활"),
    ),
    "quotes_daily": PresetDefinition(
        key="quotes_daily",
        label="명언 인사이트",
        group="lifestyle",
        description="명언과 짧은 해석으로 하루에 힘이 되는 메시지를 전하는 채널입니다.",
        collection_mode="quotes",
        visual_style="editorial_ai_art",
        category_id="22",
        title_prefix="[오늘의 명언]",
        title_suffix="삶에 남는 쇼츠",
        call_to_action="하루를 바꾸는 문장을 매일 받고 싶다면 구독과 알림 설정을 눌러주세요.",
        content_duration_seconds=35,
    ),
    "poems_daily": PresetDefinition(
        key="poems_daily",
        label="시와 문장",
        group="lifestyle",
        description="감정을 어루만지는 시적 문장과 짧은 해석을 전하는 채널입니다.",
        collection_mode="poems",
        visual_style="editorial_ai_art",
        category_id="22",
        title_prefix="[오늘의 문장]",
        title_suffix="3분 낭독",
        call_to_action="마음이 쉬어가는 문장을 계속 받고 싶다면 구독과 알림 설정을 눌러주세요.",
        content_duration_seconds=170,
    ),
    "senior_story_longform": PresetDefinition(
        key="senior_story_longform",
        label="시니어 인생사연 롱폼",
        group="story",
        description="시니어 시청자를 위한 1시간 내외의 인생사연 롱폼 영상을 자동 제작하는 채널입니다.",
        collection_mode="stories",
        visual_style="photoreal_reenactment",
        category_id="22",
        title_prefix="[인생사연]",
        title_suffix="실화 같은 1시간 몰입",
        call_to_action="황금시간의기록과 함께 다음 이야기도 듣고 싶으시다면 구독과 알림 설정을 부탁드립니다.",
        content_duration_seconds=3600,
        synthetic_media_default=True,
        content_format="longform_story",
        scene_count=7,
        hook_duration_seconds=40,
        audience_hint="50대~60대 시니어 시청자",
    ),
}


def preset_by_key(key: str) -> PresetDefinition:
    return PRESET_DEFINITIONS.get(key, PRESET_DEFINITIONS["economy_news"])
