from __future__ import annotations

from app.config import AppConfig
from app.models import RankedTopic


REALISTIC_VISUAL_STYLES = {
    "photoreal_ai_people",
    "photoreal_reenactment",
    "voice_clone",
    "synthetic_avatar",
}

REALISTIC_HINTS = (
    "실사",
    "실제 인물 재현",
    "가상 인물",
    "음성 복제",
    "포토리얼",
    "photo-real",
    "photoreal",
)


def decide_contains_synthetic_media(
    config: AppConfig,
    *,
    topic: RankedTopic | None = None,
    ai_recommendation: str | None = None,
) -> bool:
    """Decide whether the upload should be marked as altered or synthetic."""

    mode = (config.youtube.altered_content_mode or "auto").strip().lower()
    if mode == "yes":
        return True
    if mode == "no":
        return False

    if ai_recommendation:
        recommendation = ai_recommendation.strip().lower()
        if recommendation in {"yes", "true"}:
            return True
        if recommendation in {"no", "false"}:
            return False

    channel = config.active_channel
    visual_style = (channel.visual_style if channel else "").strip().lower()
    if visual_style in REALISTIC_VISUAL_STYLES:
        return True

    manual_prompts = " ".join(
        filter(
            None,
            [
                getattr(channel, "manual_background_prompt", ""),
                getattr(channel, "manual_thumbnail_prompt", ""),
            ],
        )
    ).lower()
    if any(hint in manual_prompts for hint in ("photoreal", "real person", "실사", "가상 인물", "음성 복제")):
        return True

    topic_text = " ".join(
        filter(
            None,
            [
                topic.representative_title if topic else "",
                " ".join(topic.keywords) if topic else "",
            ],
        )
    )
    if any(hint in topic_text for hint in REALISTIC_HINTS):
        return True

    return False
