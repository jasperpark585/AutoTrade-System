from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import AppConfig
from app.models import ArtifactStatus, GeneratedContent
from app.studio.channel_paths import resolve_youtube_token_file
from app.utils.logging import get_logger

LOGGER = get_logger(__name__)

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
FULL_SCOPE = "https://www.googleapis.com/auth/youtube"
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
AUTHORIZATION_SCOPES = [UPLOAD_SCOPE, MANAGE_SCOPE, READONLY_SCOPE]


class YouTubeAuthRequiredError(RuntimeError):
    """Raised when a channel needs one-time interactive YouTube authorization."""


class YouTubeUploader:
    """Upload videos and thumbnails to YouTube or fall back to a mock result."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def upload(
        self,
        content: GeneratedContent,
        video: ArtifactStatus,
        thumbnail: ArtifactStatus | None = None,
    ) -> ArtifactStatus:
        """Upload the rendered video and optionally set a thumbnail."""

        if not self.config.youtube.enabled:
            return ArtifactStatus(
                status="mocked",
                provider="youtube",
                message="YouTube upload disabled; returning mock upload result.",
            )

        if video.status != "created" or not video.path:
            return ArtifactStatus(
                status="skipped",
                provider="youtube",
                message="No rendered MP4 available for upload.",
            )

        client_secrets, token_file = self._resolve_auth_paths()
        if client_secrets is None or not client_secrets.exists():
            return ArtifactStatus(
                status="mocked",
                provider="youtube",
                path=str(token_file),
                message="Missing YouTube client secrets; upload mocked.",
            )

        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("YouTube client import failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube",
                message=f"Google API libraries unavailable: {exc}",
            )

        try:
            credentials, token_scopes = self._authenticate(
                client_secrets,
                token_file,
                allow_interactive=False,
                required_scopes=[UPLOAD_SCOPE],
                requested_scopes=AUTHORIZATION_SCOPES,
            )
        except YouTubeAuthRequiredError as exc:
            return ArtifactStatus(
                status="mocked",
                provider="youtube",
                path=str(token_file),
                message=str(exc),
            )
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("YouTube auth import failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube",
                message=f"Google API libraries unavailable: {exc}",
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("YouTube authentication failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube",
                path=str(token_file),
                message=f"YouTube authentication failed: {exc}",
            )

        binding_check = self._evaluate_channel_binding(token_scopes, self._load_channel_identity(credentials, token_scopes))
        if binding_check.get("channel_mismatch"):
            return ArtifactStatus(
                status="failed",
                provider="youtube",
                path=str(token_file),
                message=str(binding_check.get("channel_mismatch_message") or "Configured channel does not match the authorized YouTube channel."),
                extra=binding_check,
            )

        youtube = build("youtube", "v3", credentials=credentials)
        request_body = self._build_video_insert_body(content)
        request = youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=MediaFileUpload(video.path, resumable=True),
        )
        response = request.execute()

        video_id = response["id"]
        snippet = response.get("snippet", {})
        extra = {
            "video_id": video_id,
            "privacy_status": self.config.youtube.privacy_status,
            "requested_category_id": request_body["snippet"]["categoryId"],
            "default_language": self.config.youtube.default_language,
            "default_audio_language": self.config.youtube.default_audio_language,
            "made_for_kids": self.config.youtube.made_for_kids,
            "contains_synthetic_media": content.contains_synthetic_media,
            "altered_content_reason": content.altered_content_reason,
            "recommended_altered_content_answer": "yes" if content.contains_synthetic_media else "no",
            "recommended_caption_certification": self.config.youtube.caption_certification_hint,
            "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        }
        if self._is_probable_short(content):
            extra["content_type"] = "shorts"
            extra["shorts_url"] = f"https://www.youtube.com/shorts/{video_id}"
            extra["watch_url"] = f"https://www.youtube.com/watch?v={video_id}"
        if snippet.get("channelTitle"):
            extra["channel_title"] = snippet["channelTitle"]
        if snippet.get("channelId"):
            extra["channel_id"] = snippet["channelId"]
            extra["studio_content_url"] = f"https://studio.youtube.com/channel/{snippet['channelId']}/videos"
            expected_channel_id = str(getattr(self.config.active_channel, "youtube_channel_id", "") or "").strip()
            if expected_channel_id and expected_channel_id != snippet["channelId"]:
                extra["channel_mismatch"] = True
                extra["expected_channel_id"] = expected_channel_id
                extra["channel_mismatch_message"] = "Uploaded channel ID does not match the configured channel ID."

        if self._has_required_scopes(token_scopes, required_scopes=[READONLY_SCOPE]) or self._has_required_scopes(
            token_scopes,
            required_scopes=[MANAGE_SCOPE],
        ):
            try:
                video_payload = youtube.videos().list(part="snippet,status", id=video_id).execute()
                items = video_payload.get("items", [])
                if items:
                    item = items[0]
                    actual_snippet = item.get("snippet", {})
                    actual_status = item.get("status", {})
                    if actual_snippet.get("categoryId"):
                        extra["actual_category_id"] = actual_snippet["categoryId"]
                    if actual_snippet.get("defaultLanguage"):
                        extra["actual_default_language"] = actual_snippet["defaultLanguage"]
                    if actual_snippet.get("defaultAudioLanguage"):
                        extra["actual_default_audio_language"] = actual_snippet["defaultAudioLanguage"]
                    if "privacyStatus" in actual_status:
                        extra["actual_privacy_status"] = actual_status["privacyStatus"]
                    if "selfDeclaredMadeForKids" in actual_status:
                        extra["actual_made_for_kids"] = actual_status["selfDeclaredMadeForKids"]
                    if "containsSyntheticMedia" in actual_status:
                        extra["actual_contains_synthetic_media"] = actual_status["containsSyntheticMedia"]
            except HttpError as exc:
                LOGGER.info("Uploaded video metadata verification skipped: %s", exc)
                extra["verification_warning"] = str(exc)
        else:
            extra["verification_warning"] = "Readonly YouTube scope not granted; skipped post-upload metadata verification."

        if thumbnail and thumbnail.status == "created" and thumbnail.path:
            extra["thumbnail_path"] = thumbnail.path
            if self._is_probable_short(content):
                extra["thumbnail_status"] = "skipped"
                extra["thumbnail_reason"] = (
                    "Likely uploaded as a Short. YouTube does not support custom thumbnail uploads for Shorts."
                )
            else:
                try:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(thumbnail.path),
                    ).execute()
                    extra["thumbnail_status"] = "created"
                except HttpError as exc:
                    LOGGER.warning("Thumbnail upload failed: %s", exc)
                    extra["thumbnail_status"] = "failed"
                    extra["thumbnail_error"] = str(exc)

        return ArtifactStatus(
            status="created",
            provider="youtube",
            path=extra.get("shorts_url", f"https://www.youtube.com/watch?v={video_id}"),
            message="Video uploaded to YouTube.",
            extra=extra,
        )

    def authorize(self) -> ArtifactStatus:
        """Create or refresh the current channel's YouTube OAuth token interactively."""

        client_secrets, token_file = self._resolve_auth_paths()
        previous_token_text = token_file.read_text(encoding="utf-8") if token_file.exists() else None
        if client_secrets is None or not client_secrets.exists():
            return ArtifactStatus(
                status="mocked",
                provider="youtube-auth",
                path=str(token_file),
                message="Missing YouTube client secrets; channel authorization cannot start.",
            )

        try:
            credentials, token_scopes = self._authenticate(
                client_secrets,
                token_file,
                allow_interactive=True,
                force_reauth=True,
                required_scopes=AUTHORIZATION_SCOPES,
                requested_scopes=AUTHORIZATION_SCOPES,
            )
            channel_identity = self._load_channel_identity(credentials, token_scopes)
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("YouTube auth import failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube-auth",
                path=str(token_file),
                message=f"Google API libraries unavailable: {exc}",
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Interactive YouTube authorization failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube-auth",
                path=str(token_file),
                message=f"YouTube authorization failed: {exc}",
            )

        binding = self._evaluate_channel_binding(token_scopes, channel_identity)
        if binding.get("channel_mismatch"):
            expected_label = self._format_channel_label(
                binding.get("expected_channel_title"),
                binding.get("expected_channel_id"),
            )
            authorized_label = self._format_channel_label(
                binding.get("authorized_channel_title"),
                binding.get("authorized_channel_id"),
            )
            if previous_token_text is not None:
                token_file.write_text(previous_token_text, encoding="utf-8")
                message = (
                    f"Authorized channel {authorized_label} did not match the configured channel "
                    f"{expected_label}, so the previous token was restored."
                )
            else:
                token_file.unlink(missing_ok=True)
                message = (
                    f"Authorized channel {authorized_label} did not match the configured channel "
                    f"{expected_label}, so the new token was discarded."
                )
            return ArtifactStatus(
                status="failed",
                provider="youtube-auth",
                path=str(token_file),
                message=message,
                extra={"scopes": token_scopes, **binding},
            )

        return ArtifactStatus(
            status="created",
            provider="youtube-auth",
            path=str(token_file),
            message="YouTube OAuth token is ready for this channel.",
            extra={"scopes": token_scopes, **channel_identity, **binding},
        )

    def verify_channel_binding(self) -> ArtifactStatus:
        """Check whether the current token is bound to the configured YouTube channel."""

        client_secrets, token_file = self._resolve_auth_paths()
        if client_secrets is None or not client_secrets.exists():
            return ArtifactStatus(
                status="mocked",
                provider="youtube-verify",
                path=str(token_file),
                message="Missing YouTube client secrets; channel verification cannot start.",
            )

        try:
            credentials, token_scopes = self._authenticate(
                client_secrets,
                token_file,
                allow_interactive=False,
                required_scopes=[UPLOAD_SCOPE],
                requested_scopes=AUTHORIZATION_SCOPES,
            )
        except YouTubeAuthRequiredError as exc:
            return ArtifactStatus(
                status="mocked",
                provider="youtube-verify",
                path=str(token_file),
                message=str(exc),
                extra={"required_scopes": [UPLOAD_SCOPE]},
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("YouTube channel verification authentication failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube-verify",
                path=str(token_file),
                message=f"YouTube channel verification failed: {exc}",
            )

        channel_identity = self._load_channel_identity(credentials, token_scopes)
        binding = self._evaluate_channel_binding(token_scopes, channel_identity)
        if binding.get("channel_mismatch"):
            expected_label = self._format_channel_label(
                binding.get("expected_channel_title"),
                binding.get("expected_channel_id"),
            )
            authorized_label = self._format_channel_label(
                binding.get("authorized_channel_title"),
                binding.get("authorized_channel_id"),
            )
            return ArtifactStatus(
                status="failed",
                provider="youtube-verify",
                path=str(token_file),
                message=(
                    f"Configured channel {expected_label} does not match the authorized YouTube token "
                    f"channel {authorized_label}."
                ),
                extra=binding,
            )
        if binding.get("authorized_channel_id"):
            expected_label = self._format_channel_label(
                binding.get("expected_channel_title"),
                binding.get("expected_channel_id"),
            )
            return ArtifactStatus(
                status="created",
                provider="youtube-verify",
                path=str(token_file),
                message=f"Configured YouTube channel matches the authorized token for {expected_label}.",
                extra=binding,
            )
        return ArtifactStatus(
            status="mocked",
            provider="youtube-verify",
            path=str(token_file),
            message="Token is present, but channel identity could not be verified because readonly/manage scope is missing.",
            extra=binding,
        )

    def delete_videos(self, video_ids: list[str]) -> ArtifactStatus:
        """Delete uploaded videos for the currently selected channel."""

        normalized_ids = [str(video_id).strip() for video_id in video_ids if str(video_id).strip()]
        if not normalized_ids:
            return ArtifactStatus(
                status="skipped",
                provider="youtube-cleanup",
                message="No YouTube video IDs were provided for deletion.",
            )

        client_secrets, token_file = self._resolve_auth_paths()
        if client_secrets is None or not client_secrets.exists():
            return ArtifactStatus(
                status="mocked",
                provider="youtube-cleanup",
                path=str(token_file),
                message="Missing YouTube client secrets; cleanup cannot start.",
            )

        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("YouTube client import failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube-cleanup",
                message=f"Google API libraries unavailable: {exc}",
            )

        try:
            credentials, token_scopes = self._authenticate(
                client_secrets,
                token_file,
                allow_interactive=False,
                required_scopes=[MANAGE_SCOPE],
                requested_scopes=AUTHORIZATION_SCOPES,
            )
        except YouTubeAuthRequiredError as exc:
            return ArtifactStatus(
                status="mocked",
                provider="youtube-cleanup",
                path=str(token_file),
                message=str(exc),
                extra={"required_scopes": [MANAGE_SCOPE]},
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("YouTube cleanup authentication failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube-cleanup",
                path=str(token_file),
                message=f"YouTube cleanup authentication failed: {exc}",
            )

        youtube = build("youtube", "v3", credentials=credentials)
        channel_identity = self._load_channel_identity(credentials, token_scopes)
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        for video_id in normalized_ids:
            try:
                youtube.videos().delete(id=video_id).execute()
                deleted.append(video_id)
            except HttpError as exc:
                failed.append({"video_id": video_id, "error": str(exc)})

        status = "created" if deleted and not failed else "failed" if failed and not deleted else "partial"
        message = (
            f"Deleted {len(deleted)} YouTube video(s)."
            if deleted and not failed
            else f"Deleted {len(deleted)} video(s), {len(failed)} failed."
            if deleted
            else "No YouTube videos were deleted."
        )
        return ArtifactStatus(
            status=status,
            provider="youtube-cleanup",
            message=message,
            extra={
                "deleted_video_ids": deleted,
                "failed_video_ids": failed,
                "granted_scopes": token_scopes,
                **channel_identity,
            },
        )

    def update_video_metadata(self, video_ids: list[str], content: GeneratedContent) -> ArtifactStatus:
        """Update title/description/tags on existing uploaded videos for the selected channel."""

        normalized_ids = [str(video_id).strip() for video_id in video_ids if str(video_id).strip()]
        if not normalized_ids:
            return ArtifactStatus(
                status="skipped",
                provider="youtube-update",
                message="No YouTube video IDs were provided for metadata repair.",
            )

        client_secrets, token_file = self._resolve_auth_paths()
        if client_secrets is None or not client_secrets.exists():
            return ArtifactStatus(
                status="mocked",
                provider="youtube-update",
                path=str(token_file),
                message="Missing YouTube client secrets; metadata repair cannot start.",
            )

        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except ImportError as exc:  # pragma: no cover
            LOGGER.warning("YouTube client import failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube-update",
                message=f"Google API libraries unavailable: {exc}",
            )

        try:
            credentials, token_scopes = self._authenticate(
                client_secrets,
                token_file,
                allow_interactive=False,
                required_scopes=[MANAGE_SCOPE],
                requested_scopes=AUTHORIZATION_SCOPES,
            )
        except YouTubeAuthRequiredError as exc:
            return ArtifactStatus(
                status="mocked",
                provider="youtube-update",
                path=str(token_file),
                message=str(exc),
                extra={"required_scopes": [MANAGE_SCOPE]},
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("YouTube metadata repair authentication failed: %s", exc)
            return ArtifactStatus(
                status="failed",
                provider="youtube-update",
                path=str(token_file),
                message=f"YouTube metadata repair authentication failed: {exc}",
            )

        channel_identity = self._load_channel_identity(credentials, token_scopes)
        binding_check = self._evaluate_channel_binding(token_scopes, channel_identity)
        if binding_check.get("channel_mismatch"):
            return ArtifactStatus(
                status="failed",
                provider="youtube-update",
                path=str(token_file),
                message=str(binding_check.get("channel_mismatch_message") or "Configured channel does not match the authorized YouTube channel."),
                extra=binding_check,
            )

        youtube = build("youtube", "v3", credentials=credentials)
        updated: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for video_id in normalized_ids:
            try:
                current = youtube.videos().list(part="snippet,status", id=video_id).execute()
                items = current.get("items", [])
                if not items:
                    failed.append({"video_id": video_id, "error": "Video not found."})
                    continue
                body = self._build_video_update_body(video_id, items[0], content)
                youtube.videos().update(part="snippet,status", body=body).execute()
                updated.append(
                    {
                        "video_id": video_id,
                        "watch_url": f"https://www.youtube.com/watch?v={video_id}",
                        "shorts_url": f"https://www.youtube.com/shorts/{video_id}",
                        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
                    }
                )
            except HttpError as exc:
                failed.append({"video_id": video_id, "error": str(exc)})

        status = "created" if updated and not failed else "failed" if failed and not updated else "partial"
        message = (
            f"Updated metadata for {len(updated)} YouTube video(s)."
            if updated and not failed
            else f"Updated {len(updated)} video(s), {len(failed)} failed."
            if updated
            else "No YouTube videos were updated."
        )
        return ArtifactStatus(
            status=status,
            provider="youtube-update",
            path=updated[0]["shorts_url"] if updated and self._is_probable_short(content) else updated[0]["watch_url"] if updated else None,
            message=message,
            extra={
                "updated_videos": updated,
                "failed_video_ids": failed,
                "granted_scopes": token_scopes,
                "title": content.video_title,
                "description": content.description,
                "tags": content.tags,
                **channel_identity,
            },
        )

    def _build_video_insert_body(self, content: GeneratedContent) -> dict[str, object]:
        tags = [tag.lstrip("#") for tag in content.tags]
        if self._is_probable_short(content) and "shorts" not in {tag.lower() for tag in tags}:
            tags.append("shorts")
        status: dict[str, object] = {
            "privacyStatus": self.config.youtube.privacy_status,
            "selfDeclaredMadeForKids": self.config.youtube.made_for_kids,
        }
        if content.contains_synthetic_media:
            status["containsSyntheticMedia"] = True

        return {
            "snippet": {
                "title": content.video_title,
                "description": content.description,
                "tags": tags,
                "categoryId": self.config.youtube.category_id,
                "defaultLanguage": self.config.youtube.default_language,
                "defaultAudioLanguage": self.config.youtube.default_audio_language,
            },
            "status": status,
        }

    def _build_video_update_body(
        self,
        video_id: str,
        current_payload: dict[str, object],
        content: GeneratedContent,
    ) -> dict[str, object]:
        current_snippet = current_payload.get("snippet", {}) if isinstance(current_payload.get("snippet"), dict) else {}
        current_status = current_payload.get("status", {}) if isinstance(current_payload.get("status"), dict) else {}
        tags = [tag.lstrip("#") for tag in content.tags]
        if self._is_probable_short(content) and "shorts" not in {tag.lower() for tag in tags}:
            tags.append("shorts")

        status: dict[str, object] = {}
        if "privacyStatus" in current_status:
            status["privacyStatus"] = current_status["privacyStatus"]
        else:
            status["privacyStatus"] = self.config.youtube.privacy_status
        if "selfDeclaredMadeForKids" in current_status:
            status["selfDeclaredMadeForKids"] = current_status["selfDeclaredMadeForKids"]
        else:
            status["selfDeclaredMadeForKids"] = self.config.youtube.made_for_kids

        return {
            "id": video_id,
            "snippet": {
                "categoryId": current_snippet.get("categoryId") or self.config.youtube.category_id,
                "title": content.video_title,
                "description": content.description,
                "tags": tags,
                "defaultLanguage": current_snippet.get("defaultLanguage") or self.config.youtube.default_language,
                "defaultAudioLanguage": current_snippet.get("defaultAudioLanguage") or self.config.youtube.default_audio_language,
            },
            "status": status,
        }

    def _is_probable_short(self, content: GeneratedContent) -> bool:
        return (
            self.config.render.height > self.config.render.width
            and (content.estimated_duration_seconds or self.config.render.default_duration_seconds) <= 180
        )

    def _resolve_auth_paths(self) -> tuple[Path | None, Path]:
        client_secrets = Path(self.config.youtube.client_secrets_file) if self.config.youtube.client_secrets_file else None
        token_file = (
            Path(self.config.youtube.token_file)
            if self.config.youtube.token_file
            else Path(
                resolve_youtube_token_file(
                    "",
                    self.config.active_channel.id if self.config.active_channel else "default",
                    self.config.project_root,
                )
            )
        )
        return client_secrets, token_file

    def _authenticate(
        self,
        client_secrets: Path,
        token_file: Path,
        *,
        allow_interactive: bool,
        force_reauth: bool = False,
        required_scopes: list[str],
        requested_scopes: list[str],
    ) -> tuple[object, list[str]]:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        credentials = None
        token_scopes = self._load_token_scopes(token_file)
        if token_file.exists() and not force_reauth:
            credentials = Credentials.from_authorized_user_file(str(token_file), token_scopes or requested_scopes)
            if not self._has_required_scopes(token_scopes, required_scopes=required_scopes):
                LOGGER.info("Stored YouTube token is missing required scopes; requesting re-authentication.")
                credentials = None

        if credentials and not credentials.valid and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_file.write_text(credentials.to_json(), encoding="utf-8")
            token_scopes = self._load_token_scopes(token_file)

        if not credentials or not credentials.valid:
            if not allow_interactive:
                raise YouTubeAuthRequiredError(
                    "Missing required YouTube permissions for this channel; rerun channel authorization once in Studio."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), requested_scopes)
            credentials = flow.run_local_server(port=0, prompt="select_account consent")
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(credentials.to_json(), encoding="utf-8")
            token_scopes = self._load_token_scopes(token_file)

        return credentials, token_scopes

    def _has_required_scopes(
        self,
        granted_scopes: list[str] | tuple[str, ...] | set[str],
        *,
        required_scopes: list[str],
    ) -> bool:
        granted = set(granted_scopes or [])
        if FULL_SCOPE in granted:
            return True
        if MANAGE_SCOPE in granted and MANAGE_SCOPE in required_scopes:
            return True
        if MANAGE_SCOPE in granted and UPLOAD_SCOPE in required_scopes:
            return True
        if READONLY_SCOPE in granted and READONLY_SCOPE in required_scopes:
            return True
        return set(required_scopes).issubset(granted)

    def _load_channel_identity(self, credentials: object, token_scopes: list[str]) -> dict[str, str]:
        if not self._has_required_scopes(token_scopes, required_scopes=[READONLY_SCOPE]) and not self._has_required_scopes(
            token_scopes,
            required_scopes=[MANAGE_SCOPE],
        ):
            return {}
        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
        except ImportError:  # pragma: no cover
            return {}

        try:
            youtube = build("youtube", "v3", credentials=credentials)
            response = youtube.channels().list(part="snippet", mine=True).execute()
            items = response.get("items", [])
            if not items:
                return {}
            snippet = items[0].get("snippet", {})
            return {
                "authorized_channel_id": str(items[0].get("id", "")),
                "authorized_channel_title": str(snippet.get("title", "")),
            }
        except HttpError as exc:
            LOGGER.info("YouTube channel identity lookup skipped: %s", exc)
            return {}

    def _evaluate_channel_binding(
        self,
        token_scopes: list[str],
        channel_identity: dict[str, str],
    ) -> dict[str, object]:
        expected_channel_id = str(getattr(self.config.active_channel, "youtube_channel_id", "") or "").strip()
        expected_channel_title = str(getattr(self.config.active_channel, "youtube_channel_title", "") or "").strip()
        authorized_channel_id = str(channel_identity.get("authorized_channel_id", "")).strip()
        authorized_channel_title = str(channel_identity.get("authorized_channel_title", "")).strip()

        payload: dict[str, object] = {
            "expected_channel_id": expected_channel_id,
            "expected_channel_title": expected_channel_title,
            "authorized_channel_id": authorized_channel_id,
            "authorized_channel_title": authorized_channel_title,
            "granted_scopes": token_scopes,
            "identity_verifiable": bool(authorized_channel_id),
        }
        if expected_channel_id and authorized_channel_id and expected_channel_id != authorized_channel_id:
            payload["channel_mismatch"] = True
            payload["channel_mismatch_message"] = "Configured channel ID does not match the authorized YouTube token channel."
        elif expected_channel_id and authorized_channel_id:
            payload["channel_match"] = True
        return payload

    def _load_token_scopes(self, token_file: Path) -> list[str]:
        if not token_file.exists():
            return []
        try:
            payload = json.loads(token_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [str(scope) for scope in payload.get("scopes", []) if scope]

    @staticmethod
    def _format_channel_label(title: object, channel_id: object) -> str:
        name = str(title or "").strip()
        value = str(channel_id or "").strip()
        if name and value:
            return f'"{name}" ({value})'
        if name:
            return f'"{name}"'
        if value:
            return f"({value})"
        return "unknown channel"

    @staticmethod
    def extract_video_id(raw_value: str) -> str:
        value = str(raw_value or "").strip()
        if not value:
            return ""
        if "youtu" not in value:
            return value
        parsed = urlparse(value)
        if parsed.netloc.endswith("youtube.com"):
            if parsed.path.startswith("/shorts/"):
                return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
            query_value = parse_qs(parsed.query).get("v", [])
            if query_value:
                return query_value[0]
        if parsed.netloc.endswith("youtu.be"):
            return parsed.path.lstrip("/").split("/", 1)[0]
        return value
