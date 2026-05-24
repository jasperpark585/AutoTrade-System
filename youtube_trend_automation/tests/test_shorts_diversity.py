from app.config import load_config
from app.generation.content_generator import ContentGenerator
from app.models import RankedTopic, TopicDetail
from app.pipeline import Pipeline
from app.scoring.ranking import rank_topics
from app.utils.text import normalize_text


def test_normalize_text_preserves_korean_words() -> None:
    assert normalize_text("\uBB3C\uAC00 \uC0C1\uC2B9") == "\uBB3C\uAC00 \uC0C1\uC2B9"


def test_welfare_thumbnail_bucket_prefers_payment_topic_over_application_hint(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    generator = ContentGenerator(config)
    topic = RankedTopic(
        normalized_topic="\uC804\uAE30\uCC28 \uBCF4\uC870\uAE08 \uC9C0\uAE09",
        representative_title="\uC804\uAE30\uCC28 \uBCF4\uC870\uAE08 \uC870\uAE30 \uC18C\uC9C4 \uC18C\uC2DD",
        score=1.0,
        sources=["news"],
        mentions=["\uC804\uAE30\uCC28 \uBCF4\uC870\uAE08 \uC870\uAE30 \uC18C\uC9C4 \uC18C\uC2DD"],
        keywords=["\uC804\uAE30\uCC28", "\uBCF4\uC870\uAE08", "\uC9C0\uAE09", "\uD61C\uD0DD"],
    )

    bucket = generator._short_topic_bucket(
        topic,
        [
            "\uB300\uC0C1\uACFC \uC9C0\uAE09 \uBC29\uC2DD\uC740 \uC544\uC9C1 \uCD5C\uC885 \uD655\uC815 \uC804\uC785\uB2C8\uB2E4.",
            "\uC2E0\uCCAD \uC2DC\uAE30\uC640 \uCC3D\uAD6C\uB294 \uC138\uBD80 \uAE30\uC900\uC774 \uC815\uB9AC\uB418\uBA74 \uB354 \uBD84\uBA85\uD574\uC9D1\uB2C8\uB2E4.",
        ],
    )
    thumbnail = generator._resolve_short_thumbnail_text(
        topic,
        [
            "\uC804\uAE30\uCC28 \uBCF4\uC870\uAE08 \uC870\uAE30 \uC18C\uC9C4 \uAD00\uB828 \uB17C\uC758\uAC00 \uC774\uC5B4\uC9C0\uACE0 \uC788\uC2B5\uB2C8\uB2E4.",
            "\uB300\uC0C1\uACFC \uC9C0\uAE09 \uBC29\uC2DD\uC740 \uC544\uC9C1 \uCD5C\uC885 \uD655\uC815 \uC804\uC785\uB2C8\uB2E4.",
            "\uC2E0\uCCAD \uC2DC\uAE30\uC640 \uCC3D\uAD6C\uB294 \uC138\uBD80 \uAE30\uC900\uC774 \uC815\uB9AC\uB418\uBA74 \uB354 \uBD84\uBA85\uD574\uC9D1\uB2C8\uB2E4.",
        ],
    )

    assert bucket == "payment"
    assert thumbnail in {"\uC774\uBC88\uC5D4 \uC5BC\uB9C8\uAE4C\uC9C0", "\uC5B8\uC81C \uC785\uAE08\uB418\uB098", "\uC9C0\uC6D0\uAE08 \uC5BC\uB9C8\uB098 \uC624\uB098", "\uD61C\uD0DD \uADDC\uBAA8 \uC5B4\uB514\uAE4C\uC9C0", "\uC9C0\uC6D0 \uADDC\uBAA8 \uC5B4\uB514\uAE4C\uC9C0"}


def test_pipeline_prioritizes_different_news_bucket_from_recent_history(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    pipeline = Pipeline(config)
    pipeline.repository.mark_processed(
        RankedTopic(
            normalized_topic="\uBB3C\uAC00 \uC0C1\uC2B9",
            representative_title="\uBB3C\uAC00 \uC0C1\uC2B9",
            score=1.0,
            sources=["news"],
            mentions=["\uBB3C\uAC00 \uC0C1\uC2B9"],
            keywords=["\uBB3C\uAC00", "\uC0C1\uC2B9"],
        ),
        "recent-title",
        thumbnail_text="\uC0DD\uD65C\uBE44\uAC00 \uC65C \uB4E4\uC370\uC774\uB098",
    )

    inflation = RankedTopic(
        normalized_topic="\uBB3C\uAC00 \uBD88\uC548",
        representative_title="\uBB3C\uAC00 \uBD88\uC548",
        score=2.0,
        sources=["news"],
        mentions=["\uBB3C\uAC00 \uBD88\uC548"],
        keywords=["\uBB3C\uAC00", "\uC720\uAC00"],
    )
    markets = RankedTopic(
        normalized_topic="\uC99D\uC2DC \uD750\uB984",
        representative_title="\uC99D\uC2DC \uD750\uB984",
        score=1.8,
        sources=["news"],
        mentions=["\uC99D\uC2DC \uD750\uB984"],
        keywords=["\uC99D\uC2DC", "\uC8FC\uAC00"],
    )

    prioritized = pipeline._prioritize_diverse_candidates(
        [
            (2.0, inflation, []),
            (1.8, markets, []),
        ],
        preset_key="economy_news",
    )

    assert prioritized[0][1].representative_title == "\uC99D\uC2DC \uD750\uB984"


def test_welfare_pipeline_rejects_entertainment_like_detail_noise(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    pipeline = Pipeline(config)
    topic = RankedTopic(
        normalized_topic="\uBBFC\uC0DD\uC9C0\uC6D0\uAE08",
        representative_title="\uBBFC\uC0DD\uC9C0\uC6D0\uAE08 \uC2E0\uCCAD \uB17C\uC758",
        score=2.4,
        sources=["google_news", "google_trends"],
        mentions=["\uBBFC\uC0DD\uC9C0\uC6D0\uAE08"],
        keywords=["\uBBFC\uC0DD\uC9C0\uC6D0\uAE08", "\uC2E0\uCCAD", "\uC9C0\uAE09"],
    )
    details = [
        TopicDetail(title="\uD575\uC2EC", summary="\uBBFC\uC0DD\uC9C0\uC6D0\uAE08 \uB300\uC0C1\uACFC \uC9C0\uAE09 \uBC29\uC2DD \uB17C\uC758\uAC00 \uC774\uC5B4\uC9D1\uB2C8\uB2E4.", source="official"),
        TopicDetail(title="\uC5F0\uC608", summary="\uB3D9\uC0C1\uC774\uBBA8 \uD551\uD06C\uBE5B \uB17C\uB780\uC774 \uD655\uC0B0\uB410\uC2B5\uB2C8\uB2E4.", source="official"),
        TopicDetail(title="\uC2E0\uCCAD", summary="\uC2E0\uCCAD \uCC3D\uAD6C \uD655\uC815 \uC2DC \uBC14\uB85C \uD655\uC778\uD574\uC57C \uD569\uB2C8\uB2E4.", source="official"),
    ]

    assert pipeline._details_satisfy_requirements(topic, details) is False


def test_welfare_pipeline_requires_official_and_concrete_detail_set(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    pipeline = Pipeline(config)
    topic = RankedTopic(
        normalized_topic="에너지 바우처 신청",
        representative_title="에너지 바우처 신청 대상 확대",
        score=3.4,
        sources=["google_news"],
        mentions=["에너지 바우처 신청"],
        keywords=["에너지 바우처", "신청", "대상"],
    )
    strong_details = [
        TopicDetail(title="보건복지부", summary="보건복지부는 기초생활수급자와 차상위 계층 일부를 대상으로 지원한다고 밝혔습니다.", source="보건복지부"),
        TopicDetail(title="혜택", summary="냉난방 비용을 덜 수 있도록 가구별 바우처를 지급합니다.", source="정책브리핑"),
        TopicDetail(title="신청", summary="복지로 또는 주민센터에서 신청할 수 있습니다.", source="복지로"),
        TopicDetail(title="기간", summary="이번 달 말까지 접수할 수 있습니다.", source="정부24"),
    ]
    weak_details = [
        TopicDetail(title="논의", summary="지원 방안 논의가 이어지고 있습니다.", source="google_news"),
        TopicDetail(title="검토", summary="대상과 방식은 아직 검토 중입니다.", source="google_news"),
        TopicDetail(title="예정", summary="세부 기준은 추후 발표될 예정입니다.", source="google_news"),
    ]

    assert pipeline._details_satisfy_requirements(topic, strong_details) is True
    assert pipeline._details_satisfy_requirements(topic, weak_details) is False


def test_welfare_pipeline_rejects_foreign_comparison_topics(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    pipeline = Pipeline(config)
    topic = RankedTopic(
        normalized_topic="해외 유류세 인하 보조금",
        representative_title="美 비축유 방출, 英 유류세 인하, 佛 보조금 지급",
        score=2.0,
        sources=["google_news"],
        mentions=["해외 유류세 인하 보조금"],
        keywords=["비축유", "유류세", "보조금"],
    )
    details = [
        TopicDetail(title="해외 사례", summary="미국과 영국, 프랑스가 유류세와 보조금 대응에 나섰습니다.", source="조선일보"),
        TopicDetail(title="국제유가", summary="국제유가 상승에 주요국 대응이 이어지고 있습니다.", source="아주경제"),
    ]

    assert pipeline._is_actionable_welfare_topic(topic) is False
    assert pipeline._is_actionable_welfare_candidate(topic, details) is False


def test_welfare_actionable_fallback_rejects_editorial_topics(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    pipeline = Pipeline(config)
    editorial_topic = RankedTopic(
        normalized_topic="\uC9C0\uC18D\uAC00\uB2A5\uD55C \uBCF5\uC9C0",
        representative_title="\uC2E4\uD328\uB97C \uB118\uC5B4 \uC9C0\uC18D\uAC00\uB2A5\uD55C \uBCF5\uC9C0",
        score=3.1,
        sources=["google_news"],
        mentions=["\uC2E4\uD328\uB97C \uB118\uC5B4 \uC9C0\uC18D\uAC00\uB2A5\uD55C \uBCF5\uC9C0"],
        keywords=["\uBCF5\uC9C0", "\uC9C0\uC18D\uAC00\uB2A5"],
    )
    editorial_details = [
        TopicDetail(title="\uCE7C\uB7FC", summary="\uC2E4\uD328\uB97C \uB118\uC5B4 \uC9C0\uC18D\uAC00\uB2A5\uD55C \uBCF5\uC9C0 \uBC29\uD5A5\uC744 \uB17C\uD569\uB2C8\uB2E4.", source="official"),
        TopicDetail(title="\uD574\uC124", summary="\uC81C\uB3C4 \uCCA0\uD559\uACFC \uBC29\uD5A5\uC744 \uC911\uC2EC\uC73C\uB85C \uD48D\uBD80\uD558\uAC8C \uD480\uC5B4\uC90D\uB2C8\uB2E4.", source="official"),
    ]

    actionable_topic = RankedTopic(
        normalized_topic="\uBBFC\uC0DD\uC9C0\uC6D0\uAE08 \uC2E0\uCCAD",
        representative_title="\uBBFC\uC0DD\uC9C0\uC6D0\uAE08 \uC2E0\uCCAD \uC0C1\uC138",
        score=2.7,
        sources=["google_news"],
        mentions=["\uBBFC\uC0DD\uC9C0\uC6D0\uAE08 \uC2E0\uCCAD"],
        keywords=["\uBBFC\uC0DD\uC9C0\uC6D0\uAE08", "\uC2E0\uCCAD", "\uB300\uC0C1"],
    )
    actionable_details = [
        TopicDetail(title="\uD575\uC2EC", summary="\uB300\uC0C1 \uAC00\uAD6C\uC640 \uC2E0\uCCAD \uAE30\uAC04 \uD655\uC778\uC774 \uD575\uC2EC\uC785\uB2C8\uB2E4.", source="official"),
        TopicDetail(title="\uC811\uC218", summary="\uBCF5\uC9C0\uB85C\uC640 \uC8FC\uBBFC\uC13C\uD130 \uCC3D\uAD6C \uC548\uB0B4\uAC00 \uD568\uAED8 \uAC70\uB860\uB429\uB2C8\uB2E4.", source="official"),
    ]

    assert pipeline._is_actionable_welfare_candidate(editorial_topic, editorial_details) is False
    assert pipeline._is_actionable_welfare_candidate(actionable_topic, actionable_details) is True


def test_welfare_filter_keeps_actionable_topic_titles_only(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="welfare_default")
    pipeline = Pipeline(config)
    editorial_topic = RankedTopic(
        normalized_topic="\uC9C0\uC18D\uAC00\uB2A5\uD55C \uBCF5\uC9C0",
        representative_title="\uC2E4\uD328\uB97C \uB118\uC5B4 \uC9C0\uC18D\uAC00\uB2A5\uD55C \uBCF5\uC9C0",
        score=3.1,
        sources=["google_news"],
        mentions=["\uC2E4\uD328\uB97C \uB118\uC5B4 \uC9C0\uC18D\uAC00\uB2A5\uD55C \uBCF5\uC9C0"],
        keywords=["\uBCF5\uC9C0", "\uC9C0\uC18D\uAC00\uB2A5"],
    )
    actionable_topic = RankedTopic(
        normalized_topic="\uACE0\uC720\uAC00 \uD53C\uD574\uC9C0\uC6D0\uAE08",
        representative_title="\uACE0\uC720\uAC00 \uD53C\uD574\uC9C0\uC6D0\uAE08 \uCD5C\uB300 60\uB9CC \uC6D0",
        score=2.7,
        sources=["google_news"],
        mentions=["\uACE0\uC720\uAC00 \uD53C\uD574\uC9C0\uC6D0\uAE08 \uCD5C\uB300 60\uB9CC \uC6D0"],
        keywords=["\uD53C\uD574\uC9C0\uC6D0\uAE08", "\uC9C0\uAE09", "\uB300\uC0C1"],
    )

    filtered = pipeline._filter_ranked_topics([editorial_topic, actionable_topic])

    assert [item.representative_title for item in filtered] == ["\uACE0\uC720\uAC00 \uD53C\uD574\uC9C0\uC6D0\uAE08 \uCD5C\uB300 60\uB9CC \uC6D0"]


def test_rank_topics_can_expand_beyond_default_top_k(tmp_path) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    from app.models import TopicCandidate

    candidates = [
        TopicCandidate(title=f"topic {index}", source="naver_news")
        for index in range(7)
    ]

    ranked = rank_topics(candidates, config, top_k=7)

    assert len(ranked) == 7
