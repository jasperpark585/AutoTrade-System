from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import os
import re
import unicodedata


def normalize_text(value: str) -> str:
    """Normalize Korean or English text for duplicate detection and filenames."""

    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.casefold()
    normalized = re.sub(r"[^\w\s가-힣]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def similarity(left: str, right: str) -> float:
    """Return normalized string similarity."""

    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def slugify(value: str, max_length: int = 60) -> str:
    """Create a filesystem-safe slug."""

    normalized = normalize_text(value)
    slug = re.sub(r"\s+", "-", normalized).strip("-")
    if not slug:
        slug = "trend-item"
    return slug[:max_length]


def unique_preserve_order(values: list[str]) -> list[str]:
    """Remove duplicates while preserving the original order."""

    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = normalize_text(value)
        if key and key not in seen:
            seen.add(key)
            output.append(value.strip())
    return output


def resolve_existing_font(fonts_dir: Path) -> Path | None:
    """Pick a usable font from project assets or common system locations."""

    for pattern in ("*.ttf", "*.otf", "*.ttc"):
        matches = sorted(fonts_dir.glob(pattern))
        if matches:
            return matches[0]

    windir = Path(os.environ.get("WINDIR", "C:/Windows"))
    candidates = [
        windir / "Fonts" / "malgun.ttf",
        windir / "Fonts" / "malgunbd.ttf",
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumSquareR.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def wrap_text_by_width(
    text: str,
    *,
    measure: callable,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """Wrap text using a measurement callback that returns rendered width in pixels."""

    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return [""]

    words = text.split(" ")
    if len(words) == 1:
        return _wrap_characters(text, measure=measure, max_width=max_width, max_lines=max_lines)

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if measure(candidate) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines - 1:
            break

    if current and len(lines) < max_lines:
        remaining_words = words[len(" ".join(lines).split()) + len(current.split()) :]
        if remaining_words:
            current = current.rstrip(". ") + "..."
        lines.append(current)
    elif len(lines) >= max_lines and lines:
        lines[-1] = lines[-1].rstrip(". ") + "..."

    if not lines:
        return _wrap_characters(text, measure=measure, max_width=max_width, max_lines=max_lines)
    return lines[:max_lines]


def _wrap_characters(
    text: str,
    *,
    measure: callable,
    max_width: int,
    max_lines: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = f"{current}{char}"
        if current and measure(candidate) > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines - 1:
                break
        else:
            current = candidate

    remainder = text[len("".join(lines)) + len(current) :]
    if current and len(lines) < max_lines:
        if remainder:
            current = current.rstrip(". ") + "..."
        lines.append(current)
    elif remainder and lines:
        lines[-1] = lines[-1].rstrip(". ") + "..."
    return lines[:max_lines] or [text[: max_lines * 6]]
