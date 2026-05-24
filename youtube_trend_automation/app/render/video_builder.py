from __future__ import annotations

import json
from pathlib import Path
import re
import shutil

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.config import AppConfig
from app.models import ArtifactStatus, GeneratedContent
from app.render.ffmpeg_wrapper import FFmpegWrapper
from app.utils.text import resolve_existing_font, wrap_text_by_width


class VideoBuilder:
    """Render short videos or longform story videos with ffmpeg."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.ffmpeg = FFmpegWrapper(config.project_root)
        self.video_encoder = self._select_video_encoder()

    def build(
        self,
        content: GeneratedContent,
        run_id: str,
        audio: ArtifactStatus,
        background: ArtifactStatus | None = None,
    ) -> ArtifactStatus:
        if content.content_format == "longform_story" and content.scenes:
            return self._build_story_video(content, run_id, audio, background)
        return self._build_short_video(content, run_id, audio, background)

    def _build_short_video(
        self,
        content: GeneratedContent,
        run_id: str,
        audio: ArtifactStatus,
        background: ArtifactStatus | None,
    ) -> ArtifactStatus:
        image_path = self.config.output_videos_dir / f"{run_id}.png"
        video_path = self.config.output_videos_dir / f"{run_id}.mp4"
        thumbnail_path = self.config.output_thumbnails_dir / f"{run_id}.jpg"
        if thumbnail_path.exists():
            self._create_short_poster_from_thumbnail(image_path, thumbnail_path)
        else:
            self._create_short_poster(image_path, content, background)

        if not self.config.render.enabled:
            return ArtifactStatus(status="skipped", provider="ffmpeg", message="Render disabled")

        if not self.ffmpeg.is_available():
            return self._save_render_plan(
                run_id,
                {
                    "reason": "ffmpeg not installed",
                    "mode": "short",
                    "image_path": str(image_path),
                    "audio_path": audio.path,
                    "intended_output": str(video_path),
                },
            )

        result = self.ffmpeg.run(
            self._build_single_image_clip_args(
                image_path=image_path,
                audio_path=self._valid_media_path(audio.path),
                output_path=video_path,
                duration=content.estimated_duration_seconds or self.config.render.default_duration_seconds,
                motion_template="standard",
            ),
            cwd=self.config.project_root,
        )
        if result.status == "failed":
            return result
        validation_error = self._validate_rendered_short_video(video_path, result)
        if validation_error:
            return ArtifactStatus(
                status="failed",
                provider="ffmpeg",
                message=validation_error,
                extra=result.extra,
            )
        return ArtifactStatus(
            status="created",
            provider="ffmpeg",
            path=str(video_path),
            message="Video rendered successfully.",
        )

    def _build_story_video(
        self,
        content: GeneratedContent,
        run_id: str,
        audio: ArtifactStatus,
        background: ArtifactStatus | None,
    ) -> ArtifactStatus:
        final_path = self.config.output_videos_dir / f"{run_id}.mp4"
        segments = self._resolve_story_segments(content, audio, background)
        if not segments:
            return ArtifactStatus(
                status="failed",
                provider="ffmpeg",
                message="Longform story assets were missing, so the video could not be rendered.",
            )

        if not self.config.render.enabled:
            return ArtifactStatus(status="skipped", provider="ffmpeg", message="Render disabled")

        if not self.ffmpeg.is_available():
            return self._save_render_plan(
                run_id,
                {
                    "reason": "ffmpeg not installed",
                    "mode": "longform_story",
                    "segments": segments,
                    "burn_in_subtitles": bool(getattr(self.config.active_channel, "burn_in_subtitles", True)),
                    "background_music_path": self._resolve_background_music_path(),
                    "intended_output": str(final_path),
                },
            )

        work_dir = self.config.output_videos_dir / f"{run_id}_clips"
        work_dir.mkdir(parents=True, exist_ok=True)

        clip_paths: list[Path] = []
        cleanup_targets: list[Path] = []
        try:
            for index, segment in enumerate(segments):
                clip_path = work_dir / f"clip_{index:02}.mp4"
                result = self.ffmpeg.run(
                    self._build_story_segment_args(
                        image_paths=[Path(path) for path in segment["image_paths"]],
                        audio_path=self._valid_media_path(str(segment.get("audio_path", ""))),
                        output_path=clip_path,
                        duration_seconds=float(segment["duration_seconds"]),
                        motion_template=str(segment.get("motion_template", "slow_drift")),
                        title_text=str(segment.get("title_text", "")),
                    ),
                    cwd=self.config.project_root,
                )
                if result.status == "failed":
                    return result
                clip_paths.append(clip_path)

            concat_source = work_dir / "story_base.mp4"
            concat_result = self.ffmpeg.run(
                self._build_concat_args(clip_paths=clip_paths, output_path=concat_source),
                cwd=self.config.project_root,
            )
            if concat_result.status == "failed":
                return concat_result

            current_video = concat_source
            notes: list[str] = []

            bgm_path = self._resolve_background_music_path()
            if bgm_path:
                mixed_path = work_dir / "story_with_bgm.mp4"
                mix_result = self.ffmpeg.run(
                    self._build_background_music_mix_args(
                        input_video=current_video,
                        background_music=Path(bgm_path),
                        output_path=mixed_path,
                        duration_seconds=float(sum(float(segment["duration_seconds"]) for segment in segments)),
                        volume_percent=int(getattr(self.config.active_channel, "background_music_volume", 18) or 18),
                    ),
                    cwd=self.config.project_root,
                )
                if mix_result.status == "created":
                    cleanup_targets.append(current_video)
                    current_video = mixed_path
                    notes.append("background music mixed")
                else:
                    notes.append("background music mix failed")

            subtitle_path = self.config.output_subtitles_dir / f"{run_id}.srt"
            if bool(getattr(self.config.active_channel, "burn_in_subtitles", True)) and subtitle_path.exists():
                subtitle_output = final_path
                subtitle_result = self.ffmpeg.run(
                    self._build_burn_subtitles_args(
                        input_video=current_video,
                        subtitle_path=subtitle_path,
                        output_path=subtitle_output,
                        longform=True,
                    ),
                    cwd=self.config.project_root,
                )
                if subtitle_result.status == "created":
                    if current_video != final_path:
                        cleanup_targets.append(current_video)
                    current_video = subtitle_output
                    notes.append("subtitles burned in")
                else:
                    if current_video != final_path:
                        cleanup_targets.append(current_video)
                    current_video = self._copy_if_needed(current_video, final_path)
                    notes.append("subtitle burn-in failed")
            else:
                if current_video != final_path:
                    cleanup_targets.append(current_video)
                current_video = self._copy_if_needed(current_video, final_path)

            self._cleanup_paths(cleanup_targets)
            return ArtifactStatus(
                status="created",
                provider="ffmpeg",
                path=str(current_video),
                message="Longform story video rendered successfully.",
                extra={
                    "segments": segments,
                    "background_music_path": bgm_path,
                    "burn_in_subtitles": bool(getattr(self.config.active_channel, "burn_in_subtitles", True)),
                    "notes": notes,
                },
            )
        finally:
            self._remove_path(work_dir)

    def _resolve_story_segments(
        self,
        content: GeneratedContent,
        audio: ArtifactStatus,
        background: ArtifactStatus | None,
    ) -> list[dict[str, object]]:
        if background is None or not background.extra:
            return []

        hook_images = background.extra.get("hook_images", [])
        if not hook_images:
            legacy_hook = str(background.extra.get("hook_image", "")).strip()
            if legacy_hook:
                hook_images = [legacy_hook]
        scene_images = background.extra.get("scene_images", [])
        audio_segments = audio.extra.get("segments", []) if audio.extra else []
        audio_by_label: dict[str, str] = {}
        duration_by_label: dict[str, float] = {}
        if isinstance(audio_segments, list):
            for item in audio_segments:
                if isinstance(item, dict):
                    label = str(item.get("label", "")).strip()
                    path = str(item.get("path", "")).strip()
                    if not label:
                        continue
                    audio_by_label[label] = path
                    duration = self._segment_duration_seconds(item, path)
                    if duration is not None:
                        duration_by_label[label] = duration

        resolved: list[dict[str, object]] = []
        if isinstance(hook_images, list) and hook_images:
            resolved.append(
                {
                    "label": "hook",
                    "title_text": content.hook_title or "3분 몰입 훅",
                    "image_paths": [str(path) for path in hook_images if str(path).strip()],
                    "audio_path": audio_by_label.get("hook", ""),
                    "duration_seconds": max(
                        self.config.generation.hook_duration_seconds,
                        self._estimate_seconds(content.hook_script),
                    ),
                    "motion_template": str(getattr(self.config.active_channel, "hook_motion_template", "dramatic_push") or "dramatic_push"),
                }
            )

        if isinstance(scene_images, list):
            for scene in content.scenes:
                image_paths: list[str] = []
                for item in scene_images:
                    if isinstance(item, dict) and int(item.get("index", 0)) == scene.index:
                        image_paths = self._image_paths_from_scene_item(item)
                        break
                if not image_paths:
                    continue
                resolved.append(
                    {
                        "label": f"scene_{scene.index:02}",
                        "title_text": scene.title,
                        "image_paths": image_paths,
                        "audio_path": audio_by_label.get(f"scene_{scene.index:02}", ""),
                        "duration_seconds": float(scene.duration_seconds or self._estimate_seconds(scene.narration)),
                        "motion_template": "slow_drift",
                    }
                )
        return resolved

    def _build_story_segment_args(
        self,
        *,
        image_paths: list[Path],
        audio_path: str | None,
        output_path: Path,
        duration_seconds: float,
        motion_template: str,
        title_text: str,
    ) -> list[str]:
        duration_seconds = max(4.0, float(duration_seconds))
        image_paths = image_paths or []
        fade_duration = 0.75 if len(image_paths) > 1 else 0.0
        image_hold_seconds = max(2.6, (duration_seconds + fade_duration * max(0, len(image_paths) - 1)) / max(1, len(image_paths)))

        args = ["-y"]
        for image_path in image_paths:
            args.extend(["-loop", "1", "-t", f"{image_hold_seconds:.3f}", "-i", str(image_path)])

        if audio_path:
            args.extend(["-i", audio_path])
        else:
            args.extend(["-f", "lavfi", "-t", f"{duration_seconds:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])

        audio_index = len(image_paths)
        filter_complex = self._build_story_filter_complex(
            image_count=len(image_paths),
            audio_index=audio_index,
            duration_seconds=duration_seconds,
            image_hold_seconds=image_hold_seconds,
            fade_duration=fade_duration,
            motion_template=motion_template,
            title_text=title_text,
        )

        return [
            *args,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{duration_seconds:.3f}",
            "-threads",
            "1",
            *self._video_codec_args(longform=True),
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    def _build_story_filter_complex(
        self,
        *,
        image_count: int,
        audio_index: int,
        duration_seconds: float,
        image_hold_seconds: float,
        fade_duration: float,
        motion_template: str,
        title_text: str,
    ) -> str:
        width = self.config.render.width
        height = self.config.render.height
        fps = self._story_fps()
        frames = max(1, int(round(image_hold_seconds * fps)))

        graph_parts: list[str] = []
        for index in range(image_count):
            graph_parts.append(
                self._visual_input_filter(
                    input_index=index,
                    output_label=f"v{index}",
                    width=width,
                    height=height,
                    frames=frames,
                    fps=fps,
                    motion_template=motion_template,
                )
            )

        current_label = "v0"
        if image_count > 1:
            offset = image_hold_seconds - fade_duration
            for index in range(1, image_count):
                next_label = f"x{index}"
                graph_parts.append(
                    f"[{current_label}][v{index}]xfade=transition=fade:duration={fade_duration:.3f}:offset={offset:.3f}[{next_label}]"
                )
                current_label = next_label
                offset += image_hold_seconds - fade_duration

        title_safe = self._escape_drawtext(title_text[:38])
        title_filter = self._drawtext_filter(title_safe, fontcolor="white", fontsize=34, x=84, y=104)
        graph_parts.append(
            f"[{current_label}]drawbox=x=48:y=70:w={width - 96}:h=116:color=black@0.24:t=fill,"
            f"drawbox=x=48:y={height - 210}:w={width - 96}:h=126:color=black@0.22:t=fill,"
            f"{title_filter},"
            f"format=yuv420p[vout]"
        )
        graph_parts.append(
            f"[{audio_index}:a]volume=1.0,apad=whole_dur={duration_seconds:.3f}[aout]"
        )
        return ";".join(graph_parts)

    def _visual_input_filter(
        self,
        *,
        input_index: int,
        output_label: str,
        width: int,
        height: int,
        frames: int,
        fps: int,
        motion_template: str,
    ) -> str:
        if motion_template == "dramatic_push":
            zoom_expr = "min(zoom+0.0018,1.22)"
            scale_factor = 1.32
        elif motion_template == "reveal_pan":
            zoom_expr = "min(zoom+0.0012,1.16)"
            scale_factor = 1.24
        else:
            zoom_expr = "min(zoom+0.0007,1.08)"
            scale_factor = 1.16
        scaled_width = max(width + 120, int(round(width * scale_factor)))
        scaled_height = max(height + 120, int(round(height * scale_factor)))
        return (
            f"[{input_index}:v]"
            f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase,"
            f"crop={scaled_width}:{scaled_height},"
            f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps},"
            f"trim=duration={frames / fps:.3f},setpts=PTS-STARTPTS[{output_label}]"
        )

    def _build_concat_args(self, *, clip_paths: list[Path], output_path: Path) -> list[str]:
        concat_file = output_path.with_suffix(".txt")
        concat_file.write_text(
            "\n".join(f"file '{path.resolve().as_posix()}'" for path in clip_paths),
            encoding="utf-8",
        )
        return [
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(output_path),
        ]

    def _build_background_music_mix_args(
        self,
        *,
        input_video: Path,
        background_music: Path,
        output_path: Path,
        duration_seconds: float,
        volume_percent: int,
    ) -> list[str]:
        volume = max(0.0, min(1.0, volume_percent / 100.0))
        return [
            "-y",
            "-i",
            str(input_video),
            "-stream_loop",
            "-1",
            "-i",
            str(background_music),
            "-filter_complex",
            f"[1:a]volume={volume:.3f},atrim=duration={duration_seconds:.3f},asetpts=N/SR/TB[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]

    def _build_burn_subtitles_args(
        self,
        *,
        input_video: Path,
        subtitle_path: Path,
        output_path: Path,
        longform: bool = False,
    ) -> list[str]:
        return [
            "-y",
            "-i",
            str(input_video),
            "-vf",
            self._subtitle_filter_arg(subtitle_path, longform=longform),
            "-threads",
            "1",
            *self._video_codec_args(longform=longform),
            "-c:a",
            "copy",
            str(output_path),
        ]

    def _build_single_image_clip_args(
        self,
        *,
        image_path: Path,
        audio_path: str | None,
        output_path: Path,
        duration: int,
        motion_template: str,
    ) -> list[str]:
        fps = max(12, min(self.config.render.fps, 24))
        args = ["-y", "-loop", "1", "-framerate", str(fps), "-i", str(image_path)]
        if audio_path:
            args.extend(["-i", audio_path])
        else:
            args.extend(["-f", "lavfi", "-t", str(max(1, int(duration))), "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])
        return [
            *args,
            "-vf",
            f"fps={fps},format=yuv420p",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-t",
            str(max(1, int(duration))),
            "-threads",
            "1",
            *self._video_codec_args(longform=False),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    def _create_short_poster(
        self,
        image_path: Path,
        content: GeneratedContent,
        background: ArtifactStatus | None,
    ) -> None:
        width = self.config.render.width
        height = self.config.render.height
        image = Image.new("RGB", (width, height), "#07111f")
        self._paint_background(image, width, height, background)
        draw = ImageDraw.Draw(image)

        draw.rounded_rectangle((60, 90, width - 60, height - 90), radius=46, fill=(5, 12, 24, 190))
        draw.rounded_rectangle((96, 132, 336, 194), radius=26, fill="#f97316")

        font_path = resolve_existing_font(self.config.fonts_dir)
        badge_font = self._load_font(font_path, 32)
        title_font = self._load_font(font_path, 64)
        body_font = self._load_font(font_path, 34)

        draw.text((130, 148), self.config.generation.channel_name[:12], fill="#fff7ed", font=badge_font)
        title_text = self._clean_display_text(
            content.thumbnail_text or content.video_title or content.topic.representative_title
        )
        title_lines = wrap_text_by_width(
            title_text,
            measure=lambda value: draw.textbbox((0, 0), value, font=title_font)[2],
            max_width=width - 220,
            max_lines=4,
        )
        y = 280
        for line in title_lines:
            draw.text((102, y), line, fill="#f8fafc", font=title_font)
            y += 86

        draw.text((102, y + 30), "핵심 포인트", fill="#bfdbfe", font=body_font)
        y += 98
        for point in content.detail_points[:4]:
            clean_point = self._clean_display_text(point)
            if not clean_point:
                continue
            point_lines = wrap_text_by_width(
                f"- {clean_point}",
                measure=lambda value: draw.textbbox((0, 0), value, font=body_font)[2],
                max_width=width - 220,
                max_lines=2,
            )
            for line in point_lines:
                draw.text((108, y), line, fill="#e5eefb", font=body_font)
                y += 48
            y += 12

        image.save(image_path)

    def _create_short_poster_from_thumbnail(self, image_path: Path, thumbnail_path: Path) -> None:
        width = self.config.render.width
        height = self.config.render.height
        image = Image.open(thumbnail_path).convert("RGB")
        image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
        image.save(image_path)

    def _paint_background(
        self,
        image: Image.Image,
        width: int,
        height: int,
        background: ArtifactStatus | None,
    ) -> None:
        if background and background.status == "created" and background.path:
            source_path = Path(background.path)
            if source_path.exists() and source_path.is_file():
                source = Image.open(source_path).convert("RGB")
                source = ImageOps.fit(source, (width, height), method=Image.Resampling.LANCZOS)
                source = source.filter(ImageFilter.GaussianBlur(radius=4))
                image.paste(source)
                overlay = Image.new("RGBA", (width, height), (6, 17, 31, 165))
                image.paste(overlay, mask=overlay.split()[3])
                return

        draw = ImageDraw.Draw(image)
        top = (6, 23, 46)
        middle = (11, 35, 63)
        bottom = (7, 17, 31)
        for y in range(height):
            if y < height // 2:
                factor = y / max(height // 2, 1)
                color = tuple(int(top[index] + (middle[index] - top[index]) * factor) for index in range(3))
            else:
                factor = (y - height // 2) / max(height // 2, 1)
                color = tuple(int(middle[index] + (bottom[index] - middle[index]) * factor) for index in range(3))
            draw.line((0, y, width, y), fill=color)

    @staticmethod
    def _clean_display_text(text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"https?://\S+|www\.\S+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\s*-\s*(?:연합뉴스|뉴시스|뉴스1|뉴스핌|드림투데이|매일경제|한국경제|서울경제|아시아경제|이데일리|머니투데이|조선비즈|중앙일보|한겨레|경향신문|국민일보|문화일보|파이낸셜뉴스|헤럴드경제|SBS\s*Biz|SBS|KBS|MBC|JTBC|YTN|MSN|네이트)\b.*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\[[^\]]*(?:기사|보도|속보|뉴스)[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" -,:;_|")

    def _resolve_background_music_path(self) -> str:
        channel = self.config.active_channel
        manual = str(getattr(channel, "background_music_path", "") or "").strip()
        if manual:
            path = Path(manual).expanduser()
            if not path.is_absolute():
                project_relative = (self.config.project_root / path).resolve()
                music_relative = (self.config.music_dir / path).resolve()
                path = music_relative if music_relative.exists() else project_relative
            if path.exists() and path.is_file():
                return str(path)

        music_dir = self.config.music_dir
        if music_dir.exists():
            for pattern in ("*.mp3", "*.wav", "*.m4a", "*.aac"):
                matches = sorted(music_dir.glob(pattern))
                if matches:
                    return str(matches[0])
        return ""

    def _subtitle_filter_arg(self, subtitle_path: Path, *, longform: bool = False) -> str:
        subtitle_value = str(subtitle_path.resolve()).replace("\\", "/").replace(":", "\\:")
        font_name = "Malgun Gothic"
        font_size = 24 if longform else 18
        margin_v = 34 if longform else 42
        outline = 2 if longform else 1
        style = (
            f"FontName={font_name},FontSize={font_size},PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00202020,BackColour=&H64000000,BorderStyle=3,"
            f"Outline={outline},Shadow=0,MarginV={margin_v},Alignment=2"
        )
        return f"subtitles='{subtitle_value}':force_style='{style}'"

    def _fontfile_for_ffmpeg(self) -> str:
        font_path = resolve_existing_font(self.config.fonts_dir)
        if font_path is None:
            return ""
        return str(font_path).replace("\\", "/").replace(":", "\\:")

    def _drawtext_filter(
        self,
        text: str,
        *,
        fontcolor: str,
        fontsize: int,
        x: int,
        y: int,
    ) -> str:
        fontfile = self._fontfile_for_ffmpeg()
        font_arg = f":fontfile='{fontfile}'" if fontfile else ""
        return f"drawtext=text='{text}'{font_arg}:fontcolor={fontcolor}:fontsize={fontsize}:x={x}:y={y}"

    @staticmethod
    def _image_paths_from_scene_item(item: dict[str, object]) -> list[str]:
        paths = [str(path) for path in item.get("paths", []) if str(path).strip()]
        if paths:
            return paths
        legacy_path = str(item.get("path", "")).strip()
        return [legacy_path] if legacy_path else []

    @staticmethod
    def _escape_drawtext(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    @staticmethod
    def _valid_media_path(raw_path: str | None) -> str | None:
        value = str(raw_path or "").strip()
        if not value:
            return None
        path = Path(value)
        if path.exists() and path.is_file() and path.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac"}:
            return str(path)
        return None

    def _copy_if_needed(self, source: Path, target: Path) -> Path:
        if source == target:
            return source
        target.write_bytes(source.read_bytes())
        return target

    def _save_render_plan(self, run_id: str, payload: dict[str, object]) -> ArtifactStatus:
        plan_path = self.config.output_videos_dir / f"{run_id}.render-plan.json"
        plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return ArtifactStatus(
            status="skipped",
            provider="ffmpeg",
            path=str(plan_path),
            message="ffmpeg not installed; saved render plan instead of MP4.",
        )

    def _validate_rendered_short_video(self, video_path: Path, render_result: ArtifactStatus) -> str:
        if not video_path.exists():
            return "Rendered MP4 file was not created."
        try:
            size_bytes = video_path.stat().st_size
        except OSError:
            return "Rendered MP4 file could not be inspected."
        if size_bytes < 4096:
            stderr = str((render_result.extra or {}).get("stderr", "")).strip()
            if stderr:
                return stderr
            return "Rendered MP4 was too small to upload."
        return ""

    @staticmethod
    def _estimate_seconds(text: str) -> int:
        compact = "".join(text.split())
        return max(30, int(len(compact) / 4.4))

    def _resolve_story_segments(
        self,
        content: GeneratedContent,
        audio: ArtifactStatus,
        background: ArtifactStatus | None,
    ) -> list[dict[str, object]]:
        if background is None or not background.extra:
            return []

        hook_images = background.extra.get("hook_images", [])
        if not hook_images:
            legacy_hook = str(background.extra.get("hook_image", "")).strip()
            if legacy_hook:
                hook_images = [legacy_hook]
        scene_images = background.extra.get("scene_images", [])
        audio_segments = audio.extra.get("segments", []) if audio.extra else []
        audio_by_label: dict[str, str] = {}
        duration_by_label: dict[str, float] = {}
        if isinstance(audio_segments, list):
            for item in audio_segments:
                if isinstance(item, dict):
                    label = str(item.get("label", "")).strip()
                    path = str(item.get("path", "")).strip()
                    if not label:
                        continue
                    audio_by_label[label] = path
                    duration = self._segment_duration_seconds(item, path)
                    if duration is not None:
                        duration_by_label[label] = duration

        resolved: list[dict[str, object]] = []
        if isinstance(hook_images, list) and hook_images:
            resolved.append(
                {
                    "label": "hook",
                    "title_text": "",
                    "image_paths": [str(path) for path in hook_images if str(path).strip()],
                    "audio_path": audio_by_label.get("hook", ""),
                    "duration_seconds": float(
                        duration_by_label.get("hook")
                        or content.hook_duration_seconds
                        or self._estimate_seconds(content.hook_script)
                    ),
                    "motion_template": str(getattr(self.config.active_channel, "hook_motion_template", "dramatic_push") or "dramatic_push"),
                }
            )

        if isinstance(scene_images, list):
            for scene in content.scenes:
                image_paths: list[str] = []
                for item in scene_images:
                    if isinstance(item, dict) and int(item.get("index", 0)) == scene.index:
                        image_paths = self._image_paths_from_scene_item(item)
                        break
                if not image_paths:
                    continue
                resolved.append(
                    {
                        "label": f"scene_{scene.index:02}",
                        "title_text": "",
                        "image_paths": image_paths,
                        "audio_path": audio_by_label.get(f"scene_{scene.index:02}", ""),
                        "duration_seconds": float(
                            duration_by_label.get(f"scene_{scene.index:02}")
                            or scene.duration_seconds
                            or self._estimate_seconds(scene.narration)
                        ),
                        "motion_template": "slow_drift",
                    }
                )
        return resolved

    def _build_story_filter_complex(
        self,
        *,
        image_count: int,
        audio_index: int,
        duration_seconds: float,
        image_hold_seconds: float,
        fade_duration: float,
        motion_template: str,
        title_text: str,
    ) -> str:
        width = self.config.render.width
        height = self.config.render.height
        fps = self._story_fps()
        frames = max(1, int(round(image_hold_seconds * fps)))

        graph_parts: list[str] = []
        for index in range(image_count):
            graph_parts.append(
                self._visual_input_filter(
                    input_index=index,
                    output_label=f"v{index}",
                    width=width,
                    height=height,
                    frames=frames,
                    fps=fps,
                    motion_template=motion_template,
                )
            )

        current_label = "v0"
        if image_count > 1:
            offset = image_hold_seconds - fade_duration
            for index in range(1, image_count):
                next_label = f"x{index}"
                graph_parts.append(
                    f"[{current_label}][v{index}]xfade=transition=fade:duration={fade_duration:.3f}:offset={offset:.3f}[{next_label}]"
                )
                current_label = next_label
                offset += image_hold_seconds - fade_duration

        if title_text.strip():
            title_safe = self._escape_drawtext(title_text[:38])
            title_filter = self._drawtext_filter(title_safe, fontcolor="white", fontsize=34, x=84, y=104)
            graph_parts.append(
                f"[{current_label}]drawbox=x=48:y=70:w={width - 96}:h=116:color=black@0.24:t=fill,"
                f"{title_filter},"
                f"format=yuv420p[vout]"
            )
        else:
            graph_parts.append(f"[{current_label}]format=yuv420p[vout]")

        graph_parts.append(f"[{audio_index}:a]volume=1.0,apad=whole_dur={duration_seconds:.3f}[aout]")
        return ";".join(graph_parts)

    def _story_fps(self) -> int:
        return max(8, min(self.config.render.fps, 12))

    def _segment_duration_seconds(self, payload: dict[str, object], media_path: str) -> float | None:
        try:
            stored = float(payload.get("duration_seconds", 0) or 0)
        except (TypeError, ValueError):
            stored = 0.0
        if stored > 0:
            return stored
        return self._probe_media_duration(media_path)

    def _probe_media_duration(self, path: str) -> float | None:
        media_path = self._valid_media_path(path)
        if not media_path:
            return None

        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            ffmpeg_binary = str(getattr(self.ffmpeg, "binary", "") or "").strip()
            if ffmpeg_binary:
                ffmpeg_path = Path(ffmpeg_binary)
                for candidate in (ffmpeg_path.with_name("ffprobe.exe"), ffmpeg_path.with_name("ffprobe")):
                    if candidate.exists():
                        ffprobe = str(candidate)
                        break
        if not ffprobe:
            return None

        process = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                media_path,
            ],
            cwd=self.config.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            return None
        try:
            return max(0.25, float((process.stdout or "").strip()))
        except ValueError:
            return None

    def _select_video_encoder(self) -> str:
        encoders = self.ffmpeg.available_encoders()
        for encoder in ("libx264", "mpeg4", "h264_mf", "h264_qsv", "mpeg2video"):
            if encoder in encoders:
                return encoder
        return "mpeg4"

    def _video_codec_args(self, *, longform: bool) -> list[str]:
        encoder = self.video_encoder
        args = ["-c:v", encoder]
        if encoder == "libx264":
            args.extend(
                [
                    "-preset",
                    "veryfast" if longform else "ultrafast",
                    "-crf",
                    "31" if longform else "27",
                    "-pix_fmt",
                    "yuv420p",
                ]
            )
        elif encoder == "mpeg4":
            args.extend(["-q:v", "8" if longform else "5"])
        elif encoder.startswith("h264_"):
            args.extend(["-b:v", "1400k" if longform else "2600k"])
        return args

    @staticmethod
    def _cleanup_paths(paths: list[Path]) -> None:
        for path in paths:
            VideoBuilder._remove_path(path)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return
        path.unlink(missing_ok=True)

    @staticmethod
    def _load_font(font_path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if font_path is None:
            return ImageFont.load_default()
        return ImageFont.truetype(str(font_path), size)
