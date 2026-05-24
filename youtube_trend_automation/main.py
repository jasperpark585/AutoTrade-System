from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

CODE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.getenv("YTA_RUNTIME_ROOT", str(CODE_ROOT))).resolve()
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from app.config import load_config
from app.generation.content_generator import ContentGenerator
from app.models import ArtifactStatus, RankedTopic, TopicDetail
from app.pipeline import Pipeline
from app.runtime.control import (
    is_studio_session_active,
    mark_server_update,
    scheduler_pause_message,
    send_current_studio_access_notice,
)
from app.scheduler.service import SchedulerService, run_scheduled_channels
from app.storage.repository import StorageRepository
from app.utils.logging import configure_logging
from app.youtube.uploader import YouTubeUploader
from kakao import KakaoNotifier


def emit_json(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text)
        return
    except UnicodeEncodeError:
        pass

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
            print(text)
            return
        except (ValueError, UnicodeEncodeError):
            pass

    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="backslashreplace"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube trend automation system")
    parser.add_argument(
        "--mode",
        choices=[
            "dry-run",
            "run-once",
            "render-only",
            "upload-only",
            "auth-youtube",
            "verify-youtube-channel",
            "delete-youtube-video",
            "repair-youtube-metadata",
            "kakao-auth-url",
            "kakao-exchange-code",
            "kakao-auth-auto",
            "send-kakao-studio-url",
            "mark-server-update",
            "scheduled-run",
            "scheduler",
            "studio",
            "studio-server",
        ],
        required=True,
    )
    parser.add_argument("--channel-id", help="Target channel id from studio settings")
    parser.add_argument("--video-id", action="append", help="YouTube video ID to delete; repeat for multiple videos")
    parser.add_argument("--code", help="OAuth authorization code for Kakao token exchange")
    parser.add_argument("--deploy-id", help="Unique server deployment id used for one-time notifications")
    parser.add_argument("--skip-render", action="store_true", help="Skip ffmpeg render stage")
    parser.add_argument("--skip-upload", action="store_true", help="Skip YouTube upload stage")
    parser.add_argument("--force", action="store_true", help="Allow duplicate topics in run-once mode")
    parser.add_argument(
        "--allow-network",
        choices=["true", "false"],
        help="Override network access for collectors and edge-tts",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "studio":
        return launch_studio()
    if args.mode == "studio-server":
        return launch_studio_server()

    config = load_config(PROJECT_ROOT, channel_id=args.channel_id)
    if args.allow_network is not None:
        config.allow_network = args.allow_network == "true"

    configure_logging(config.logs_dir, config.log_level)
    pipeline = Pipeline(config)

    if args.mode == "dry-run":
        result = pipeline.dry_run()
    elif args.mode == "run-once":
        result = pipeline.run_once(
            skip_render=args.skip_render,
            skip_upload=args.skip_upload,
            force=args.force,
        )
    elif args.mode == "render-only":
        result = pipeline.render_only()
    elif args.mode == "upload-only":
        result = pipeline.upload_only()
    elif args.mode == "auth-youtube":
        result = YouTubeUploader(config).authorize()
    elif args.mode == "verify-youtube-channel":
        result = YouTubeUploader(config).verify_channel_binding()
    elif args.mode == "delete-youtube-video":
        result = YouTubeUploader(config).delete_videos(args.video_id or [])
    elif args.mode == "repair-youtube-metadata":
        result = repair_youtube_metadata(PROJECT_ROOT, channel_id=args.channel_id, requested_video_ids=args.video_id or [])
    elif args.mode == "kakao-auth-url":
        notifier = KakaoNotifier(project_root=PROJECT_ROOT)
        emit_json(
            {
                "mode": "kakao-auth-url",
                "status": "success",
                "authorization_url": notifier.authorization_url(),
            }
        )
        return 0
    elif args.mode == "kakao-exchange-code":
        notifier = KakaoNotifier(project_root=PROJECT_ROOT)
        try:
            token_payload = notifier.exchange_authorization_code(args.code or "")
        except Exception as exc:
            emit_json(
                {
                    "mode": "kakao-exchange-code",
                    "status": "failed",
                    "message": str(exc),
                }
            )
            return 1
        emit_json(
            {
                "mode": "kakao-exchange-code",
                "status": "success",
                "message": "Kakao tokens saved to .env and runtime cache.",
                "has_refresh_token": bool(token_payload.get("refresh_token") or notifier.refresh_token),
            }
        )
        return 0
    elif args.mode == "kakao-auth-auto":
        notifier = KakaoNotifier(project_root=PROJECT_ROOT)
        try:
            token_payload = notifier.run_local_auth_flow()
        except Exception as exc:
            emit_json(
                {
                    "mode": "kakao-auth-auto",
                    "status": "failed",
                    "message": str(exc),
                }
            )
            return 1
        emit_json(
            {
                "mode": "kakao-auth-auto",
                "status": "success",
                "message": "Kakao OAuth flow completed and tokens were saved.",
                "has_refresh_token": bool(token_payload.get("refresh_token") or notifier.refresh_token),
            }
        )
        return 0
    elif args.mode == "send-kakao-studio-url":
        result_dict = send_current_studio_access_notice(PROJECT_ROOT)
        emit_json({"mode": "send-kakao-studio-url", **result_dict})
        return 0 if result_dict.get("status") == "success" else 1
    elif args.mode == "mark-server-update":
        marker = mark_server_update(PROJECT_ROOT, deploy_id=args.deploy_id or "", source="deploy")
        emit_json(
            {
                "mode": "mark-server-update",
                "status": "success",
                "message": "Server update marker saved.",
                "marker": marker,
            }
        )
        return 0
    elif args.mode == "scheduled-run":
        if is_studio_session_active(PROJECT_ROOT):
            message = scheduler_pause_message(PROJECT_ROOT)
            result_dict = {
                "mode": "scheduled-run",
                "status": "success",
                "warnings": [message],
                "details": {"scheduler_paused": True, "scheduler_message": message, "channel_results": []},
            }
            emit_json(result_dict)
            return 0

        result_dict = run_scheduled_channels(PROJECT_ROOT)
        emit_json(result_dict)
        return 0 if result_dict.get("status") in {"success", "skipped"} else 1
    else:
        scheduler = SchedulerService(config, pipeline)
        scheduler.start()
        return 0

    emit_json(result.to_dict())
    return 0 if result.status in {"success", "created", "skipped", "mocked", "partial"} else 1


def launch_studio() -> int:
    from studio_launcher import main as studio_main

    return studio_main()


def launch_studio_server() -> int:
    from studio_launcher import main as studio_main

    port = str(os.getenv("YTA_STUDIO_PORT", "8502")).strip() or "8502"
    listen_host = str(os.getenv("YTA_STUDIO_LISTEN_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    return studio_main(["--no-browser", "--port", port, "--listen-host", listen_host, "--session-source", "studio-server"])


def repair_youtube_metadata(project_root: Path, *, channel_id: str | None, requested_video_ids: list[str]) -> ArtifactStatus:
    config = load_config(project_root, channel_id=channel_id)
    configure_logging(config.logs_dir, config.log_level)
    repository = StorageRepository(config)
    latest_run = repository.latest_uploaded_run(channel_id=config.active_channel.id if config.active_channel else None)
    if not latest_run:
        return ArtifactStatus(
            status="failed",
            provider="youtube-update",
            message="No saved channel run metadata was found for metadata repair.",
        )

    topic = _topic_from_payload(latest_run.get("topic", {}))
    details = _details_from_payload(latest_run.get("details_collected", []))
    content = ContentGenerator(config).generate(topic, details)

    normalized_ids = [
        YouTubeUploader.extract_video_id(video_id)
        for video_id in requested_video_ids
        if YouTubeUploader.extract_video_id(video_id)
    ]
    if not normalized_ids:
        latest_upload = latest_run.get("artifacts", {}).get("upload", {}) if isinstance(latest_run.get("artifacts", {}), dict) else {}
        upload_extra = latest_upload.get("extra", {}) if isinstance(latest_upload, dict) and isinstance(latest_upload.get("extra", {}), dict) else {}
        candidate_values = [
            upload_extra.get("video_id", ""),
            latest_upload.get("path", "") if isinstance(latest_upload, dict) else "",
        ]
        normalized_ids = [
            YouTubeUploader.extract_video_id(candidate)
            for candidate in candidate_values
            if YouTubeUploader.extract_video_id(candidate)
        ]

    if not normalized_ids:
        return ArtifactStatus(
            status="failed",
            provider="youtube-update",
            message="No YouTube video ID was found in the latest run metadata. Pass --video-id to repair a specific upload.",
        )

    result = YouTubeUploader(config).update_video_metadata(normalized_ids, content)
    result.extra.setdefault("metadata_source_run_id", latest_run.get("run_id", ""))
    result.extra.setdefault("repaired_video_ids", normalized_ids)
    return result


def _topic_from_payload(payload: dict[str, object]) -> RankedTopic:
    return RankedTopic(
        normalized_topic=str(payload.get("normalized_topic", "") or ""),
        representative_title=str(payload.get("representative_title", "") or ""),
        score=float(payload.get("score", 0.0) or 0.0),
        sources=[str(item) for item in payload.get("sources", []) if item],
        mentions=[str(item) for item in payload.get("mentions", []) if item],
        keywords=[str(item) for item in payload.get("keywords", []) if item],
    )


def _details_from_payload(payload: object) -> list[TopicDetail]:
    items = payload if isinstance(payload, list) else []
    details: list[TopicDetail] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        details.append(
            TopicDetail(
                title=str(item.get("title", "") or ""),
                summary=str(item.get("summary", "") or ""),
                source=str(item.get("source", "") or ""),
                url=str(item.get("url", "") or "") or None,
                published_at=str(item.get("published_at", "") or "") or None,
            )
        )
    return details


if __name__ == "__main__":
    raise SystemExit(main())
