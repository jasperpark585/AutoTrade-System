from types import SimpleNamespace

from app.collectors.naver_news import NaverNewsCollector
from app.models import TopicCandidate


def _collector() -> NaverNewsCollector:
    return NaverNewsCollector(
        SimpleNamespace(
            allow_network=True,
            collection=SimpleNamespace(
                google_trends_limit=5,
                fallback_topics=["지원금", "물가"],
                naver_sections=[100, 101],
            ),
        )
    )


def test_naver_news_prefers_google_results_before_sections(monkeypatch) -> None:
    collector = _collector()
    section_called = {"value": False}

    def fake_google(**_kwargs):
        return [TopicCandidate(title=f"구글 {index}", source="google_news_search") for index in range(5)]

    def fake_get(*_args, **_kwargs):
        section_called["value"] = True
        raise AssertionError("Naver section request should not run when Google results are sufficient")

    monkeypatch.setattr(collector, "_collect_search_results", fake_google)
    monkeypatch.setattr("app.collectors.naver_news.requests.get", fake_get)

    result = collector.collect(["증시"], limit=5)

    assert len(result) == 5
    assert section_called["value"] is False


def test_naver_news_uses_sections_only_when_google_results_are_insufficient(monkeypatch) -> None:
    collector = _collector()
    section_called = {"value": False}

    def fake_google(**_kwargs):
        return [TopicCandidate(title="구글 1", source="google_news_search")]

    class Response:
        text = "<html><body><a class='sa_text_title'>섹션 기사 제목</a></body></html>"

        def raise_for_status(self) -> None:
            return None

    def fake_get(*_args, **_kwargs):
        section_called["value"] = True
        return Response()

    monkeypatch.setattr(collector, "_collect_search_results", fake_google)
    monkeypatch.setattr("app.collectors.naver_news.requests.get", fake_get)

    result = collector.collect(["증시"], limit=5)

    assert len(result) >= 2
    assert section_called["value"] is True
