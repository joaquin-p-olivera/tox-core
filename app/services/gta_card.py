"""The GTA VI countdown as an image (services/gta_countdown.py): the artwork on top and a bar with the days, hours,
minutes and seconds left, in Vice City colours.

Pillow only, fully in memory, same rule as sticker_generator.py and table_image.py: nothing is ever written to disk.

The pictures live in GTA_ARTWORK_DIR (`media/gta6`, and `media/` is git-ignored): they are Rockstar's, handed out
on their own site as wallpapers, so they stay on this machine and out of the repo. One is picked at random for each
card and centre-cropped to a square. With no folder, no readable picture in it, or a file that turns out not to be
an image, the card falls back without a word to the sunset drawn here, which is why that drawing exists at all.
"""

import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Same fallback chain as sticker_generator.py; DejaVu ships on Debian/Ubuntu, this project's target machine.
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

# Square on purpose: WhatsApp on iOS crops a preview taller than about 1:1, and a 720x898 card lost the numbers off
# the bottom of the bubble (Android showed it whole). The bar keeps its own place under the picture, so what gives is
# the picture's shape: it is the card's width by whatever is left over, a landscape crop instead of a square one.
WIDTH = 900
HEIGHT = WIDTH
MARGIN = 24
GAP = 20
BAR_HEIGHT = 150
ART_WIDTH = WIDTH - 2 * MARGIN
ART_HEIGHT = HEIGHT - 2 * MARGIN - GAP - BAR_HEIGHT
RADIUS = 28
VERTICAL_BIAS = 0.35  # 0.5 would be the dead centre; a bit higher keeps heads inside the crop
MIMETYPE = "image/png"

# The same card as a 9:16 story: it sits on its own artwork, blurred and darkened so the card is what reads.
# JPEG because that is the only image format Instagram's publishing API takes.
STORY_WIDTH, STORY_HEIGHT = 1080, 1920
STORY_CARD = 960  # how wide the card is inside the story
STORY_BLUR = 30
STORY_DARKEN = 0.45  # how far the background is pushed towards black
STORY_RADIUS = 40
STORY_MIMETYPE = "image/jpeg"

# Sampled off the reference the owner sent, so the card looks like the countdown they had in mind
COLOR_BG = (228, 171, 141)
COLOR_BAR = (92, 64, 49)
COLOR_NUMBER = (255, 255, 255)
COLOR_LABEL = (214, 205, 200)
COLOR_SEPARATOR = (142, 120, 109)

# The sunset, top to bottom
SKY = ((58, 38, 96), (122, 58, 122), (205, 85, 128), (240, 134, 112), (250, 183, 118))
SUN = ((255, 227, 150), (255, 150, 120))
SEA = ((70, 40, 92), (128, 60, 118))
PALM = (28, 16, 44)

# What counts as an artwork in GTA_ARTWORK_DIR. Read from disk on every card, so adding one needs no restart.
ARTWORK_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

LABELS = ("días", "horas", "minutos", "segundos")
ARRIVED_LINE = "BIENVENIDOS A VICE CITY"
TITLE = "GRAND THEFT AUTO"


def list_artworks(directory: str) -> list[Path]:
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir()
                  if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in ARTWORK_SUFFIXES)


def _font(candidates: tuple[str, ...], size: int):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _blend(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * ratio) for a, b in zip(start, end))  # type: ignore[return-value]


def _gradient(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], stops: tuple) -> None:
    """Fills `box` with a vertical gradient through `stops`, one line at a time."""
    left, top, right, bottom = box
    height = bottom - top
    spans = len(stops) - 1
    for offset in range(height):
        position = offset / max(height - 1, 1) * spans
        index = min(int(position), spans - 1)
        draw.line([(left, top + offset), (right, top + offset)],
                  fill=_blend(stops[index], stops[index + 1], position - index))


def _bezier(start: tuple[float, float], control: tuple[float, float], end: tuple[float, float],
            steps: int = 16) -> list[tuple[float, float]]:
    points = []
    for step in range(steps + 1):
        t = step / steps
        inverse = 1 - t
        points.append((inverse * inverse * start[0] + 2 * inverse * t * control[0] + t * t * end[0],
                       inverse * inverse * start[1] + 2 * inverse * t * control[1] + t * t * end[1]))
    return points


def _frond(draw: ImageDraw.ImageDraw, crown: tuple[float, float], angle: float, length: float, width: float) -> None:
    """One leaf: a spine that arcs out of the crown and droops, fleshed out into a pointed polygon."""
    reach = (crown[0] + math.cos(angle) * length, crown[1] - math.sin(angle) * length)
    control = (crown[0] + math.cos(angle) * length * 0.55, crown[1] - math.sin(angle) * length * 0.75 - length * 0.12)
    tip = (reach[0] + math.cos(angle) * length * 0.1, reach[1] + length * 0.42)  # the droop at the end
    spine = _bezier(crown, control, tip)
    upper, lower = [], []
    for index, (x, y) in enumerate(spine):
        t = index / (len(spine) - 1)
        thickness = width * math.sin(math.pi * t) ** 0.7
        previous = spine[max(index - 1, 0)]
        following = spine[min(index + 1, len(spine) - 1)]
        dx, dy = following[0] - previous[0], following[1] - previous[1]
        norm = math.hypot(dx, dy) or 1
        upper.append((x - dy / norm * thickness, y + dx / norm * thickness))
        lower.append((x + dy / norm * thickness, y - dx / norm * thickness))
    draw.polygon(upper + lower[::-1], fill=PALM)


def _palm(draw: ImageDraw.ImageDraw, foot: tuple[int, int], height: int, lean: int, fronds: int = 8) -> None:
    """A palm silhouette: a trunk leaning away from the middle of the picture, and its fronds drooping off the crown."""
    x, y = foot
    crown = (x + lean, y - height)
    spine = _bezier((x, y), (x + lean * 0.35, y - height * 0.6), crown, steps=60)
    for index, point in enumerate(spine):  # overlapping discs, so the trunk tapers towards the crown
        radius = height * 0.026 * (1 - 0.5 * index / (len(spine) - 1))
        draw.ellipse((point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius), fill=PALM)
    for index in range(fronds):
        angle = math.pi * (0.08 + 0.84 * index / (fronds - 1))
        _frond(draw, crown, angle, height * (0.40 if index % 2 else 0.46), height * 0.028)
    for offset in (-1, 0, 1):  # a few coconuts under the crown
        draw.ellipse((crown[0] + offset * 9 - 7, crown[1] + 8, crown[0] + offset * 9 + 7, crown[1] + 22), fill=PALM)


def _crop_to(picture: Image.Image, width: int, height: int) -> Image.Image:
    """The middle of `picture` in the shape of the box it has to fill, scaled to it."""
    wanted = width / height
    if picture.width / picture.height > wanted:  # too wide: take a column out of the middle
        side = round(picture.height * wanted)
        left = (picture.width - side) // 2
        box = (left, 0, left + side, picture.height)
    else:  # too tall: take a band a little above the middle, which is where faces and skylines sit
        side = round(picture.width / wanted)
        top = round((picture.height - side) * VERTICAL_BIAS)
        box = (0, top, picture.width, top + side)
    return picture.crop(box).resize((width, height), Image.LANCZOS)


def _artwork(width: int, height: int, artwork_path: str | None) -> Image.Image:
    """The picture on top: the owner's own file if there is one, else the sunset drawn here."""
    if artwork_path:
        try:
            with Image.open(artwork_path) as handle:
                return _crop_to(handle.convert("RGB"), width, height)
        except (OSError, ValueError):
            pass  # unreadable, not an image, gone: the drawing below is always available

    # The sunset is composed square and then cropped to the box, so its own geometry stays simple
    size = max(width, height)
    art = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(art)
    horizon = round(size * 0.62)
    _gradient(draw, (0, 0, size, horizon), SKY)

    sun_radius = round(size * 0.17)
    sun_centre = (size // 2, horizon - round(size * 0.04))
    sun = Image.new("RGB", (sun_radius * 2, sun_radius * 2))
    _gradient(ImageDraw.Draw(sun), (0, 0, sun_radius * 2, sun_radius * 2), SUN)
    mask = Image.new("L", sun.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, sun_radius * 2 - 1, sun_radius * 2 - 1), fill=255)
    art.paste(sun, (sun_centre[0] - sun_radius, sun_centre[1] - sun_radius), mask)
    # the horizontal slits that make a synthwave sun: inside the disc only, thicker towards the bottom
    for step in range(5):
        y = sun_centre[1] + round(sun_radius * (0.18 + step * 0.16))
        if y > horizon:
            break
        half = math.sqrt(max(sun_radius ** 2 - (y - sun_centre[1]) ** 2, 0))
        draw.line([(sun_centre[0] - half, y), (sun_centre[0] + half, y)],
                  fill=_blend(SKY[2], SKY[3], 0.5), width=3 + step * 2)

    _gradient(draw, (0, horizon, size, size), SEA)  # over the sun, so it is cut by the horizon as it sets
    for offset in range(1, 7):  # the sun's reflection on the water
        y = horizon + offset * round(size * 0.025)
        half = round(sun_radius * (1 - offset * 0.12))
        draw.line([(sun_centre[0] - half, y), (sun_centre[0] + half, y)],
                  fill=_blend(SEA[1], SUN[1], 0.45 - offset * 0.06), width=4)

    _palm(draw, (round(size * 0.16), size + 40), round(size * 0.74), lean=-round(size * 0.06))
    _palm(draw, (round(size * 0.87), size + 40), round(size * 0.62), lean=round(size * 0.05))

    title_font = _font(_BOLD_CANDIDATES, round(size * 0.052))
    spaced = " ".join(TITLE)
    draw.text((size / 2, round(size * 0.12)), spaced, font=title_font, fill=(255, 255, 255), anchor="mm")

    vi_font = _font(_BOLD_CANDIDATES, round(size * 0.3))
    glow = Image.new("RGB", (size, size))
    ImageDraw.Draw(glow).text((size / 2, round(size * 0.3)), "VI", font=vi_font, fill=(255, 150, 210), anchor="mm")
    art.paste(Image.blend(art, glow.filter(ImageFilter.GaussianBlur(18)), 0.85), (0, 0),
              glow.filter(ImageFilter.GaussianBlur(18)).convert("L"))
    draw.text((size / 2, round(size * 0.3)), "VI", font=vi_font, fill=(255, 255, 255), anchor="mm")
    return _crop_to(art, width, height)


def _rounded(picture: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", picture.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, picture.width - 1, picture.height - 1), radius=radius, fill=255)
    picture.putalpha(mask)
    return picture


def _card(artwork_path: str | None) -> tuple[Image.Image, ImageDraw.ImageDraw, tuple[int, int, int, int]]:
    """The card with its artwork and its empty bar, plus the bar's box for the caller to fill."""
    card = Image.new("RGB", (WIDTH, HEIGHT), COLOR_BG)
    art = _rounded(_artwork(ART_WIDTH, ART_HEIGHT, artwork_path).convert("RGBA"), RADIUS)
    card.paste(art, (MARGIN, MARGIN), art)
    draw = ImageDraw.Draw(card)
    top = MARGIN + ART_HEIGHT + GAP
    bar = (MARGIN, top, WIDTH - MARGIN, top + BAR_HEIGHT)
    draw.rounded_rectangle(bar, radius=RADIUS, fill=COLOR_BAR)
    return card, draw, bar


def _png(card: Image.Image) -> bytes:
    buffer = io.BytesIO()
    card.save(buffer, format="PNG")
    return buffer.getvalue()


def countdown_card(days: int, hours: int, minutes: int, seconds: int, artwork_path: str | None = None) -> Image.Image:
    """The countdown card: four cells under the artwork, each a number over what it counts."""
    card, draw, bar = _card(artwork_path)
    left, top, right, bottom = bar
    number_font = _font(_BOLD_CANDIDATES, 62)
    label_font = _font(_REGULAR_CANDIDATES, 24)
    cell = (right - left) / 4
    height = bottom - top
    for index, (value, label) in enumerate(zip((days, hours, minutes, seconds), LABELS)):
        centre = left + cell * (index + 0.5)
        draw.text((centre, top + height * 0.40), str(value), font=number_font, fill=COLOR_NUMBER, anchor="mm")
        draw.text((centre, top + height * 0.74), label, font=label_font, fill=COLOR_LABEL, anchor="mm")
        if index:  # a hairline between cells, like the reference
            x = left + cell * index
            draw.line([(x, top + 30), (x, bottom - 30)], fill=COLOR_SEPARATOR, width=2)
    return card


def arrived_card(artwork_path: str | None = None) -> Image.Image:
    """The one card that is not a countdown: the day it finally comes out."""
    card, draw, bar = _card(artwork_path)
    left, top, right, bottom = bar
    draw.text(((left + right) / 2, (top + bottom) / 2), ARRIVED_LINE,
              font=_font(_BOLD_CANDIDATES, 46), fill=COLOR_NUMBER, anchor="mm")
    return card


def render(days: int, hours: int, minutes: int, seconds: int, artwork_path: str | None = None) -> bytes:
    """The countdown card as a PNG, which is what goes to the chats."""
    return _png(countdown_card(days, hours, minutes, seconds, artwork_path))


def render_arrived(artwork_path: str | None = None) -> bytes:
    return _png(arrived_card(artwork_path))


def _story(card: Image.Image, artwork_path: str | None) -> bytes:
    """The card centred on a 9:16 canvas, over its own artwork blurred out of the way."""
    background = _artwork(STORY_WIDTH, STORY_HEIGHT, artwork_path).filter(ImageFilter.GaussianBlur(STORY_BLUR))
    background = Image.blend(background, Image.new("RGB", background.size, (0, 0, 0)), STORY_DARKEN)
    scaled = _rounded(card.resize((STORY_CARD, STORY_CARD), Image.LANCZOS).convert("RGBA"), STORY_RADIUS)
    background.paste(scaled, ((STORY_WIDTH - STORY_CARD) // 2, (STORY_HEIGHT - STORY_CARD) // 2), scaled)
    buffer = io.BytesIO()
    background.save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def render_story(days: int, hours: int, minutes: int, seconds: int, artwork_path: str | None = None) -> bytes:
    """The countdown as a story: 1080x1920 JPEG, the shape and format a social network wants."""
    return _story(countdown_card(days, hours, minutes, seconds, artwork_path), artwork_path)


def render_arrived_story(artwork_path: str | None = None) -> bytes:
    return _story(arrived_card(artwork_path), artwork_path)
