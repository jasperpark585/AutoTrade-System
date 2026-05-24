from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import AppConfig
from app.generation.ai_service import OpenAIContentService
from app.models import ArtifactStatus, GeneratedContent
from app.studio.presets import preset_by_key
from app.utils.text import resolve_existing_font, wrap_text_by_width


IMAGE_VARIATIONS = (
    "wide emotional framing, dramatic subject separation",
    "closer portrait framing, subtle tension in the atmosphere",
    "alternate angle, richer depth, cinematic realism",
    "quiet environmental shot with symbolic details",
)


class BackgroundBuilder:
    """Resolve a background image or a full story scene image set."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.ai = OpenAIContentService(config)

    def build(self, content: GeneratedContent, run_id: str) -> ArtifactStatus:
        channel = self.config.active_channel
        if channel and channel.manual_background_path:
            manual_path = Path(channel.manual_background_path).expanduser()
            if not manual_path.is_absolute():
                manual_path = (self.config.project_root / manual_path).resolve()
            if manual_path.exists():
                return ArtifactStatus(
                    status="created",
                    provider="manual-background",
                    path=str(manual_path),
                    message="Using manually provided background image.",
                )

        reusable_short_background = self._reusable_short_background(content)
        if reusable_short_background is not None:
            return ArtifactStatus(
                status="created",
                provider="reusable-background",
                path=str(reusable_short_background),
                message="Reusable short background reused successfully.",
            )

        if content.content_format == "longform_story" and content.scenes:
            return self._build_story_scene_images(content, run_id)

        output_path = self.config.output_backgrounds_dir / f"{run_id}.png"
        prompt = self._resolve_short_base_prompt(content)
        if self.ai.generate_image(prompt=prompt, output_path=output_path):
            return ArtifactStatus(
                status="created",
                provider="openai-image",
                path=str(output_path),
                message="AI background image created successfully.",
            )

        self._create_placeholder_image(
            output_path,
            title=content.topic.representative_title,
            subtitle=content.detail_points[0] if content.detail_points else self.config.generation.channel_name,
        )
        return ArtifactStatus(
            status="created",
            provider="placeholder-background",
            path=str(output_path),
            message="Fallback background image created locally.",
        )

    def _reusable_short_background(self, content: GeneratedContent) -> Path | None:
        channel = self.config.active_channel
        if channel is None:
            return None

        preset = preset_by_key(channel.preset_key)
        if preset.key not in {"economy_news", "welfare_news"}:
            return None

        output_path = self._reusable_short_background_path(preset.key)
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = self._create_reusable_short_background(preset.key)
        image.save(output_path)
        return output_path

    def _reusable_short_background_path(self, preset_key: str) -> Path:
        if preset_key == "economy_news":
            name = "news_v3"
        else:
            name = "welfare_v3"
        return self.config.backgrounds_dir / f"reusable_{name}_short_background.png"

    def _create_reusable_short_background(self, preset_key: str) -> Image.Image:
        width = self.config.render.width
        height = self.config.render.height
        if preset_key == "welfare_news":
            return self._create_welfare_short_background(width, height)
        return self._create_news_short_background(width, height)

    @staticmethod
    def _apply_vertical_gradient(image: Image.Image, top_color: tuple[int, int, int], bottom_color: tuple[int, int, int]) -> Image.Image:
        width, height = image.size
        draw = ImageDraw.Draw(image)
        for y in range(height):
            mix = y / max(1, height - 1)
            color = tuple(
                int(top_color[index] + (bottom_color[index] - top_color[index]) * mix)
                for index in range(3)
            )
            draw.line((0, y, width, y), fill=color)
        return image

    def _create_news_short_background(self, width: int, height: int) -> Image.Image:
        image = Image.new("RGBA", (width, height), "#07111f")
        image = self._apply_vertical_gradient(image, (4, 11, 22), (8, 20, 39))

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        draw.rounded_rectangle(
            (46, 48, width - 46, height - 48),
            radius=44,
            outline=(255, 255, 255, 26),
            width=3,
        )
        draw.rectangle((0, 0, int(width * 0.58), int(height * 0.44)), fill=(2, 7, 16, 118))
        draw.rectangle((0, int(height * 0.77), width, height), fill=(3, 8, 16, 176))

        # Premium headline stage on the left. This intentionally stays clean so text never fights the background.
        draw.rounded_rectangle(
            (62, 152, int(width * 0.58), int(height * 0.63)),
            radius=36,
            fill=(8, 14, 27, 122),
            outline=(255, 255, 255, 12),
            width=2,
        )

        # Lower card zone where the highlight box can sit without visual clutter.
        draw.rounded_rectangle(
            (64, int(height * 0.69), width - 64, height - 132),
            radius=34,
            fill=(6, 11, 22, 112),
            outline=(255, 255, 255, 12),
            width=2,
        )

        # Minimal ticker lines.
        ticker_top = int(height * 0.70)
        for offset in (0, 38, 76):
            draw.line(
                (92, ticker_top + offset, width - 92, ticker_top + offset),
                fill=(255, 255, 255, 12),
                width=2,
            )

        # Refined right-side globe / broadcast motif instead of cluttered boxes.
        globe = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        globe_draw = ImageDraw.Draw(globe)
        globe_bounds = (width - 468, 146, width - 64, 552)
        globe_draw.ellipse(globe_bounds, outline=(96, 165, 250, 66), width=4)
        globe_draw.ellipse((globe_bounds[0] + 28, globe_bounds[1] + 28, globe_bounds[2] - 28, globe_bounds[3] - 28), outline=(255, 255, 255, 22), width=2)
        globe_draw.arc(globe_bounds, start=18, end=162, fill=(248, 113, 113, 180), width=7)
        globe_draw.arc(globe_bounds, start=196, end=342, fill=(56, 189, 248, 180), width=7)
        for ratio in (0.28, 0.5, 0.72):
            y = globe_bounds[1] + int((globe_bounds[3] - globe_bounds[1]) * ratio)
            globe_draw.arc((globe_bounds[0] + 24, y - 18, globe_bounds[2] - 24, y + 18), start=0, end=180, fill=(255, 255, 255, 28), width=2)
            globe_draw.arc((globe_bounds[0] + 24, y - 18, globe_bounds[2] - 24, y + 18), start=180, end=360, fill=(255, 255, 255, 18), width=2)
        mid_x = (globe_bounds[0] + globe_bounds[2]) // 2
        globe_draw.line((mid_x, globe_bounds[1] + 22, mid_x, globe_bounds[3] - 22), fill=(255, 255, 255, 22), width=2)
        globe_draw.line((globe_bounds[0] + 22, (globe_bounds[1] + globe_bounds[3]) // 2, globe_bounds[2] - 22, (globe_bounds[1] + globe_bounds[3]) // 2), fill=(255, 255, 255, 22), width=2)
        globe = globe.filter(ImageFilter.GaussianBlur(radius=0.2))

        # Dramatic edge glows.
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((width - 520, 80, width + 40, 560), fill=(239, 68, 68, 116))
        glow_draw.ellipse((width - 460, 160, width - 20, 620), fill=(59, 130, 246, 92))
        glow_draw.ellipse((120, height - 520, 560, height - 90), fill=(14, 165, 233, 34))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=84))

        # Clean financial line at the bottom.
        chart = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        chart_draw = ImageDraw.Draw(chart)
        chart_points = [
            (92, height - 164),
            (198, height - 188),
            (302, height - 154),
            (414, height - 214),
            (538, height - 182),
            (664, height - 252),
            (802, height - 220),
            (934, height - 286),
        ]
        chart_draw.line(chart_points, fill=(251, 146, 60, 230), width=10, joint="curve")
        chart_draw.line([(x, y + 12) for x, y in chart_points], fill=(255, 255, 255, 44), width=3)
        for x, y in chart_points[1::2]:
            chart_draw.ellipse((x - 11, y - 11, x + 11, y + 11), fill=(255, 255, 255, 220))
            chart_draw.ellipse((x - 25, y - 25, x + 25, y + 25), outline=(251, 146, 60, 70), width=3)

        image = Image.alpha_composite(image, glow)
        image = Image.alpha_composite(image, overlay)
        image = Image.alpha_composite(image, globe)
        image = Image.alpha_composite(image, chart)
        return image.convert("RGB")

    def _add_news_glows(self, canvas: Image.Image, width: int, height: int) -> None:
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glow)
        # Directional glows keep the left side dark and focus attention on the hero area.
        draw.ellipse((width - 470, 60, width - 20, 470), fill=(239, 68, 68, 120))
        draw.ellipse((width - 620, int(height * 0.18), width - 180, int(height * 0.64)), fill=(59, 130, 246, 74))
        draw.ellipse((30, height - 520, 440, height - 120), fill=(14, 165, 233, 72))
        draw.ellipse((int(width * 0.34), int(height * 0.50), int(width * 0.70), int(height * 0.92)), fill=(250, 204, 21, 48))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=92))
        image = Image.alpha_composite(canvas, glow)
        canvas.paste(image)

    def _paint_news_grid(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        # A subtle grid gives the frame a newsroom / data-room feel without becoming noisy.
        left = 94
        right = width - 94
        top = 182
        bottom = height - 210
        step_y = 110
        for y in range(top, bottom, step_y):
            draw.line((left, y, right, y), fill=(255, 255, 255, 11), width=1)
        step_x = 170
        for x in range(left + 40, right, step_x):
            draw.line((x, top, x, bottom), fill=(255, 255, 255, 8), width=1)

        # The right side gets a stronger vertical accent so the thumbnail headline can sit on the left.
        draw.rounded_rectangle((width - 410, 146, width - 108, 556), radius=36, fill=(7, 13, 26, 178), outline=(255, 255, 255, 18), width=2)
        draw.rounded_rectangle((width - 386, 174, width - 138, 244), radius=18, fill=(235, 87, 87, 220))
        draw.rounded_rectangle((width - 386, 264, width - 138, 330), radius=18, fill=(250, 204, 21, 214))
        draw.rounded_rectangle((width - 386, 350, width - 138, 416), radius=18, fill=(14, 165, 233, 210))
        draw.rounded_rectangle((width - 386, 436, width - 180, 498), radius=18, fill=(255, 255, 255, 18), outline=(255, 255, 255, 24), width=1)

    def _paint_news_cards(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        # Abstract "news cards" simulate stacked story tiles and keep the composition rich.
        card_top = 244
        card_width = 300
        card_height = 160
        card_x = 110
        cards = [
            (card_x, card_top, card_x + card_width, card_top + card_height, (9, 17, 32, 212), (255, 255, 255, 36)),
            (card_x + 38, card_top + 192, card_x + card_width + 38, card_top + 192 + card_height, (14, 24, 44, 205), (96, 165, 250, 34)),
            (card_x + 76, card_top + 384, card_x + card_width + 76, card_top + 384 + card_height, (13, 22, 41, 198), (245, 158, 11, 32)),
        ]
        for left, top, right, bottom, fill, outline in cards:
            draw.rounded_rectangle((left, top, right, bottom), radius=30, fill=fill, outline=outline, width=2)
            draw.rounded_rectangle((left + 22, top + 18, left + 98, top + 46), radius=12, fill=(239, 68, 68, 188))
            draw.line((left + 22, top + 82, right - 22, top + 82), fill=(255, 255, 255, 20), width=2)
            draw.line((left + 22, top + 110, right - 50, top + 110), fill=(255, 255, 255, 14), width=2)
            draw.line((left + 22, top + 136, right - 76, top + 136), fill=(255, 255, 255, 10), width=2)

        # Small dots and nodes create the feeling of live updates.
        for x, y, color in (
            (width - 290, 248, (255, 255, 255, 160)),
            (width - 216, 300, (239, 68, 68, 180)),
            (width - 316, 386, (14, 165, 233, 170)),
            (width - 206, 468, (250, 204, 21, 160)),
        ):
            draw.ellipse((x, y, x + 16, y + 16), fill=color)

    def _paint_news_chart(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        chart_left = 96
        chart_right = width - 120
        chart_top = int(height * 0.48)
        chart_bottom = height - 164
        draw.rounded_rectangle((chart_left, chart_top, chart_right, chart_bottom), radius=32, fill=(4, 10, 21, 150), outline=(255, 255, 255, 18), width=2)

        for y in range(chart_top + 26, chart_bottom - 20, 64):
            draw.line((chart_left + 18, y, chart_right - 18, y), fill=(255, 255, 255, 10), width=1)

        # Candlestick bars.
        bars = [
            (140, 60, 168),
            (210, 44, 188),
            (280, 82, 210),
            (350, 36, 174),
            (420, 16, 156),
            (490, 90, 230),
            (560, 32, 182),
            (630, 14, 158),
            (700, 64, 202),
            (770, 24, 166),
            (840, 12, 154),
        ]
        base_y = chart_bottom - 30
        for x, offset, candle_height in bars:
            top = base_y - candle_height - offset
            draw.line((x, top - 14, x, base_y), fill=(96, 165, 250, 90), width=4)
            color = (249, 115, 22, 210) if offset > 50 else (59, 130, 246, 210)
            draw.rounded_rectangle((x - 12, top, x + 12, top + candle_height), radius=6, fill=color)

        # Hero trend line and glowing trough.
        trend_points = [
            (132, chart_bottom - 96),
            (220, chart_bottom - 126),
            (320, chart_bottom - 92),
            (430, chart_bottom - 166),
            (548, chart_bottom - 148),
            (680, chart_bottom - 228),
            (792, chart_bottom - 196),
            (900, chart_bottom - 272),
        ]
        draw.line(trend_points, fill=(255, 138, 61, 245), width=10, joint="curve")
        draw.line([(x, y + 10) for x, y in trend_points], fill=(255, 255, 255, 55), width=3)
        for x, y in trend_points:
            draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=(255, 255, 255, 230))
            draw.ellipse((x - 22, y - 22, x + 22, y + 22), outline=(255, 138, 61, 60), width=3)

        # A soft spotlight behind the chart peak.
        spot = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        spot_draw = ImageDraw.Draw(spot)
        spot_draw.ellipse((560, chart_top - 56, width - 92, chart_bottom + 20), fill=(255, 157, 0, 56))
        spot = spot.filter(ImageFilter.GaussianBlur(radius=60))
        canvas.paste(Image.alpha_composite(canvas, spot))

    def _paint_news_cityline(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        # Minimal skyline silhouette keeps the bottom anchored like a news studio backdrop.
        base_y = height - 82
        skyline = [
            (0, base_y),
            (96, base_y),
            (96, base_y - 70),
            (154, base_y - 70),
            (154, base_y - 120),
            (220, base_y - 120),
            (220, base_y - 44),
            (286, base_y - 44),
            (286, base_y - 140),
            (346, base_y - 140),
            (346, base_y - 72),
            (420, base_y - 72),
            (420, base_y - 164),
            (500, base_y - 164),
            (500, base_y - 94),
            (576, base_y - 94),
            (576, base_y - 188),
            (656, base_y - 188),
            (656, base_y - 120),
            (720, base_y - 120),
            (720, base_y - 42),
            (804, base_y - 42),
            (804, base_y - 156),
            (892, base_y - 156),
            (892, base_y - 64),
            (980, base_y - 64),
            (width, base_y - 64),
            (width, height),
            (0, height),
        ]
        draw.polygon(skyline, fill=(3, 7, 15, 210))
        draw.line((0, base_y, width, base_y), fill=(96, 165, 250, 70), width=2)
        draw.line((0, base_y - 2, width, base_y - 2), fill=(249, 115, 22, 40), width=1)

    def _paint_news_glass_switches(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        # Small decorative glass switches add the "premium dashboard" feel.
        pill_specs = (
            (88, 118, 208, 42, "MARKET"),
            (320, 118, 186, 42, "ALERT"),
        )
        for left, top, box_width, box_height, _label in pill_specs:
            draw.rounded_rectangle(
                (left, top, left + box_width, top + box_height),
                radius=20,
                fill=(17, 24, 39, 170),
                outline=(255, 255, 255, 24),
                width=1,
            )
        # Purely decorative indicator dots, no readable text needed here.
        draw.ellipse((120, 130, 146, 156), fill=(249, 115, 22, 230))
        draw.ellipse((352, 130, 378, 156), fill=(34, 197, 94, 220))

    def _create_welfare_short_background(self, width: int, height: int) -> Image.Image:
        image = Image.new("RGBA", (width, height), "#f6f4ed")
        image = self._apply_vertical_gradient(image, (246, 243, 235), (229, 238, 248))

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        draw.rounded_rectangle(
            (48, 52, width - 48, height - 52),
            radius=42,
            outline=(40, 61, 91, 28),
            width=3,
        )
        draw.rectangle((0, 0, width, int(height * 0.20)), fill=(255, 255, 255, 78))
        draw.rectangle((0, int(height * 0.76), width, height), fill=(15, 23, 42, 116))

        # Central information card so the screen no longer feels empty.
        card_bounds = (92, 220, width - 92, height - 280)
        draw.rounded_rectangle(card_bounds, radius=38, fill=(255, 255, 255, 214), outline=(89, 117, 154, 28), width=2)
        draw.rounded_rectangle(
            (card_bounds[0] + 24, card_bounds[1] + 24, card_bounds[2] - 24, card_bounds[3] - 24),
            radius=30,
            outline=(89, 117, 154, 18),
            width=1,
        )
        # Fill the middle card with information blocks so the center never looks empty.
        draw.rounded_rectangle(
            (card_bounds[0] + 34, card_bounds[1] + 34, card_bounds[2] - 34, card_bounds[1] + 132),
            radius=26,
            fill=(234, 244, 255, 228),
            outline=(104, 133, 173, 18),
            width=2,
        )
        draw.rounded_rectangle(
            (card_bounds[0] + 34, card_bounds[1] + 154, card_bounds[0] + 224, card_bounds[3] - 34),
            radius=24,
            fill=(255, 247, 231, 210),
            outline=(221, 182, 106, 16),
            width=2,
        )
        draw.rounded_rectangle(
            (card_bounds[0] + 246, card_bounds[1] + 154, card_bounds[2] - 34, card_bounds[3] - 34),
            radius=24,
            fill=(240, 247, 255, 210),
            outline=(111, 142, 180, 16),
            width=2,
        )

        # Suggest structured welfare info instead of a large empty white panel.
        for y in (card_bounds[1] + 60, card_bounds[1] + 92):
            draw.rounded_rectangle(
                (card_bounds[0] + 72, y, card_bounds[2] - 110, y + 14),
                radius=7,
                fill=(132, 159, 193, 92),
            )
        for y in (card_bounds[1] + 196, card_bounds[1] + 248, card_bounds[1] + 300):
            draw.rounded_rectangle(
                (card_bounds[0] + 282, y, card_bounds[2] - 76, y + 18),
                radius=9,
                fill=(146, 169, 199, 88),
            )
        draw.rounded_rectangle(
            (card_bounds[0] + 282, card_bounds[3] - 112, card_bounds[2] - 110, card_bounds[3] - 78),
            radius=16,
            fill=(34, 197, 94, 148),
        )

        # Warm utility chips on the upper-right.
        chip_specs = (
            (width - 328, 158, width - 118, 216, (239, 246, 255, 235)),
            (width - 328, 232, width - 118, 290, (255, 247, 237, 235)),
        )
        for left, top, right, bottom, fill in chip_specs:
            draw.rounded_rectangle((left, top, right, bottom), radius=24, fill=fill, outline=(76, 103, 140, 20), width=2)

        # Document list zone at the bottom.
        draw.rounded_rectangle((94, height - 418, width - 94, height - 148), radius=34, fill=(247, 250, 255, 160), outline=(93, 119, 155, 18), width=2)
        for y in (height - 364, height - 308, height - 252):
            draw.rounded_rectangle((134, y, width - 148, y + 22), radius=10, fill=(183, 202, 226, 96))

        # Welfare icons in the middle card.
        icon_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        icon_draw = ImageDraw.Draw(icon_layer)
        center_x = width // 2
        center_y = (card_bounds[1] + card_bounds[3]) // 2 - 20

        # Main shield/check icon.
        shield = [
            (center_x, center_y - 120),
            (center_x + 108, center_y - 56),
            (center_x + 84, center_y + 90),
            (center_x, center_y + 146),
            (center_x - 84, center_y + 90),
            (center_x - 108, center_y - 56),
        ]
        icon_draw.polygon(shield, fill=(224, 241, 255, 255), outline=(74, 108, 152, 48))
        icon_draw.line(
            (
                center_x - 54, center_y + 8,
                center_x - 8, center_y + 56,
                center_x + 66, center_y - 30,
            ),
            fill=(34, 197, 94, 230),
            width=18,
            joint="curve",
        )

        # Side welfare props.
        # Left: coin / won icon.
        icon_draw.ellipse((card_bounds[0] + 62, center_y - 64, card_bounds[0] + 166, center_y + 40), fill=(255, 236, 185, 238), outline=(214, 158, 46, 34), width=3)
        coin_font = self._safe_icon_font(72)
        icon_draw.text((card_bounds[0] + 90, center_y - 30), "₩", fill=(180, 127, 25, 220), font=coin_font)

        # Right: document icon.
        doc_left = card_bounds[2] - 186
        doc_top = center_y - 92
        icon_draw.rounded_rectangle((doc_left, doc_top, doc_left + 122, doc_top + 162), radius=22, fill=(236, 246, 255, 236), outline=(83, 114, 156, 30), width=3)
        icon_draw.polygon(
            [(doc_left + 82, doc_top), (doc_left + 122, doc_top), (doc_left + 122, doc_top + 40)],
            fill=(209, 226, 247, 250),
        )
        for offset in (38, 70, 102):
            icon_draw.rounded_rectangle((doc_left + 22, doc_top + offset, doc_left + 98, doc_top + offset + 10), radius=5, fill=(110, 143, 183, 104))

        # Mid-right list bullets.
        for idx, y in enumerate((center_y - 18, center_y + 24, center_y + 66)):
            icon_draw.ellipse((card_bounds[0] + 266, y, card_bounds[0] + 282, y + 16), fill=(74, 144, 226, 166 if idx == 0 else 120))

        # Small top-right utility icon pills.
        self._draw_welfare_icon_pill(draw, (width - 302, 170), "calendar")
        self._draw_welfare_icon_pill(draw, (width - 302, 244), "check")

        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((70, 90, 420, 420), fill=(96, 165, 250, 44))
        glow_draw.ellipse((width - 420, 130, width - 34, 476), fill=(245, 158, 11, 38))
        glow_draw.ellipse((190, height - 500, 560, height - 120), fill=(59, 130, 246, 30))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=74))

        image = Image.alpha_composite(image, glow)
        image = Image.alpha_composite(image, overlay)
        image = Image.alpha_composite(image, icon_layer)
        return image.convert("RGB")

    @staticmethod
    def _safe_icon_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for candidate in (
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/malgun.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            path = Path(candidate)
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size=size)
                except OSError:
                    continue
        return ImageFont.load_default()

    def _draw_welfare_icon_pill(self, draw: ImageDraw.ImageDraw, position: tuple[int, int], icon_kind: str) -> None:
        left, top = position
        right = left + 158
        bottom = top + 34
        draw.rounded_rectangle((left, top, right, bottom), radius=17, fill=(255, 255, 255, 0))
        icon_left = left + 18
        icon_top = top + 5

        if icon_kind == "calendar":
            draw.rounded_rectangle((icon_left, icon_top, icon_left + 24, icon_top + 24), radius=6, fill=(59, 130, 246, 210))
            draw.rectangle((icon_left + 4, icon_top + 6, icon_left + 20, icon_top + 10), fill=(255, 255, 255, 190))
            draw.line((icon_left + 6, icon_top + 16, icon_left + 18, icon_top + 16), fill=(255, 255, 255, 180), width=2)
        elif icon_kind == "check":
            draw.ellipse((icon_left, icon_top, icon_left + 24, icon_top + 24), fill=(34, 197, 94, 220))
            draw.line((icon_left + 5, icon_top + 13, icon_left + 10, icon_top + 18, icon_left + 19, icon_top + 8), fill=(255, 255, 255, 220), width=3, joint="curve")

    @staticmethod
    def _resolve_short_base_prompt(content: GeneratedContent) -> str:
        if content.content_format != "short":
            return content.background_prompt

        thumbnail_prompt = (content.thumbnail_prompt or "").strip()
        background_prompt = (content.background_prompt or "").strip()
        if thumbnail_prompt and background_prompt and thumbnail_prompt != background_prompt:
            return f"{thumbnail_prompt}. Supporting scene context: {background_prompt}"
        return thumbnail_prompt or background_prompt

    def _build_story_scene_images(self, content: GeneratedContent, run_id: str) -> ArtifactStatus:
        output_dir = self.config.output_backgrounds_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        images_per_scene = max(1, int(getattr(self.config.active_channel, "story_images_per_scene", 3) or 3))
        hook_variants = 1 if images_per_scene == 1 else max(2, min(4, images_per_scene))

        hook_images: list[str] = []
        for variant_index in range(hook_variants):
            hook_path = output_dir / f"hook_{variant_index + 1:02}.png"
            self._generate_or_placeholder(
                output_path=hook_path,
                prompt=self._variant_prompt(content.hook_image_prompt or content.background_prompt, variant_index),
                title=content.hook_title or "3분 몰입 훅",
                subtitle=content.topic.representative_title,
            )
            hook_images.append(str(hook_path))

        scene_items: list[dict[str, object]] = []
        for scene in content.scenes:
            variant_paths: list[str] = []
            for variant_index in range(images_per_scene):
                scene_path = output_dir / f"scene_{scene.index:02}_{variant_index + 1:02}.png"
                self._generate_or_placeholder(
                    output_path=scene_path,
                    prompt=self._variant_prompt(scene.image_prompt, variant_index),
                    title=scene.title,
                    subtitle=scene.summary,
                )
                variant_paths.append(str(scene_path))
            scene_items.append(
                {
                    "index": scene.index,
                    "title": scene.title,
                    "paths": variant_paths,
                    "duration_seconds": scene.duration_seconds,
                }
            )

        return ArtifactStatus(
            status="created",
            provider="story-scene-images",
            path=str(output_dir),
            message="Story scene images created successfully.",
            extra={
                "hook_image": hook_images[0] if hook_images else "",
                "hook_images": hook_images,
                "scene_images": scene_items,
                "images_per_scene": images_per_scene,
            },
        )

    def _generate_or_placeholder(
        self,
        *,
        output_path: Path,
        prompt: str,
        title: str,
        subtitle: str,
    ) -> None:
        if not self.ai.generate_image(prompt=prompt, output_path=output_path):
            self._create_placeholder_image(output_path, title=title, subtitle=subtitle)

    def _variant_prompt(self, prompt: str, variant_index: int) -> str:
        prompt = prompt.strip()
        variation = IMAGE_VARIATIONS[variant_index % len(IMAGE_VARIATIONS)]
        if not prompt:
            return variation
        return f"{prompt}, {variation}"

    def _create_placeholder_image(self, output_path: Path, *, title: str, subtitle: str) -> None:
        width = self.config.render.width
        height = self.config.render.height
        image = Image.new("RGB", (width, height), "#0b1320")
        draw = ImageDraw.Draw(image)
        for y in range(height):
            mix = y / max(1, height - 1)
            color = (
                int(9 + 30 * mix),
                int(19 + 55 * mix),
                int(32 + 80 * mix),
            )
            draw.line((0, y, width, y), fill=color)

        draw.ellipse((width - 420, 80, width - 80, 420), fill="#d97706")
        draw.ellipse((90, height - 520, 450, height - 160), fill="#0f766e")
        draw.rounded_rectangle((80, 120, width - 80, height - 120), radius=42, outline="#e5e7eb", width=3)

        font_path = resolve_existing_font(self.config.fonts_dir)
        title_font = self._load_font(font_path, 76)
        body_font = self._load_font(font_path, 40)

        title_lines = wrap_text_by_width(
            title,
            measure=lambda value: draw.textbbox((0, 0), value, font=title_font)[2],
            max_width=width - 220,
            max_lines=4,
        )
        y = 320
        for line in title_lines:
            draw.text((120, y), line, font=title_font, fill="#f8fafc")
            y += 96

        summary_lines = wrap_text_by_width(
            subtitle,
            measure=lambda value: draw.textbbox((0, 0), value, font=body_font)[2],
            max_width=width - 220,
            max_lines=5,
        )
        y += 40
        for line in summary_lines:
            draw.text((120, y), line, font=body_font, fill="#dbeafe")
            y += 56

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)

    def _build_story_scene_images(self, content: GeneratedContent, run_id: str) -> ArtifactStatus:
        output_dir = self.config.output_backgrounds_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        images_per_scene = max(1, int(getattr(self.config.active_channel, "story_images_per_scene", 3) or 3))
        hook_variants = max(2, min(4, images_per_scene))

        hook_images: list[str] = []
        last_story_image: Path | None = None
        for variant_index in range(hook_variants):
            hook_path = output_dir / f"hook_{variant_index + 1:02}.png"
            last_story_image = self._generate_story_asset(
                output_path=hook_path,
                prompt=self._variant_prompt(content.hook_image_prompt or content.background_prompt, variant_index),
                title=content.hook_title or "",
                subtitle=content.topic.representative_title,
                show_text=False,
                previous_real_image=last_story_image,
            )
            hook_images.append(str(hook_path))

        scene_items: list[dict[str, object]] = []
        for scene in content.scenes:
            variant_paths: list[str] = []
            scene_last_real_image = last_story_image
            for variant_index in range(images_per_scene):
                scene_path = output_dir / f"scene_{scene.index:02}_{variant_index + 1:02}.png"
                scene_last_real_image = self._generate_story_asset(
                    output_path=scene_path,
                    prompt=self._variant_prompt(scene.image_prompt, variant_index),
                    title=scene.title,
                    subtitle=scene.summary,
                    show_text=False,
                    previous_real_image=scene_last_real_image,
                )
                variant_paths.append(str(scene_path))
            if scene_last_real_image is not None:
                last_story_image = scene_last_real_image
            scene_items.append(
                {
                    "index": scene.index,
                    "title": scene.title,
                    "paths": variant_paths,
                    "duration_seconds": scene.duration_seconds,
                }
            )

        return ArtifactStatus(
            status="created",
            provider="story-scene-images",
            path=str(output_dir),
            message="Story scene images created successfully.",
            extra={
                "hook_image": hook_images[0] if hook_images else "",
                "hook_images": hook_images,
                "scene_images": scene_items,
                "images_per_scene": images_per_scene,
            },
        )

    def _generate_story_asset(
        self,
        *,
        output_path: Path,
        prompt: str,
        title: str,
        subtitle: str,
        show_text: bool,
        previous_real_image: Path | None,
    ) -> Path | None:
        if self.ai.generate_image(prompt=prompt, output_path=output_path):
            return output_path
        fallback_image = previous_real_image if previous_real_image is not None and previous_real_image.exists() else self._latest_story_reference_image()
        if fallback_image is not None and fallback_image.exists():
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self._create_story_variant_from_reference(
                source_path=fallback_image,
                output_path=output_path,
                prompt=prompt,
                title=title,
                subtitle=subtitle,
            )
            return output_path
        self._create_placeholder_image(output_path, title=title, subtitle=subtitle, show_text=show_text)
        return None

    def _latest_story_reference_image(self) -> Path | None:
        candidates = [
            path
            for path in self.config.openai_image_cache_dir.glob("*.png")
            if path.exists() and path.stat().st_size > 64 * 1024
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0]

    def _generate_or_placeholder(
        self,
        *,
        output_path: Path,
        prompt: str,
        title: str,
        subtitle: str,
        show_text: bool = True,
    ) -> None:
        if not self.ai.generate_image(prompt=prompt, output_path=output_path):
            self._create_placeholder_image(output_path, title=title, subtitle=subtitle, show_text=show_text)

    def _create_story_variant_from_reference(
        self,
        *,
        source_path: Path,
        output_path: Path,
        prompt: str,
        title: str,
        subtitle: str,
    ) -> None:
        width = max(1280, int(self.config.render.width or 1280))
        height = max(720, int(self.config.render.height or 720))
        seed = int(hashlib.sha256(f"{prompt}|{title}|{subtitle}".encode("utf-8")).hexdigest()[:8], 16)

        image = Image.open(source_path).convert("RGB")
        src_w, src_h = image.size
        crop_ratio = 0.80 + ((seed % 9) * 0.02)
        crop_w = max(int(src_w * crop_ratio), int(src_w * 0.72))
        crop_h = max(int(src_h * crop_ratio), int(src_h * 0.72))
        max_left = max(0, src_w - crop_w)
        max_top = max(0, src_h - crop_h)
        left = seed % (max_left + 1) if max_left else 0
        top = (seed // 11) % (max_top + 1) if max_top else 0
        image = image.crop((left, top, left + crop_w, top + crop_h)).resize((width, height), Image.Resampling.LANCZOS)

        if seed % 2:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        mood_palette = [
            ((12, 26, 52, 78), (0, 0, 0, 36)),
            ((73, 30, 48, 72), (8, 10, 14, 40)),
            ((37, 61, 78, 68), (12, 8, 4, 28)),
            ((58, 43, 18, 70), (0, 0, 0, 30)),
        ]
        top_color, bottom_color = mood_palette[seed % len(mood_palette)]
        draw.rectangle((0, 0, width, int(height * 0.45)), fill=top_color)
        draw.rectangle((0, int(height * 0.45), width, height), fill=bottom_color)
        draw.rounded_rectangle(
            (int(width * 0.06), int(height * 0.08), int(width * 0.94), int(height * 0.92)),
            radius=28,
            outline=(255, 255, 255, 26),
            width=3,
        )

        image = Image.blend(image, Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), 0.28)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.3))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="PNG", optimize=True)

    def _create_placeholder_image(self, output_path: Path, *, title: str, subtitle: str, show_text: bool = True) -> None:
        width = self.config.render.width
        height = self.config.render.height
        image = Image.new("RGB", (width, height), "#0b1320")
        draw = ImageDraw.Draw(image)
        for y in range(height):
            mix = y / max(1, height - 1)
            color = (
                int(9 + 30 * mix),
                int(19 + 55 * mix),
                int(32 + 80 * mix),
            )
            draw.line((0, y, width, y), fill=color)

        draw.ellipse((width - 420, 80, width - 80, 420), fill="#d97706")
        draw.ellipse((90, height - 520, 450, height - 160), fill="#0f766e")
        draw.rounded_rectangle((80, 120, width - 80, height - 120), radius=42, outline="#e5e7eb", width=3)

        if show_text:
            font_path = resolve_existing_font(self.config.fonts_dir)
            title_font = self._load_font(font_path, 76)
            body_font = self._load_font(font_path, 40)

            title_lines = wrap_text_by_width(
                title,
                measure=lambda value: draw.textbbox((0, 0), value, font=title_font)[2],
                max_width=width - 220,
                max_lines=4,
            )
            y = 320
            for line in title_lines:
                draw.text((120, y), line, font=title_font, fill="#f8fafc")
                y += 96

            summary_lines = wrap_text_by_width(
                subtitle,
                measure=lambda value: draw.textbbox((0, 0), value, font=body_font)[2],
                max_width=width - 220,
                max_lines=5,
            )
            y += 40
            for line in summary_lines:
                draw.text((120, y), line, font=body_font, fill="#dbeafe")
                y += 56

        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)

    @staticmethod
    def _load_font(font_path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if font_path is None:
            return ImageFont.load_default()
        return ImageFont.truetype(str(font_path), size)
