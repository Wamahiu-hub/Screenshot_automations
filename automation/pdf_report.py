"""
Consolidate all captured PNG screenshots into a single PDF report.

The PDF is laid out in the same order that captures are produced:

    <cover page>
    <section title: "1. General (Current Admin)">
        01_Spend_Overview/001.png
        01_Spend_Overview/002.png
        02_Expense_Details/001.png
        ...
    <section title: "2. Alex Belov">
        01_Spend_Overview/001.png
        ...
    <section title: "3. Rachita Sundar">
        ...

Uses only Pillow (already added to requirements.txt).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config


log = logging.getLogger(__name__)


PAGE_WIDTH = 1600
PAGE_HEIGHT = 1000
BG_COLOR = (10, 15, 26)         # matches AIQ dark theme (#0a0f1a)
TITLE_COLOR = (241, 245, 249)    # light gray text
SUBTITLE_COLOR = (148, 163, 184) # slate-400
ACCENT_COLOR = (56, 189, 248)    # sky-400


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def _folder_name(dashboard: dict) -> str:
    return f"{dashboard['index']:02d}_{_slug(dashboard['name'])}"


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, y: int, font, fill) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text(((PAGE_WIDTH - w) // 2, y), text, font=font, fill=fill)
    return y + h


def _make_page(lines: list[tuple[str, int, tuple[int, int, int]]]) -> Image.Image:
    """Compose a single title/section page from a list of (text, size, color)."""
    img = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    total_height = 0
    rendered = []
    for text, size, color in lines:
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        h = bbox[3] - bbox[1]
        rendered.append((text, font, color, h))
        total_height += h + 20

    y = (PAGE_HEIGHT - total_height) // 2
    for text, font, color, h in rendered:
        _draw_centered(draw, text, y, font, color)
        y += h + 20

    draw.line([(160, PAGE_HEIGHT - 80), (PAGE_WIDTH - 160, PAGE_HEIGHT - 80)],
              fill=ACCENT_COLOR, width=2)
    footer_font = _load_font(20)
    _draw_centered(draw, "AIQ Qualtrics Screenshot Automation",
                   PAGE_HEIGHT - 60, footer_font, SUBTITLE_COLOR)
    return img


def make_cover_page(users: list[str]) -> Image.Image:
    today = dt.datetime.now().strftime("%A, %B %d, %Y")
    time_str = dt.datetime.now().strftime("%H:%M")
    return _make_page([
        ("AIQ Qualtrics", 92, ACCENT_COLOR),
        ("Daily Dashboard Report", 56, TITLE_COLOR),
        ("", 20, TITLE_COLOR),
        (today, 32, SUBTITLE_COLOR),
        (f"Captured at {time_str}", 24, SUBTITLE_COLOR),
        ("", 20, TITLE_COLOR),
        (f"Users: {', '.join(users)}", 26, TITLE_COLOR),
    ])


def make_section_page(index: int, title: str, subtitle: str) -> Image.Image:
    return _make_page([
        (f"Section {index}", 32, SUBTITLE_COLOR),
        (title, 84, ACCENT_COLOR),
        ("", 12, TITLE_COLOR),
        (subtitle, 30, TITLE_COLOR),
    ])


def _dashboard_pngs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.glob("*.png"))


def _load_png_page(path: Path) -> Image.Image:
    img = Image.open(path).convert("RGB")
    return img


def consolidate_pdf(
    run_root: Path,
    sections: list[tuple[str, str, str]],
    pdf_path: Path | None = None,
) -> Path:
    """Build a single PDF containing all captures for all sections.

    Args:
        run_root: e.g. ``config.OUTPUT_DIRECTORY / "test"``.
        sections: list of ``(section_slug, user_label, subtitle)`` tuples,
            in output order. ``section_slug`` is the sub-directory under
            ``run_root`` (e.g. "General"), ``user_label`` is the human
            title shown on the section page, ``subtitle`` is a smaller
            second line.
        pdf_path: output PDF path. Defaults to
            ``run_root/AIQ_Qualtrics_Report.pdf``.

    Returns:
        The path to the written PDF.
    """
    if pdf_path is None:
        pdf_path = run_root / "AIQ_Qualtrics_Report.pdf"

    pages: list[Image.Image] = []
    user_labels = [label for _, label, _ in sections]
    pages.append(make_cover_page(user_labels))

    for idx, (section_slug, user_label, subtitle) in enumerate(sections, start=1):
        pages.append(make_section_page(idx, user_label, subtitle))

        section_dir = run_root / section_slug
        if not section_dir.is_dir():
            log.warning(
                "Section directory not found; skipping: %s", section_dir
            )
            continue

        for dashboard in sorted(config.DASHBOARDS, key=lambda d: d["index"]):
            folder = _folder_name(dashboard)
            dashboard_dir = section_dir / folder
            pngs = _dashboard_pngs(dashboard_dir)
            if not pngs:
                log.warning(
                    "No PNGs found for %s / %s", section_slug, folder
                )
                continue
            for png in pngs:
                try:
                    pages.append(_load_png_page(png))
                except Exception as exc:  # noqa: BLE001
                    log.exception("Failed to load %s: %s", png, exc)

    if not pages:
        raise RuntimeError("No pages generated; nothing to write to PDF.")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(
        pdf_path,
        save_all=True,
        append_images=pages[1:],
        format="PDF",
    )
    log.info("Wrote consolidated PDF (%d pages) to %s", len(pages), pdf_path)
    return pdf_path
