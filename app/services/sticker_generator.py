"""Renders a WhatsApp/Telegram sticker (512x512 WEBP) with the classic meme look: white background,
bold black text, centered, auto-wrapped and shrunk to fit. Pure image work — no I/O beyond loading the font.
"""

import io

from PIL import Image, ImageDraw, ImageFont

SIZE = 512
MARGIN = 32
MAX_CHARS = 200
MAX_FONT_SIZE = 72
MIN_FONT_SIZE = 22
MAX_LINES = 8

# Tried in order; DejaVu Bold ships on Debian/Ubuntu (and this project's target machine).
# PIL's built-in bitmap font is the last-resort fallback so rendering never raises.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
)


class TextTooLong(ValueError):
    """The text doesn't fit even at the smallest font size and shortest lines."""


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Word-wraps to fit ``max_width``; a single word longer than the width is split by character."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        while draw.textlength(current, font=font) > max_width and len(current) > 1:
            # A single word wider than the line: break it wherever it still fits.
            split = len(current) - 1
            while split > 1 and draw.textlength(current[:split], font=font) > max_width:
                split -= 1
            lines.append(current[:split])
            current = current[split:]
        lines.append(current)
    return lines


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int):
    """Largest font size (within the allowed range) whose wrapped lines fit the box."""
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        font = _load_font(size)
        lines = _wrap(draw, text, font, max_width)
        line_height = font.getbbox("Ág")[3] + 6
        if len(lines) <= MAX_LINES and line_height * len(lines) <= max_height:
            return font, lines, line_height
    raise TextTooLong(f"El texto no entra incluso con letra chica (máx. {MAX_CHARS} caracteres).")


def render_sticker(text: str) -> bytes:
    """Renders ``text`` as a 512x512 WEBP sticker. Raises TextTooLong if it can't be made to fit."""
    text = text.strip()
    if not text:
        raise ValueError("El texto no puede estar vacío.")
    if len(text) > MAX_CHARS:
        raise TextTooLong(f"El texto es demasiado largo (máx. {MAX_CHARS} caracteres).")

    image = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    box = SIZE - 2 * MARGIN
    font, lines, line_height = _fit(draw, text, box, box)

    total_height = line_height * len(lines)
    y = (SIZE - total_height) / 2
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text(((SIZE - width) / 2, y), line, font=font, fill=(0, 0, 0, 255))
        y += line_height

    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", lossless=True)
    return buffer.getvalue()
