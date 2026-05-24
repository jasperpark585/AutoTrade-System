from pathlib import Path

from app.config import load_config
from app.generation.content_generator import ContentGenerator
from app.models import GeneratedContent, RankedTopic, StoryScene, TopicDetail
from app.storage.repository import StorageRepository


def test_thumbnail_text_strips_source_suffixes(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="통합특별시민 정부 20조 지원금",
        representative_title="통합특별시민 '정부 20조 지원금' 운용 방안 들여보니 - 드림투데이",
        score=3.4,
        sources=["google_trends"],
        mentions=["통합특별시민", "정부 20조 지원금"],
        keywords=["통합특별시민", "지원금", "정부 20조"],
    )
    details = [
        TopicDetail(title="논의 상황", summary="운용 방안 논의가 이어지고 있습니다.", source="official"),
        TopicDetail(title="지급 방식", summary="지급 방식은 아직 최종 확정 전입니다.", source="official"),
        TopicDetail(title="확인 포인트", summary="세부 기준이 정리되면 더 분명해집니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "드림투데이" not in content.thumbnail_text
    assert "v.daum.net" not in content.thumbnail_text
    assert "mk.co.kr" not in content.thumbnail_text


def test_content_generator_creates_longer_briefing_and_fixed_title_style(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="ai 에이전트",
        representative_title="AI 에이전트",
        score=3.5,
        sources=["google_trends", "naver_news"],
        mentions=["AI 에이전트"],
        keywords=["AI", "에이전트", "자동화"],
    )
    details = [
        TopicDetail(title="오늘 흐름", summary="기업 업무 자동화와 생산성 분야에서 AI 에이전트 도입 수요가 빠르게 커지고 있습니다.", source="fallback"),
        TopicDetail(title="배경", summary="국내외 IT 기업이 에이전트 기반 기능과 서비스 출시를 서두르고 있습니다.", source="fallback"),
        TopicDetail(title="관심 이유", summary="비용 절감과 업무 효율 향상 기대가 동시에 커지고 있기 때문입니다.", source="fallback"),
    ]

    content = generator.generate(topic, details)

    assert content.video_title.startswith("[경제 브리핑]") or content.video_title.startswith("[오늘의 브리핑]")
    assert content.content_format == "short"
    assert 4 <= len(content.segments) <= 10
    assert content.estimated_duration_seconds >= 45
    assert "[이번 영상 핵심]" in content.description
    assert "#AI에이전트" in content.description or "#AI" in content.description


def test_story_channel_generates_longform_story_structure(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="황혼 이혼 직전의 부부",
        representative_title="황혼 이혼 직전의 부부가 다시 식탁에 앉은 사연",
        score=4.2,
        sources=["stories"],
        mentions=["황혼 이혼", "가족 화해"],
        keywords=["황혼이혼", "부부", "가족", "화해"],
    )
    details = [
        TopicDetail(title="배경", summary="노후와 가족 갈등을 동시에 겪는 시니어 부부의 이야기를 다룹니다.", source="stories"),
        TopicDetail(title="감정선", summary="초반 갈등, 중반 반전, 후반 화해 또는 결단이 중요합니다.", source="stories"),
        TopicDetail(title="훅", summary="첫 3분 안에 비밀과 반전을 보여줘야 몰입도가 높아집니다.", source="stories"),
    ]

    content = generator.generate(topic, details)

    assert content.content_format == "longform_story"
    assert content.hook_script
    assert len(content.scenes) == 7
    assert all(scene.image_prompt for scene in content.scenes)
    assert all("16:9" in scene.image_prompt for scene in content.scenes)
    assert content.estimated_duration_seconds >= 3000


def test_news_description_stays_self_contained_and_sanitized(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="정책 변화",
        representative_title="정책 변화",
        score=3.1,
        sources=["google_trends", "naver_news"],
        mentions=["정책 변화"],
        keywords=["정책", "변화"],
    )
    details = [
        TopicDetail(title="핵심", summary="영향 대상과 시행 시점을 함께 확인해야 합니다.", source="official"),
        TopicDetail(title="배경", summary="발표 내용과 실제 적용 시점이 다를 수 있습니다.", source="official"),
        TopicDetail(title="영향", summary="생활비와 신청 조건에 직접 영향이 있을 수 있습니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "[핵심 정리]" in content.description
    assert "공식 발표문/원문 확인 필요" not in content.description
    assert "브리핑" not in content.script
    assert "연합뉴스" not in content.script
    assert "나한테" not in content.script
    assert content.script.startswith("오늘은 정책 변화 이슈부터 차근히 정리해드리겠습니다.")
    assert "영향 대상과 시행 시점을 함께 확인해야 합니다." in content.script
    assert "세부 적용 범위" in content.script
    assert "제목" not in content.script


def test_welfare_channel_description_stays_self_contained(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="에너지 바우처 신청",
        representative_title="에너지 바우처 신청",
        score=3.8,
        sources=["naver_news", "google_trends"],
        mentions=["에너지 바우처", "신청 마감"],
        keywords=["에너지", "바우처", "지원"],
    )
    details = [
        TopicDetail(title="대상", summary="기초생활수급자와 차상위 계층 일부가 대상입니다.", source="official"),
        TopicDetail(title="혜택", summary="냉난방 비용 부담을 줄일 수 있는 지원입니다.", source="official"),
        TopicDetail(title="신청 방법", summary="주민센터 또는 복지로에서 대상 여부를 확인합니다.", source="official"),
        TopicDetail(title="신청 시기", summary="신청 기간과 사용 기간은 공고문에서 다시 확인해야 합니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "[핵심 정리]" in content.description
    assert "공식 공고문 확인 필요" not in content.description
    assert "먼저 대상은" in content.script
    assert "받을 수 있는 혜택은" in content.script
    assert "신청은" in content.script
    assert "제목" not in content.script
    assert "공식 사이트" not in content.script
    assert "구독과 좋아요" in content.script


def test_welfare_generator_turns_latest_policy_into_actionable_briefing(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="고유가 지원금",
        representative_title="고유가 지원금 27일부터 준다... 소득하위 70%에 최대 60만원",
        score=4.1,
        sources=["google_news_search"],
        mentions=["고유가 지원금", "소득하위 70%", "최대 60만원"],
        keywords=["고유가", "지원금", "소득하위", "60만원"],
    )
    details = [
        TopicDetail(title="27일부터 취약계층 최대 60만원", summary="27일부터 취약계층은 먼저 받고 나머지 소득 하위 70%는 다음 달 18일부터 순차 지급됩니다.", source="official"),
        TopicDetail(title="고유가 지원금 사용처", summary="매출 30억 원이 넘는 일부 주유소는 제외되고 사용 가능한 업종을 확인해야 합니다.", source="official"),
        TopicDetail(title="대상과 금액", summary="소득 하위 70% 가구가 대상이고 가구 상황에 따라 최대 60만 원까지 지원됩니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "소득 하위 70% 가구" in content.script
    assert "최대 60만 원" in content.script
    assert "사용 가능한 곳과 제외되는 곳" in content.script or "신청은" in content.script
    assert "다음 달 18일" in content.script or "27일부터" in content.script
    assert "누가 받고 얼마 받나" not in content.script
    assert "나중에 확인" not in content.script
    assert "공식 공고문" not in content.script
    assert "누가 얼마나 받나" in content.video_title or "대상과 혜택 정리" in content.video_title


def test_story_generator_sanitizes_meta_language_and_limits_hook_length(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="가족 갈등",
        representative_title="가족 갈등 끝에 다시 손을 잡은 사연",
        score=4.0,
        sources=["stories"],
        mentions=["가족 갈등"],
        keywords=["가족", "갈등", "용서"],
    )
    details = [
        TopicDetail(title="장면 설명", summary="시청자는 이 장면에서 오해가 깊어지는 부분을 떠올리게 됩니다.", source="stories"),
        TopicDetail(title="후킹 규칙", summary="첫 3분 안에 가장 강한 반전을 보여줘야 합니다.", source="stories"),
        TopicDetail(title="실제 내용", summary="가족에게 끝내 말하지 못한 사정이 늦은 밤 한 통의 전화로 터져 나옵니다.", source="stories"),
    ]

    content = generator.generate(topic, details)

    assert content.content_format == "longform_story"
    assert 20 <= content.hook_duration_seconds <= 40
    assert "시청자" not in content.script
    assert "장면" not in content.script
    assert "3분" not in content.script
    assert "주인공" not in content.script


def test_news_generator_prefers_explanatory_detail_points_from_ai(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="환율 상승",
        representative_title="환율 상승",
        score=3.4,
        sources=["google_trends"],
        mentions=["환율 상승"],
        keywords=["환율", "원달러", "물가"],
    )
    details = [
        TopicDetail(title="핵심", summary="환율 상승은 수입 원가를 밀어 올릴 수 있습니다.", source="official"),
        TopicDetail(title="배경", summary="국제유가와 금리 기대가 함께 흔들릴 때 변동성이 커집니다.", source="official"),
    ]

    monkeypatch.setattr(
        generator.ai,
        "generate_briefing",
        lambda **kwargs: {
            "title": "환율이 오르면 먼저 오는 변화",
            "description": "환율이 오르면 수입 물가가 먼저 움직입니다. 생활비에도 천천히 반영될 수 있습니다.",
            "tags": ["환율", "물가"],
            "segments": [
                "환율 상승 = 수입원가 상승",
                "생활비 부담 가능성",
                "국제유가 변수 체크",
                "금리 방향 확인",
            ],
            "detail_points": [
                "환율이 오르면 수입 원가가 먼저 커집니다.",
                "기름과 식료품처럼 수입 비중이 큰 품목이 먼저 반응할 수 있습니다.",
                "국제유가와 금리 기대가 함께 흔들리면 체감 부담이 더 커질 수 있습니다.",
            ],
            "thumbnail_text": "환율 오르면 생기는 일",
            "thumbnail_prompt": "prompt",
            "background_prompt": "prompt",
            "altered_content_answer": "yes",
            "altered_content_reason": "test",
        },
    )

    content = generator.generate(topic, details)

    assert "환율이 오르면 수입 원가가 먼저 커집니다." in content.script
    assert "기름과 식료품처럼 수입 비중이 큰 품목이 먼저 반응할 수 있습니다." in content.script
    assert "환율 상승 = 수입원가 상승" not in content.script


def test_news_thumbnail_text_uses_curiosity_style(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="interest rate hold",
        representative_title="금리 동결 가능성 커진다",
        score=3.2,
        sources=["google_trends"],
        mentions=["금리 동결"],
        keywords=["금리", "동결"],
    )
    details = [
        TopicDetail(title="핵심", summary="금리 동결 가능성이 커지면서 시장 해석이 엇갈리고 있습니다.", source="official"),
        TopicDetail(title="배경", summary="물가와 환율 부담이 동시에 남아 있습니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert content.thumbnail_text == "금리 이번엔 멈출까"
    assert len(content.thumbnail_text) <= 18


def test_welfare_thumbnail_text_uses_curiosity_style(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="energy voucher",
        representative_title="에너지 바우처 신청 대상 확대",
        score=3.6,
        sources=["google_trends"],
        mentions=["에너지 바우처"],
        keywords=["지원", "신청", "대상"],
    )
    details = [
        TopicDetail(title="대상", summary="대상 가구와 연령 기준이 함께 조정됩니다.", source="official"),
        TopicDetail(title="신청", summary="신청 기간과 접수 창구를 먼저 확인해야 합니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert content.thumbnail_text in {"지금 신청 가능?", "이번엔 누가 받나"}
    assert len(content.thumbnail_text) <= 18


def test_tags_do_not_keep_trailing_punctuation(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="물가 얼마나 올랐나",
        representative_title="물가 얼마나 올랐나?",
        score=3.0,
        sources=["google_trends"],
        mentions=["물가 얼마나 올랐나?"],
        keywords=["물가", "내주", "다주택자", "CPI"],
    )
    details = [
        TopicDetail(title="핵심", summary="소비자물가 발표를 앞두고 있습니다.", source="official"),
        TopicDetail(title="배경", summary="국제유가와 환율이 함께 흔들리고 있습니다.", source="official"),
        TopicDetail(title="쟁점", summary="다주택자 규제 방향도 함께 예고됐습니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert all(not tag.endswith(".") for tag in content.tags)
    assert "#유튜브자동화" in content.tags


def test_news_title_strips_source_like_tokens(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="민생지원금",
        representative_title="민생지원금 또 나온다 [많이 본 경제기사] - v.daum.net",
        score=3.2,
        sources=["google_trends"],
        mentions=["민생지원금"],
        keywords=["민생지원금", "추경", "지원"],
    )
    details = [
        TopicDetail(title="핵심", summary="지원 대상과 지급 방식이 함께 거론되고 있습니다.", source="official"),
        TopicDetail(title="배경", summary="추경 논의와 맞물려 관심이 커졌습니다.", source="official"),
        TopicDetail(title="쟁점", summary="지급 시기와 금액이 아직 확정 전입니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "v.daum.net" not in content.video_title
    assert "경제기사" not in content.video_title
    assert "많이 본 경제기사" not in content.video_title

def test_news_description_strips_domains_and_verification_block(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="oecd 물가 전망",
        representative_title="OECD, 올해 한국 근원물가 상승률 급등 예상 - v.daum.net",
        score=3.7,
        sources=["google_trends", "naver_news"],
        mentions=["oecd 물가"],
        keywords=["oecd", "근원물가", "한국"],
    )
    details = [
        TopicDetail(title="핵심", summary="근원물가 상승 압력이 예상보다 오래 이어질 수 있다는 전망이 나왔습니다.", source="official"),
        TopicDetail(title="배경", summary="mk.co.kr 기사처럼 보이는 문구는 제거되어야 합니다.", source="official"),
        TopicDetail(title="쟁점", summary="중동 변수와 환율 불안이 물가 흐름에 영향을 줄 수 있다는 분석입니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "v.daum.net" not in content.description
    assert "mk.co.kr" not in content.description
    assert "검증에 사용한 핵심 사실 포인트" not in content.description
    assert "공식 발표문" not in content.description
    assert "#shorts" in content.description.lower()


def test_news_thumbnail_text_uses_curiosity_style(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="interest rate hold",
        representative_title="금리 동결 가능성 커진다",
        score=3.2,
        sources=["google_trends"],
        mentions=["금리 동결"],
        keywords=["금리", "동결"],
    )
    details = [
        TopicDetail(title="핵심", summary="금리 동결 가능성이 커지면서 시장 해석이 엇갈리고 있습니다.", source="official"),
        TopicDetail(title="배경", summary="물가와 경기 부담이 동시에 남아 있습니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert content.thumbnail_text
    assert content.thumbnail_text != "물가 왜 다시 오르나"
    assert any(token in content.thumbnail_text for token in ("금리", "이자", "대출"))
    assert len(content.thumbnail_text) <= 18


def test_welfare_thumbnail_text_uses_curiosity_style(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="energy voucher",
        representative_title="에너지 바우처 신청 대상 정리",
        score=3.6,
        sources=["google_trends"],
        mentions=["에너지 바우처"],
        keywords=["지원", "신청", "대상"],
    )
    details = [
        TopicDetail(title="대상", summary="대상 가구와 연령 기준이 함께 조정됩니다.", source="official"),
        TopicDetail(title="신청", summary="신청 기간과 접수 창구를 먼저 확인해야 합니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert content.thumbnail_text
    assert content.thumbnail_text != "이번엔 누가 받나"
    assert len(content.thumbnail_text) <= 18


def test_thumbnail_text_avoids_recent_repetition(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    repo = StorageRepository(config)
    previous = RankedTopic(
        normalized_topic="recent inflation issue",
        representative_title="물가 상승 이슈",
        score=3.5,
        sources=["news"],
        mentions=["물가 상승 이슈"],
        keywords=["물가", "상승", "생활비"],
    )
    repo.mark_processed(previous, "old title 1", thumbnail_text="물가 왜 다시 오르나")
    repo.mark_processed(previous, "old title 2", thumbnail_text="이번 달 물가 변수")

    topic = RankedTopic(
        normalized_topic="fresh inflation issue",
        representative_title="4월 생활물가 다시 들썩",
        score=3.7,
        sources=["news"],
        mentions=["4월 생활물가 다시 들썩"],
        keywords=["물가", "생활비", "4월"],
    )
    details = [
        TopicDetail(title="핵심", summary="생활물가 부담이 다시 커질 수 있다는 전망이 나옵니다.", source="official"),
        TopicDetail(title="배경", summary="식품과 생활비 체감 부담이 동시에 언급되고 있습니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert content.thumbnail_text not in {"물가 왜 다시 오르나", "이번 달 물가 변수"}


def test_thumbnail_text_avoids_recent_repetition(tmp_path: Path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    repo = StorageRepository(config)
    previous = RankedTopic(
        normalized_topic="recent inflation issue",
        representative_title="물가 상승 이슈",
        score=3.5,
        sources=["news"],
        mentions=["물가 상승 이슈"],
        keywords=["물가", "상승", "생활비"],
    )
    repo.mark_processed(previous, "old title 1", thumbnail_text="물가 왜 다시 오르나")
    repo.mark_processed(previous, "old title 2", thumbnail_text="이번 달 물가 변수")

    topic = RankedTopic(
        normalized_topic="fresh inflation issue",
        representative_title="4월 생활물가 다시 들썩",
        score=3.7,
        sources=["news"],
        mentions=["4월 생활물가 다시 들썩"],
        keywords=["물가", "생활비", "4월"],
    )
    details = [
        "생활물가 부담이 다시 커질 수 있다는 전망이 나옵니다.",
        "식품과 생활비 체감 부담이 동시에 언급되고 있습니다.",
    ]

    thumbnail_text = generator._resolve_short_thumbnail_text(topic, details)

    assert thumbnail_text not in {"물가 왜 다시 오르나", "이번 달 물가 변수"}


def test_thumbnail_text_avoids_recent_repetition(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="fresh inflation issue",
        representative_title="4월 생활물가 다시 들썩",
        score=3.7,
        sources=["news"],
        mentions=["4월 생활물가 다시 들썩"],
        keywords=["물가", "생활비", "4월"],
    )
    details = [
        "생활물가 부담이 다시 커질 수 있다는 전망이 나옵니다.",
        "식품과 생활비 체감 부담이 동시에 언급되고 있습니다.",
    ]

    monkeypatch.setattr(
        generator,
        "_recent_thumbnail_texts",
        lambda limit=8: ["물가 왜 다시 오르나", "이번 달 물가 변수"],
    )

    thumbnail_text = generator._resolve_short_thumbnail_text(topic, details)

    assert thumbnail_text not in {"물가 왜 다시 오르나", "이번 달 물가 변수"}

def test_content_generator_creates_longer_briefing_and_fixed_title_style(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="ai 에이전트",
        representative_title="AI 에이전트",
        score=3.5,
        sources=["google_trends", "naver_news"],
        mentions=["AI 에이전트"],
        keywords=["AI", "에이전트", "자동화"],
    )
    details = [
        TopicDetail(title="오늘 흐름", summary="기업 업무 자동화와 생산성 분야에서 AI 에이전트 도입 수요가 빠르게 커지고 있습니다.", source="fallback"),
        TopicDetail(title="배경", summary="국내외 IT 기업이 에이전트 기반 기능과 서비스 출시를 서두르고 있습니다.", source="fallback"),
        TopicDetail(title="관심 이유", summary="비용 절감과 업무 효율 향상 기대가 동시에 커지고 있기 때문입니다.", source="fallback"),
    ]

    content = generator.generate(topic, details)

    assert content.video_title.startswith("[경제 브리핑]") or content.video_title.startswith("[오늘의 브리핑]")
    assert content.content_format == "short"
    assert 4 <= len(content.segments) <= 10
    assert content.estimated_duration_seconds >= 45
    assert "[핵심 정리]" in content.description
    assert "#AI" in content.description or "#에이전트" in content.description
