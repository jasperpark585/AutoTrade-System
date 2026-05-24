from pathlib import Path
import json

from app.config import load_config
from app.models import GeneratedContent, RankedTopic
from app.youtube.uploader import MANAGE_SCOPE, READONLY_SCOPE, UPLOAD_SCOPE, YouTubeUploader


def test_uploader_marks_video_as_not_for_kids_by_default(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    uploader = YouTubeUploader(config)
    content = GeneratedContent(
        topic=RankedTopic(
            normalized_topic="ai 에이전트",
            representative_title="AI 에이전트",
            score=3.2,
            sources=["google_trends"],
            mentions=["AI 에이전트"],
            keywords=["AI", "에이전트"],
        ),
        video_title="테스트 제목",
        script="테스트 스크립트",
        description="테스트 설명",
        tags=["#AI", "#에이전트"],
        segments=["요약 1", "요약 2"],
        contains_synthetic_media=False,
        thumbnail_text="AI 에이전트",
    )

    body = uploader._build_video_insert_body(content)

    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["status"]["privacyStatus"] == config.youtube.privacy_status
    assert "containsSyntheticMedia" not in body["status"]
    assert body["snippet"]["categoryId"] == "25"
    assert body["snippet"]["defaultLanguage"] == "ko"
    assert body["snippet"]["defaultAudioLanguage"] == "ko-KR"


def test_uploader_adds_synthetic_flag_when_content_requires_it(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    uploader = YouTubeUploader(config)
    content = GeneratedContent(
        topic=RankedTopic(
            normalized_topic="실사 ai 인물",
            representative_title="실사 AI 인물 재현",
            score=2.1,
            sources=["fallback"],
            mentions=["실사 AI 인물 재현"],
            keywords=["실사", "AI", "인물"],
        ),
        video_title="테스트 제목",
        script="테스트 스크립트",
        description="테스트 설명",
        tags=["#테스트"],
        segments=["요약"],
        contains_synthetic_media=True,
        thumbnail_text="테스트",
    )

    body = uploader._build_video_insert_body(content)

    assert body["status"]["containsSyntheticMedia"] is True


def test_uploader_accepts_upload_only_token_scope(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    uploader = YouTubeUploader(config)
    token_file = tmp_path / "youtube-token.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "access",
                "refresh_token": "refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
            }
        ),
        encoding="utf-8",
    )

    scopes = uploader._load_token_scopes(token_file)

    assert scopes == [UPLOAD_SCOPE]
    assert uploader._has_required_scopes(scopes, required_scopes=[UPLOAD_SCOPE]) is True
    assert uploader._has_required_scopes(scopes, required_scopes=[MANAGE_SCOPE]) is False


def test_uploader_accepts_manage_scope_for_cleanup_and_upload(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    uploader = YouTubeUploader(config)

    assert uploader._has_required_scopes([MANAGE_SCOPE], required_scopes=[MANAGE_SCOPE]) is True
    assert uploader._has_required_scopes([MANAGE_SCOPE], required_scopes=[UPLOAD_SCOPE]) is True
    assert uploader._has_required_scopes([READONLY_SCOPE], required_scopes=[READONLY_SCOPE]) is True


def test_verify_channel_binding_reports_match(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="story_default")
    uploader = YouTubeUploader(config)
    secrets_path = tmp_path / "secrets" / "client_secret.json"
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text("{}", encoding="utf-8")
    config.youtube.client_secrets_file = str(secrets_path)

    monkeypatch.setattr(
        uploader,
        "_authenticate",
        lambda *args, **kwargs: (object(), [UPLOAD_SCOPE, READONLY_SCOPE]),
    )
    monkeypatch.setattr(
        uploader,
        "_load_channel_identity",
        lambda *args, **kwargs: {
            "authorized_channel_id": "UCqBGwk38wrT5ac0If2HD1Hw",
            "authorized_channel_title": "황금시간의기록",
        },
    )

    result = uploader.verify_channel_binding()

    assert result.status == "created"
    assert result.extra["channel_match"] is True
    assert result.extra["authorized_channel_id"] == "UCqBGwk38wrT5ac0If2HD1Hw"


def test_verify_channel_binding_reports_mismatch(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="insight_default")
    uploader = YouTubeUploader(config)
    secrets_path = tmp_path / "secrets" / "client_secret.json"
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text("{}", encoding="utf-8")
    config.youtube.client_secrets_file = str(secrets_path)

    monkeypatch.setattr(
        uploader,
        "_authenticate",
        lambda *args, **kwargs: (object(), [UPLOAD_SCOPE, MANAGE_SCOPE]),
    )
    monkeypatch.setattr(
        uploader,
        "_load_channel_identity",
        lambda *args, **kwargs: {
            "authorized_channel_id": "UCqBGwk38wrT5ac0If2HD1Hw",
            "authorized_channel_title": "황금시간의기록",
        },
    )

    result = uploader.verify_channel_binding()

    assert result.status == "failed"
    assert result.extra["channel_mismatch"] is True


def test_authorize_restores_previous_token_on_channel_mismatch(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path, channel_id="news_default")
    uploader = YouTubeUploader(config)
    secrets_path = tmp_path / "secrets" / "client_secret.json"
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text("{}", encoding="utf-8")
    config.youtube.client_secrets_file = str(secrets_path)
    token_file = tmp_path / "youtube-token-news_default.json"
    token_file.write_text('{"scopes":["https://www.googleapis.com/auth/youtube.upload"]}', encoding="utf-8")
    config.youtube.token_file = str(token_file)

    monkeypatch.setattr(
        uploader,
        "_authenticate",
        lambda *args, **kwargs: (object(), [UPLOAD_SCOPE, MANAGE_SCOPE, READONLY_SCOPE]),
    )
    monkeypatch.setattr(
        uploader,
        "_load_channel_identity",
        lambda *args, **kwargs: {
            "authorized_channel_id": "UCWsjdJoJcr-Szv4EZabE8OQ",
            "authorized_channel_title": "명언이간다",
        },
    )

    result = uploader.authorize()

    assert result.status == "failed"
    assert result.extra["channel_mismatch"] is True
    assert "youtube.upload" in token_file.read_text(encoding="utf-8")


def test_extract_video_id_supports_watch_and_shorts_urls() -> None:
    assert YouTubeUploader.extract_video_id("https://www.youtube.com/watch?v=abc123XYZ") == "abc123XYZ"
    assert YouTubeUploader.extract_video_id("https://www.youtube.com/shorts/short987") == "short987"
    assert YouTubeUploader.extract_video_id("https://youtu.be/qwerty777") == "qwerty777"
    assert YouTubeUploader.extract_video_id("plainVideoId") == "plainVideoId"


def test_build_video_update_body_preserves_status_and_adds_shorts_tag(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    uploader = YouTubeUploader(config)
    content = GeneratedContent(
        topic=RankedTopic(
            normalized_topic="뉴스",
            representative_title="뉴스",
            score=1.0,
            sources=["fallback"],
            mentions=["뉴스"],
            keywords=["뉴스"],
        ),
        video_title="새 제목",
        script="새 스크립트",
        description="새 설명",
        tags=["#뉴스"],
        segments=["요약 1", "요약 2"],
        estimated_duration_seconds=45,
        thumbnail_text="뉴스",
    )

    body = uploader._build_video_update_body(
        "video123",
        {
            "snippet": {
                "categoryId": "25",
                "defaultLanguage": "ko",
                "defaultAudioLanguage": "ko-KR",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        },
        content,
    )

    assert body["id"] == "video123"
    assert body["snippet"]["title"] == "새 제목"
    assert body["snippet"]["description"] == "새 설명"
    assert "shorts" in body["snippet"]["tags"]
    assert body["status"]["privacyStatus"] == "public"
