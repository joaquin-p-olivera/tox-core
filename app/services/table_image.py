"""Renders a league standings table as a PNG image (Pillow only, no new heavy dependency like
matplotlib/pandas): a title, one header row, and one row per team, with each real zone/group
(e.g. Argentina's "Group A"/"Group B" this season) shown as its own labelled block.

Pure image work, fully in memory — same "never touch disk" rule as sticker_generator.py.
"""

import io
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from .football_client import Standing

# Tried in order; DejaVu ships on Debian/Ubuntu (this project's target machine), same fallback
# chain as sticker_generator.py.
_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
)
_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
)

WIDTH = 720
MARGIN = 20
TITLE_HEIGHT = 56
GROUP_HEADER_HEIGHT = 36
ROW_HEIGHT = 38
FOOTER_HEIGHT = 30
# rank, team (flexible), PJ, G, E, P, Pts
COL_WIDTHS = (56, 300, 56, 56, 56, 56, 76)
COLUMNS = ("#", "Equipo", "PJ", "G", "E", "P", "Pts")

COLOR_BG = (255, 255, 255)
COLOR_HEADER_BG = (24, 58, 92)
COLOR_HEADER_TEXT = (255, 255, 255)
COLOR_GROUP_BG = (224, 224, 224)
COLOR_ROW_ALT = (243, 246, 249)
COLOR_TEXT = (30, 30, 30)
COLOR_BORDER = (210, 210, 210)


def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _group(standings: list[Standing]) -> list[tuple[str, list[Standing]]]:
    """Groups without losing order, e.g. [("", [...])] when there's only one (no group header shown)."""
    groups: dict[str, list[Standing]] = {}
    for row in standings:
        groups.setdefault(row.group, []).append(row)
    return list(groups.items())


def render_table(country_label: str, standings: list[Standing]) -> bytes:
    """Renders the whole table (every group) as one PNG. ``standings`` must not be empty."""
    groups = _group(standings)
    height = MARGIN * 2 + TITLE_HEIGHT + FOOTER_HEIGHT
    for name, rows in groups:
        height += (GROUP_HEADER_HEIGHT if name else 0) + ROW_HEIGHT * (len(rows) + 1)  # +1 for the header row

    image = Image.new("RGB", (WIDTH, height), COLOR_BG)
    draw = ImageDraw.Draw(image)
    title_font = _load_font(_BOLD_CANDIDATES, 26)
    header_font = _load_font(_BOLD_CANDIDATES, 16)
    group_font = _load_font(_BOLD_CANDIDATES, 15)
    row_font = _load_font(_REGULAR_CANDIDATES, 15)
    footer_font = _load_font(_REGULAR_CANDIDATES, 12)

    table_width = sum(COL_WIDTHS)
    left = (WIDTH - table_width) // 2
    y = MARGIN

    title = f"Tabla — {country_label}"
    draw.text(((WIDTH - draw.textlength(title, font=title_font)) / 2, y), title, font=title_font, fill=COLOR_TEXT)
    y += TITLE_HEIGHT

    for name, rows in groups:
        if name:
            draw.rectangle((left, y, left + table_width, y + GROUP_HEADER_HEIGHT), fill=COLOR_GROUP_BG)
            draw.text((left + 12, y + (GROUP_HEADER_HEIGHT - 15) / 2), name, font=group_font, fill=COLOR_TEXT)
            y += GROUP_HEADER_HEIGHT

        draw.rectangle((left, y, left + table_width, y + ROW_HEIGHT), fill=COLOR_HEADER_BG)
        x = left
        for label, width in zip(COLUMNS, COL_WIDTHS):
            align_left = label == "Equipo"
            text_x = x + 10 if align_left else x + (width - draw.textlength(label, font=header_font)) / 2
            draw.text((text_x, y + (ROW_HEIGHT - 16) / 2), label, font=header_font, fill=COLOR_HEADER_TEXT)
            x += width
        y += ROW_HEIGHT

        for i, row in enumerate(rows):
            if i % 2 == 1:
                draw.rectangle((left, y, left + table_width, y + ROW_HEIGHT), fill=COLOR_ROW_ALT)
            values = (str(row.rank), row.team, str(row.played), str(row.won), str(row.drawn), str(row.lost), str(row.points))
            x = left
            for label, value, width in zip(COLUMNS, values, COL_WIDTHS):
                align_left = label == "Equipo"
                text = _truncate(draw, value, row_font, width - 16) if align_left else value
                text_x = x + 10 if align_left else x + (width - draw.textlength(text, font=row_font)) / 2
                draw.text((text_x, y + (ROW_HEIGHT - 15) / 2), text, font=row_font, fill=COLOR_TEXT)
                x += width
            draw.line((left, y + ROW_HEIGHT, left + table_width, y + ROW_HEIGHT), fill=COLOR_BORDER)
            y += ROW_HEIGHT

    draw.rectangle((left, MARGIN + TITLE_HEIGHT, left + table_width, y), outline=COLOR_BORDER)
    footer = f"Actualizado {datetime.now().astimezone():%d/%m %H:%M}"
    draw.text(((WIDTH - draw.textlength(footer, font=footer_font)) / 2, y + 8), footer, font=footer_font, fill=(120, 120, 120))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
