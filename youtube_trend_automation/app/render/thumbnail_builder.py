from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.config import AppConfig
from app.generation.ai_service import OpenAIContentService
from app.models import ArtifactStatus, GeneratedContent
from app.studio.presets import preset_by_key
from app.utils.text import resolve_existing_font, wrap_text_by_width


class ThumbnailBuilder:
    """Create YouTube thumbnails for both shorts and longform videos."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.ai = OpenAIContentService(config)

    def build(
        self,
        content: GeneratedContent,
        run_id: str,
        background: ArtifactStatus | None = None,
    ) -> ArtifactStatus:
        if not self.config.thumbnail.enabled:
            return ArtifactStatus(status="skipped", provider="thumbnail", message="Thumbnail generation disabled.")

        output_path = self.config.output_thumbnails_dir / f"{run_id}.jpg"
        base_image_path = self._resolve_base_image(content, run_id, background)
        self._draw_thumbnail(output_path, content, base_image_path)
        extra = {"base_image": str(base_image_path)} if base_image_path else {}
        return ArtifactStatus(
            status="created",
            provider="thumbnail",
            path=str(output_path),
            message="Thumbnail created successfully.",
            extra=extra,
        )

    def _resolve_base_image(
        self,
        content: GeneratedContent,
        run_id: str,
        background: ArtifactStatus | None,
    ) -> Path | None:
        channel = self.config.active_channel
        if channel and channel.manual_thumbnail_path:
            manual_path = Path(channel.manual_thumbnail_path).expanduser()
            if not manual_path.is_absolute():
                manual_path = (self.config.project_root / manual_path).resolve()
            if manual_path.exists():
                return manual_path

        if content.content_format == "short" and channel is not None:
            preset = preset_by_key(channel.preset_key)
            if preset.key in {"economy_news", "welfare_news"}:
                reused_background = self._background_base_image(background)
                if reused_background is not None:
                    return reused_background
                return None

        if channel and channel.manual_thumbnail_prompt.strip():
            ai_path = self.config.output_thumbnails_dir / f"{run_id}.thumb-base.png"
            if self.ai.generate_image(prompt=channel.manual_thumbnail_prompt.strip(), output_path=ai_path):
                return ai_path

        if content.content_format == "short":
            reused_background = self._background_base_image(background)
            if reused_background is not None:
                return reused_background

        if content.content_format == "longform_story" and content.thumbnail_prompt.strip():
            ai_path = self.config.output_thumbnails_dir / f"{run_id}.story-thumb-base.png"
            if self.ai.generate_image(prompt=content.thumbnail_prompt.strip(), output_path=ai_path):
                return ai_path

        if content.thumbnail_prompt.strip():
            ai_path = self.config.output_thumbnails_dir / f"{run_id}.thumb-topic-base.png"
            if self.ai.generate_image(prompt=content.thumbnail_prompt.strip(), output_path=ai_path):
                return ai_path

        reused_background = self._background_base_image(background)
        if reused_background is not None:
            return reused_background

        return None

    @staticmethod
    def _background_base_image(background: ArtifactStatus | None) -> Path | None:
        if not background or background.status != "created" or not background.path:
            return None

        path = Path(background.path)
        if path.is_dir():
            hook_image = str(background.extra.get("hook_image", "")).strip() if background.extra else ""
            if hook_image and Path(hook_image).exists():
                return Path(hook_image)
            scene_images = background.extra.get("scene_images", []) if background.extra else []
            if isinstance(scene_images, list):
                for item in scene_images:
                    if isinstance(item, dict):
                        for raw_path in item.get("paths", []):
                            candidate = Path(str(raw_path).strip())
                            if candidate.exists():
                                return candidate
        if path.exists():
            return path
        return None

    def _draw_thumbnail(self, output_path: Path, content: GeneratedContent, base_image_path: Path | None) -> None:
        if content.content_format == "short":
            width = self.config.render.width
            height = self.config.render.height
        else:
            width = self.config.thumbnail.width
            height = self.config.thumbnail.height

        if base_image_path is not None and base_image_path.exists():
            image = Image.open(base_image_path).convert("RGB")
            image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
            blur_radius = 0.15 if content.content_format == "longform_story" else 0.0
            image = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        else:
            image = Image.new("RGB", (width, height), "#09111d")
            self._paint_background(ImageDraw.Draw(image), width, height)

        font_path = resolve_existing_font(self.config.fonts_dir)
        if content.content_format == "longform_story":
            image = self._draw_story_thumbnail(image, content, font_path)
        elif content.content_format == "short":
            image = self._draw_short_thumbnail(image, content, font_path)
        else:
            image = self._draw_default_thumbnail(image, content, font_path)

        image.save(output_path, format="JPEG", quality=92, optimize=True)

    def _draw_default_thumbnail(
        self,
        image: Image.Image,
        content: GeneratedContent,
        font_path: Path | None,
    ) -> Image.Image:
        width, height = image.size
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((40, 40, width - 40, height - 40), radius=46, fill=(5, 12, 24, 160))
        draw.rounded_rectangle((70, 68, 338, 132), radius=28, fill="#f97316")

        badge_font = self._load_font(font_path, 34)
        headline_font = self._load_font(font_path, 76)
        sub_font = self._load_font(font_path, 30)
        body_font = self._load_font(font_path, 28)

        draw.text((102, 84), self.config.thumbnail.label, fill="#fff7ed", font=badge_font)
        draw.text((84, 166), "TODAY'S ISSUE", fill="#bfdbfe", font=sub_font)

        headline_text = self._clean_display_text(
            content.thumbnail_text or content.video_title or content.topic.representative_title
        )
        headline_lines = wrap_text_by_width(
            headline_text,
            measure=lambda value: draw.textbbox((0, 0), value, font=headline_font)[2],
            max_width=width - 220,
            max_lines=3,
        )
        current_y = 228
        for line in headline_lines:
            draw.text((84, current_y), line, fill="#f8fafc", font=headline_font)
            current_y += 92

        draw.rounded_rectangle((84, height - 212, width - 84, height - 90), radius=30, fill="#eff6ff")
        draw.text((112, height - 192), "AI briefing template", fill="#1d4ed8", font=body_font)
        footer = f"{datetime.now():%Y-%m-%d}  {self.config.thumbnail.footer}"
        draw.text((112, height - 146), footer, fill="#475569", font=sub_font)
        footer_text = self._clean_display_text(content.video_title or content.topic.representative_title)
        draw.text((112, height - 106), footer_text[:50], fill="#0f172a", font=sub_font)
        return image

    def _draw_short_thumbnail(
        self,
        image: Image.Image,
        content: GeneratedContent,
        font_path: Path | None,
    ) -> Image.Image:
        width, height = image.size
        preset = preset_by_key(str(getattr(content, "preset_key", "") or ""))
        is_news = preset.key == "economy_news"
        is_welfare = preset.key == "welfare_news"
        rgba = image.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        board_left = int(width * 0.135)
        board_top = int(height * 0.49)
        board_right = int(width * 0.865)
        board_bottom = int(height * 0.73)

        if is_news:
            draw.rectangle((0, 0, width, int(height * 0.18)), fill=(5, 9, 18, 68))
            draw.rectangle((0, int(height * 0.78), width, height), fill=(4, 8, 15, 92))
            draw.rounded_rectangle(
                (96, 360, width - 96, int(height * 0.60)),
                radius=42,
                fill=(7, 16, 33, 124),
                outline=(255, 255, 255, 34),
                width=2,
            )
            draw.rounded_rectangle(
                (118, 392, width - 118, int(height * 0.57)),
                radius=34,
                fill=(5, 12, 28, 78),
            )
        elif is_welfare:
            draw.rounded_rectangle(
                (board_left, board_top, board_right, board_bottom),
                radius=28,
                fill=(255, 255, 255, 28),
                outline=(255, 255, 255, 70),
                width=3,
            )
            draw.rounded_rectangle(
                (board_left + 18, board_top + 18, board_right - 18, board_bottom - 18),
                radius=22,
                fill=(255, 255, 255, 34),
            )
        else:
            draw.rectangle((0, 0, width, int(height * 0.20)), fill=(6, 10, 18, 110))
            draw.rectangle((0, int(height * 0.76), width, height), fill=(5, 9, 16, 160))

        draw.rounded_rectangle((44, 48, width - 44, height - 48), radius=42, outline=(255, 255, 255, 38), width=4)
        if not is_news and not is_welfare:
            draw.rounded_rectangle((44, 44, width - 44, 134), radius=28, fill=(214, 35, 35, 225))

        rgba = Image.alpha_composite(rgba, overlay)
        draw = ImageDraw.Draw(rgba)

        if is_news:
            badge_font = self._load_font(font_path, 34)
            sub_font = self._load_font(font_path, 34)
            footer_font = self._load_font(font_path, 28)
            headline_panel = (116, 414, width - 116, int(height * 0.58))
            headline_font, headline_lines, headline_total_height = self._fit_wrapped_text_block(
                draw,
                self._clean_display_text(
                    content.thumbnail_text or content.video_title or content.topic.representative_title
                ),
                font_path=font_path,
                start_size=116,
                min_size=82,
                max_width=(headline_panel[2] - headline_panel[0]) - 108,
                max_lines=3,
                max_height=(headline_panel[3] - headline_panel[1]) - 138,
                line_gap=24,
            )
            badge_fill = (10, 20, 42, 220)
            badge_x = 86
            badge_y = 286
            headline_colors = ["#ffd44d", "#ffffff", "#ffffff"]
            stroke_colors = ["#fff7ed", "#111111", "#111111"]
        elif is_welfare:
            badge_font = self._load_font(font_path, 34)
            sub_font = self._load_font(font_path, 32)
            footer_font = self._load_font(font_path, 26)
            headline_panel = (board_left + 36, board_top + 40, board_right - 36, board_bottom - 166)
            headline_font, headline_lines, headline_total_height = self._fit_wrapped_text_block(
                draw,
                self._clean_display_text(
                    content.thumbnail_text or content.video_title or content.topic.representative_title
                ),
                font_path=font_path,
                start_size=102,
                min_size=68,
                max_width=(headline_panel[2] - headline_panel[0]) - 40,
                max_lines=3,
                max_height=(headline_panel[3] - headline_panel[1]) - 24,
                line_gap=20,
            )
            badge_fill = (22, 78, 174, 214)
            badge_x = 126
            badge_y = int(height * 0.44)
            headline_colors = ["#1d4ed8", "#ea580c", "#1d4ed8"]
            stroke_colors = ["#ffffff", "#ffffff", "#ffffff"]
        else:
            badge_font = self._load_font(font_path, 42)
            sub_font = self._load_font(font_path, 42)
            footer_font = self._load_font(font_path, 36)
            headline_panel = (60, 210, width - 60, int(height * 0.62))
            headline_font, headline_lines, headline_total_height = self._fit_wrapped_text_block(
                draw,
                self._clean_display_text(
                    content.thumbnail_text or content.video_title or content.topic.representative_title
                ),
                font_path=font_path,
                start_size=116,
                min_size=84,
                max_width=(headline_panel[2] - headline_panel[0]),
                max_lines=3,
                max_height=(headline_panel[3] - headline_panel[1]),
                line_gap=26,
            )
            badge_fill = (12, 18, 31, 220)
            badge_x = 56
            badge_y = 48
            headline_colors = ["#ffd44d", "#ffffff", "#ffffff"]
            stroke_colors = ["#fff7ed", "#111111", "#111111"]

        badge_text = self._short_badge_text(content)
        badge_box = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_width = (badge_box[2] - badge_box[0]) + 36
        draw.rounded_rectangle((badge_x, badge_y, badge_x + badge_width, badge_y + 58), radius=24, fill=badge_fill)
        draw.text((badge_x + 18, badge_y + 13), badge_text, fill="#fff8dc" if is_news else "#ffffff", font=badge_font)

        line_gap = 24 if is_news else (20 if is_welfare else 26)
        line_heights = [draw.textbbox((0, 0), line, font=headline_font)[3] for line in headline_lines]
        current_y = headline_panel[1] + max(0, ((headline_panel[3] - headline_panel[1]) - headline_total_height) // 2)
        for index, line in enumerate(headline_lines):
            line_width = draw.textbbox((0, 0), line, font=headline_font)[2]
            line_x = (width - line_width) // 2
            self._draw_outlined_text(
                draw,
                (line_x, current_y),
                line,
                font=headline_font,
                fill=headline_colors[min(index, len(headline_colors) - 1)],
                stroke_fill=stroke_colors[min(index, len(stroke_colors) - 1)],
                stroke_width=8 if is_welfare else 10,
            )
            current_y += line_heights[index] + line_gap

        highlight = self._short_highlight_text(content)
        if highlight:
            max_width = int(width * 0.72) if is_news else (int(width * 0.58) if is_welfare else width - 120)
            highlight_lines = wrap_text_by_width(
                highlight,
                measure=lambda value: draw.textbbox((0, 0), value, font=sub_font)[2],
                max_width=max_width,
                max_lines=2 if is_welfare else 3,
            )
            box_height = 32 + len(highlight_lines) * 48
            if is_news:
                top = int(height * 0.61)
                left = (width - int(width * 0.74)) // 2
                right = width - left
                fill = (6, 12, 24, 194)
                text_x = left + 26
            elif is_welfare:
                top = int(height * 0.64)
                left = int(width * 0.16)
                right = int(width * 0.84)
                fill = (17, 24, 39, 228)
                text_x = left + 24
            else:
                top = height - 280
                left = 54
                right = width - 54
                fill = (8, 14, 24, 190)
                text_x = left + 20
            draw.rounded_rectangle((left, top, right, top + box_height), radius=26, fill=fill)
            highlight_y = top + 16
            for line in highlight_lines:
                draw.text((text_x, highlight_y), line, fill="#f8fafc", font=sub_font)
                highlight_y += 46

        footer = self._short_footer_text(content)
        if footer:
            if is_news:
                footer_box_width = max(352, min(width - 140, draw.textbbox((0, 0), footer, font=footer_font)[2] + 64))
                footer_x = (width - footer_box_width) // 2
                draw.rounded_rectangle((footer_x, height - 122, footer_x + footer_box_width, height - 60), radius=24, fill=(255, 255, 255, 236))
                draw.text((footer_x + 24, height - 100), footer, fill="#111827", font=footer_font)
            elif is_welfare:
                footer_box_width = max(300, min(width - 180, draw.textbbox((0, 0), footer, font=footer_font)[2] + 56))
                footer_x = (width - footer_box_width) // 2
                footer_y = int(height * 0.77)
                draw.rounded_rectangle((footer_x, footer_y, footer_x + footer_box_width, footer_y + 60), radius=24, fill=(255, 255, 255, 236))
                draw.text((footer_x + 24, footer_y + 19), footer, fill="#0f172a", font=footer_font)
            else:
                draw.rounded_rectangle((54, height - 126, width - 54, height - 52), radius=24, fill=(255, 255, 255, 228))
                draw.text((78, height - 106), footer, fill="#111827", font=footer_font)
        return rgba.convert("RGB")

    def _draw_story_thumbnail(
        self,
        image: Image.Image,
        content: GeneratedContent,
        font_path: Path | None,
    ) -> Image.Image:
        width, height = image.size
        base = image.convert("RGBA")
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 34))
        draw.rectangle((0, 0, int(width * 0.56), height), fill=(8, 8, 10, 158))
        draw.rectangle((0, int(height * 0.67), width, height), fill=(0, 0, 0, 110))
        rgba = Image.alpha_composite(base, overlay)
        draw = ImageDraw.Draw(rgba)

        badge_font = self._load_font(font_path, 34)
        headline_font = self._load_font(font_path, 104)
        sub_font = self._load_font(font_path, 36)
        footer_font = self._load_font(font_path, 46)

        badge_text = "인생사연"
        badge_box = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_w = badge_box[2] - badge_box[0] + 34
        draw.rounded_rectangle((58, 48, 58 + badge_w, 104), radius=24, fill=(18, 18, 20, 222))
        draw.text((76, 60), badge_text, fill="#f8fafc", font=badge_font)

        headline_primary, headline_secondary = self._story_headline_parts(
            content.thumbnail_text or content.video_title or content.topic.representative_title
        )
        current_y = 138
        if headline_primary:
            self._draw_outlined_text(
                draw,
                (64, current_y),
                headline_primary,
                font=headline_font,
                fill="#ffd34d",
                stroke_fill="#ffffff",
                stroke_width=10,
            )
            current_y += 118
        if headline_secondary:
            self._draw_outlined_text(
                draw,
                (64, current_y),
                headline_secondary,
                font=headline_font,
                fill="#ffffff",
                stroke_fill="#111111",
                stroke_width=12,
            )

        subhead = self._story_subhead(content)
        if subhead:
            sub_lines = wrap_text_by_width(
                subhead,
                measure=lambda value: draw.textbbox((0, 0), value, font=sub_font)[2],
                max_width=int(width * 0.46),
                max_lines=2,
            )
            sub_y = int(height * 0.74)
            for line in sub_lines:
                self._draw_outlined_text(
                    draw,
                    (76, sub_y),
                    line,
                    font=sub_font,
                    fill="#fef3c7",
                    stroke_fill="#111111",
                    stroke_width=6,
                )
                sub_y += 46

        footer = self._story_footer(content)
        if footer:
            footer_lines = wrap_text_by_width(
                footer,
                measure=lambda value: draw.textbbox((0, 0), value, font=footer_font)[2],
                max_width=int(width * 0.78),
                max_lines=2,
            )
            footer_y = height - 120 - (max(0, len(footer_lines) - 1) * 52)
            for line in footer_lines:
                self._draw_outlined_text(
                    draw,
                    (72, footer_y),
                    line,
                    font=footer_font,
                    fill="#f8fafc",
                    stroke_fill="#111111",
                    stroke_width=7,
                )
                footer_y += 52

        return rgba.convert("RGB")

    @staticmethod
    def _draw_text_shadow(
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        *,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: str,
        shadow: str,
    ) -> None:
        x, y = position
        draw.text((x + 4, y + 4), text, fill=shadow, font=font)
        draw.text((x, y), text, fill=fill, font=font)

    @staticmethod
    def _draw_outlined_text(
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        *,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: str,
        stroke_fill: str,
        stroke_width: int,
    ) -> None:
        draw.text(
            position,
            text,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )

    def _fit_wrapped_text_block(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        font_path: Path | None,
        start_size: int,
        min_size: int,
        max_width: int,
        max_lines: int,
        max_height: int,
        line_gap: int,
    ) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str], int]:
        cleaned = self._clean_display_text(text)
        candidate_lines: list[str] = [cleaned] if cleaned else [""]
        candidate_font: ImageFont.FreeTypeFont | ImageFont.ImageFont = self._load_font(font_path, start_size)
        candidate_total = 0

        for size in range(start_size, min_size - 1, -4):
            font = self._load_font(font_path, size)
            lines = wrap_text_by_width(
                cleaned,
                measure=lambda value: draw.textbbox((0, 0), value, font=font)[2],
                max_width=max_width,
                max_lines=max_lines,
            )
            heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
            total_height = sum(heights) + (max(0, len(lines) - 1) * line_gap)
            candidate_font = font
            candidate_lines = lines
            candidate_total = total_height
            if total_height <= max_height:
                return font, lines, total_height

        return candidate_font, candidate_lines, candidate_total

    @staticmethod
    def _story_headline_parts(text: str) -> tuple[str, str]:
        cleaned = ThumbnailBuilder._clean_display_text(text)
        if not cleaned:
            return "", ""
        tokens = cleaned.split()
        if len(tokens) >= 4:
            split_index = max(2, len(tokens) // 2)
            first = " ".join(tokens[:split_index]).strip()
            second = " ".join(tokens[split_index:]).strip()
            return first[:14], second[:14]
        if len(cleaned) > 8:
            split_index = max(4, min(len(cleaned) - 4, len(cleaned) // 2))
            return cleaned[:split_index].strip(), cleaned[split_index:].strip()
        return cleaned[:18], ""

    @staticmethod
    def _story_subhead(content: GeneratedContent) -> str:
        candidates = [
            *(scene.summary for scene in content.scenes[:2] if scene.summary),
            *(scene.title for scene in content.scenes[:2] if scene.title),
            content.video_title,
            content.topic.representative_title,
        ]
        for source in candidates:
            cleaned = ThumbnailBuilder._clean_display_text(source or "")
            cleaned = cleaned.replace(content.thumbnail_text or "", "").strip(" -:|")
            cleaned = re.sub(r"\s+", " ", cleaned)
            if len(cleaned) >= 8:
                return cleaned[:24].strip()
        return ""

    @staticmethod
    def _story_footer(content: GeneratedContent) -> str:
        candidates = []
        if len(content.scenes) > 1:
            second_scene = content.scenes[1]
            candidates.extend([second_scene.summary, second_scene.title])
        if content.scenes:
            first_scene = content.scenes[0]
            candidates.extend([first_scene.summary, first_scene.title])
        candidates.extend([content.video_title, content.topic.representative_title])
        for source in candidates:
            footer = ThumbnailBuilder._clean_display_text(source or "")
            footer = footer.replace(content.thumbnail_text or "", "").strip(" -:|")
            footer = re.sub(r"\s+", " ", footer)
            if len(footer) >= 8:
                return footer[:20].strip()
        return ""

    @staticmethod
    def _short_badge_text(content: GeneratedContent) -> str:
        preset_key = str(getattr(content, "preset_key", "") or "")
        if preset_key == "welfare_news":
            return "복지 브리핑"
        if preset_key == "economy_news":
            return "긴급 브리핑"
        if preset_key == "quotes_daily":
            return "오늘의 문장"
        return "오늘 이슈"

    @staticmethod
    def _short_highlight_text(content: GeneratedContent) -> str:
        for item in content.detail_points[:2]:
            cleaned = ThumbnailBuilder._clean_display_text(item)
            if cleaned:
                return cleaned[:44]
        source = ThumbnailBuilder._clean_display_text(content.video_title or content.topic.representative_title)
        return source[:44]

    @staticmethod
    def _short_footer_text(content: GeneratedContent) -> str:
        preset_key = str(getattr(content, "preset_key", "") or "")
        if preset_key == "welfare_news":
            return "지금 대상과 신청 시점 확인"
        if preset_key == "economy_news":
            return "핵심 흐름만 짧게 정리"
        return "오늘 이슈만 빠르게 확인"

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

    @staticmethod
    def _paint_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        stripe_colors = ["#08111f", "#10233e", "#163b6c", "#1f4f8b"]
        stripe_width = width // len(stripe_colors)
        for index, color in enumerate(stripe_colors):
            x0 = index * stripe_width
            x1 = width if index == len(stripe_colors) - 1 else (index + 1) * stripe_width
            draw.rectangle((x0, 0, x1, height), fill=color)

        draw.ellipse((width - 320, -40, width + 80, 360), fill="#fb7185")
        draw.ellipse((width - 480, 260, width - 120, 620), fill="#38bdf8")
        draw.rounded_rectangle((width - 260, 420, width - 80, 600), radius=36, fill="#22c55e")

    @staticmethod
    def _load_font(font_path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if font_path is None:
            return ImageFont.load_default()
        return ImageFont.truetype(str(font_path), size)
