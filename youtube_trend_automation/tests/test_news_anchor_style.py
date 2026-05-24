from pathlib import Path

from app.config import load_config
from app.generation.content_generator import ContentGenerator
from app.models import RankedTopic, TopicDetail


def test_news_fallback_uses_anchor_style_and_cta(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="기준금리 동결 가능성",
        representative_title="기준금리 동결 가능성 커진다",
        score=3.6,
        sources=["google_trends"],
        mentions=["기준금리 동결 가능성"],
        keywords=["금리", "동결", "이자"],
    )
    details = [
        TopicDetail(title="핵심", summary="시장에서는 이번 달 기준금리 동결 가능성이 더 커졌다는 해석이 나오고 있습니다.", source="official"),
        TopicDetail(title="배경", summary="환율과 물가 부담이 동시에 남아 있어 금리를 섣불리 낮추기 어렵다는 분석입니다.", source="official"),
        TopicDetail(title="반응", summary="대출 금리와 예금 금리 방향을 함께 살펴봐야 한다는 반응이 이어지고 있습니다.", source="official"),
        TopicDetail(title="포인트", summary="이번 발표에서는 향후 인하 시점을 어떻게 설명하는지가 핵심 포인트로 꼽힙니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert content.segments[0].startswith("오늘 경제 뉴스는")
    assert "기준금리" in content.segments[0]
    assert content.segments[-1] == "지금까지 NewsTrend였습니다. 도움이 되셨다면 구독과 좋아요 부탁드립니다."
    assert "공식 발표문" not in content.script
    assert "원문 확인" not in content.script
    assert "자세한 대상 내용은 알아봐야" not in content.script
    assert "기준금리" in content.script
    assert "동결" in content.script
    assert "구독과 좋아요" in content.description


def test_news_ai_output_is_reframed_into_anchor_style(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)

    topic = RankedTopic(
        normalized_topic="소비자물가 상승",
        representative_title="4월 소비자물가 상승률 전망",
        score=3.4,
        sources=["google_trends"],
        mentions=["4월 소비자물가 상승률 전망"],
        keywords=["물가", "소비자물가", "생활비"],
    )
    details = [
        TopicDetail(title="핵심", summary="4월 소비자물가 상승률이 다시 높아질 수 있다는 전망이 나왔습니다.", source="official"),
        TopicDetail(title="배경", summary="가공식품과 에너지 가격 압력이 동시에 남아 있다는 분석입니다.", source="official"),
        TopicDetail(title="반응", summary="생활비 체감 부담이 다시 커질 수 있다는 우려가 이어지고 있습니다.", source="official"),
        TopicDetail(title="포인트", summary="정부의 추가 물가 대책과 국제유가 흐름을 함께 봐야 한다는 평가입니다.", source="official"),
    ]

    monkeypatch.setattr(
        generator.ai,
        "generate_briefing",
        lambda **kwargs: {
            "title": "물가 왜 다시 오르나",
            "description": "자세한 내용은 공식 발표문을 확인해보세요.",
            "tags": ["물가", "생활비"],
            "segments": [
                "물가가 오릅니다",
                "자세한 내용은 공식 발표문 확인 필요",
            ],
            "detail_points": [
                "4월 소비자물가 상승률이 다시 높아질 수 있다는 전망이 나왔습니다.",
                "가공식품과 에너지 가격 압력이 동시에 남아 있다는 분석입니다.",
                "생활비 체감 부담이 다시 커질 수 있다는 우려가 이어지고 있습니다.",
            ],
            "thumbnail_text": "물가 왜 다시 오르나",
            "thumbnail_prompt": "prompt",
            "background_prompt": "prompt",
            "altered_content_answer": "yes",
            "altered_content_reason": "test",
        },
    )

    content = generator.generate(topic, details)

    assert content.segments[0].startswith("오늘 경제 뉴스")
    assert content.segments[-1] == "지금까지 NewsTrend였습니다. 도움이 되셨다면 구독과 좋아요 부탁드립니다."
    assert "공식 발표문" not in content.script
    assert "원문 확인" not in content.description
    assert "구독과 좋아요" in content.script


def test_news_anchor_style_handles_savings_topics_naturally(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="장병내일준비적금 우대금리 확대",
        representative_title="우정사업본부, '우체국 장병내일준비적금' 우대금리 확대",
        score=3.9,
        sources=["google_trends"],
        mentions=["장병내일준비적금", "우대금리 확대"],
        keywords=["적금", "우대금리", "장병", "우체국"],
    )
    details = [
        TopicDetail(title="핵심", summary="우체국 장병내일준비적금 금리가 최대 연 11%까지 높아집니다.", source="official"),
        TopicDetail(title="배경", summary="군 복무 기간 동안 모을 수 있는 적금 혜택이 더 커지는 방향입니다.", source="official"),
        TopicDetail(title="반응", summary="가입 조건이 맞는 장병이라면 실제 수익 차이가 커질 수 있다는 반응이 나옵니다.", source="official"),
        TopicDetail(title="포인트", summary="실제 적용 대상과 우대 조건이 어떻게 달라지는지가 핵심 포인트입니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "생활경제 뉴스" in content.segments[0]
    assert "군 복무 기간 동안 모을 수 있는 적금 혜택이 더 커지는 방향입니다." in content.script
    assert "적용 대상과 우대 조건" in content.script
    assert "대출 숨통 트일까" != content.thumbnail_text
    assert any(token in content.thumbnail_text for token in ("적금", "우대금리", "가입"))
    assert "우정사업본부" not in content.video_title


def test_news_thumbnail_text_does_not_invent_unrelated_sector_labels(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="코스피 변동성 확대",
        representative_title="코스피 장중 급락, 환율도 출렁",
        score=3.5,
        sources=["google_trends"],
        mentions=["코스피 장중 급락"],
        keywords=["코스피", "환율", "증시"],
    )
    details = [
        TopicDetail(title="증시", summary="코스피가 장중 큰 폭으로 흔들렸습니다.", source="official"),
        TopicDetail(title="환율", summary="환율도 함께 오르며 투자심리가 위축됐습니다.", source="official"),
        TopicDetail(title="반응", summary="외국인 수급이 약해지며 변동성이 커졌습니다.", source="official"),
        TopicDetail(title="포인트", summary="하루짜리 조정인지 추세 전환인지가 관건입니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "반도체주" not in content.thumbnail_text
    assert any(token in content.thumbnail_text for token in ("증시", "주가", "시장", "금융시장"))


def test_news_thumbnail_text_uses_fact_driven_amount_phrase_when_available(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="부동산 탈세 신고 포상금",
        representative_title="부동산 탈세 신고하면 최대 40억 원 포상금 지급",
        score=3.4,
        sources=["google_trends"],
        mentions=["부동산 탈세 신고 포상금"],
        keywords=["부동산", "탈세", "포상금"],
    )
    details = [
        TopicDetail(title="국세청", summary="부동산 탈세 신고포상금이 최대 40억 원까지 확대됩니다.", source="official"),
        TopicDetail(title="제보", summary="제보가 이어지며 포상금 기준에도 관심이 쏠리고 있습니다.", source="official"),
        TopicDetail(title="포인트", summary="실제 적용 기준과 지급 절차가 핵심입니다.", source="official"),
        TopicDetail(title="수치", summary="최대 40억 원까지 받을 수 있습니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert any(token in content.thumbnail_text for token in ("40억", "포상금"))


def test_news_label_keeps_market_context_from_bracketed_headline(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="아증시 하락",
        representative_title="[亞증시-종합] 불안한 휴전 합의에 전반적으로 하락 - 연합인포맥스",
        score=3.2,
        sources=["google_news_search"],
        mentions=["아시아 증시"],
        keywords=["증시", "휴전", "하락"],
    )
    details = [
        TopicDetail(title="증시", summary="아시아 증시가 전반적으로 약세를 보였습니다.", source="official"),
        TopicDetail(title="배경", summary="휴전 합의 불확실성이 이어지며 투자심리가 흔들렸습니다.", source="official"),
        TopicDetail(title="환율", summary="환율도 함께 움직이며 금융시장이 출렁였습니다.", source="official"),
        TopicDetail(title="포인트", summary="하루짜리 반응인지 지켜볼 필요가 있습니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "아시아 증시" in content.video_title
    assert "아시아 증시" in content.segments[0]


def test_news_title_drops_generic_suffix_for_cleaner_broadcast_style(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="반도체 수퍼사이클",
        representative_title="21만 전자·100만 닉스 탈환...반도체 수퍼 사이클 언제까지",
        score=3.8,
        sources=["google_news_search"],
        mentions=["반도체 수퍼사이클"],
        keywords=["반도체", "삼성전자", "하이닉스"],
    )
    details = [
        TopicDetail(title="상승", summary="삼성전자와 SK하이닉스가 장중 강세를 보였습니다.", source="official"),
        TopicDetail(title="배경", summary="반도체 업황 회복 기대가 다시 커졌습니다.", source="official"),
        TopicDetail(title="반응", summary="외국인 수급이 돌아오며 투자 심리가 개선됐습니다.", source="official"),
        TopicDetail(title="포인트", summary="이번 반등이 실적 기대까지 이어질지가 관건입니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert content.video_title.startswith("[경제 브리핑]")
    assert "오늘 핵심 쇼츠" not in content.video_title


def test_news_fact_direction_filter_keeps_consistent_market_flow(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="반도체 반등",
        representative_title="21만 전자·100만 닉스 탈환",
        score=3.8,
        sources=["google_news_search"],
        mentions=["21만 전자·100만 닉스 탈환"],
        keywords=["삼성전자", "하이닉스", "탈환"],
    )
    details = [
        TopicDetail(title="강세", summary="삼성전자와 SK하이닉스가 장중 강세를 보이며 21만 전자·100만 닉스를 다시 탈환했습니다.", source="official"),
        TopicDetail(title="회복", summary="외국인 매수세가 유입되며 반도체주가 반등했습니다.", source="official"),
        TopicDetail(title="약세", summary="삼전·하닉 3%대 하락 마감으로 하루 만에 반납했습니다.", source="official"),
        TopicDetail(title="포인트", summary="이번 반등이 하루짜리인지, 실적 기대로 이어질지가 관건입니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "하락 마감" not in content.script
    assert "반납" not in content.script
    assert "반등" in content.script or "탈환" in content.script


def test_news_fact_selection_skips_unrelated_detail_items(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="포상금 지급",
        representative_title="부동산 탈세 신고하면 최대 40억 원 포상금 지급",
        score=3.5,
        sources=["google_news_search"],
        mentions=["부동산 탈세 포상금"],
        keywords=["부동산", "탈세", "포상금"],
    )
    details = [
        TopicDetail(title="핵심", summary="부동산 탈세 신고포상금이 최대 40억 원까지 확대됩니다.", source="official"),
        TopicDetail(title="절차", summary="신고 절차와 지급 기준이 함께 공개됐습니다.", source="official"),
        TopicDetail(title="무관", summary="SK네트웍스 회사채 수요예측서 목표액 5배 넘게 확보", source="official"),
        TopicDetail(title="포인트", summary="실제 적용 기준과 지급 절차를 함께 보는 것이 중요합니다.", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "SK네트웍스" not in content.script


def test_news_fallback_turns_headline_fragments_into_broadcast_lines(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    generator = ContentGenerator(config)
    monkeypatch.setattr(generator.ai, "generate_briefing", lambda **kwargs: None)

    topic = RankedTopic(
        normalized_topic="한국은행 기준금리",
        representative_title="한국은행 기준금리",
        score=3.3,
        sources=["google_trends"],
        mentions=["한국은행 기준금리"],
        keywords=["한국은행", "기준금리", "동결"],
    )
    details = [
        TopicDetail(title="핵심", summary="신현송 중동 전쟁 장기화 시 기준금리 인상 등 통화정책 대응 필요", source="official"),
        TopicDetail(title="배경", summary="한국은행, 기준금리 전쟁 리스크에 인하보다 동결 무게", source="official"),
        TopicDetail(title="반응", summary="4월 금통위 기준금리 동결 전망..인상 경로 진단 주목", source="official"),
        TopicDetail(title="포인트", summary="기준금리 동결될 듯…전쟁에 물가·환율 우려, 연내 올릴 수도", source="official"),
    ]

    content = generator.generate(topic, details)

    assert "신현송" not in content.script
    assert "인하보다 동결 무게" not in content.script
    assert "동결 쪽에 무게" in content.script or "금리를 쉽게 낮추기 어렵" in content.script
    assert content.segments[-1] == "지금까지 NewsTrend였습니다. 도움이 되셨다면 구독과 좋아요 부탁드립니다."
