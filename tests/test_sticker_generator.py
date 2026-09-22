import io

import pytest
from PIL import Image

from app.services import sticker_generator as sg


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_renders_a_512x512_webp():
    data = sg.render_sticker("hola mundo")
    image = _open(data)
    assert image.format == "WEBP"
    assert image.size == (sg.SIZE, sg.SIZE)


def test_background_is_white_and_has_black_pixels_for_the_text():
    image = _open(sg.render_sticker("A")).convert("RGB")
    # Corners should be untouched background; a very short glyph is centered, far from the edges.
    assert image.getpixel((2, 2)) == (255, 255, 255)
    colors = {image.getpixel((x, y)) for x in range(0, sg.SIZE, 4) for y in range(0, sg.SIZE, 4)}
    assert (0, 0, 0) in colors, "some pixel should be black text ink"


def test_empty_text_is_rejected():
    with pytest.raises(ValueError, match="vacío"):
        sg.render_sticker("")
    with pytest.raises(ValueError, match="vacío"):
        sg.render_sticker("   ")


def test_text_over_the_limit_is_rejected():
    with pytest.raises(sg.TextTooLong, match=str(sg.MAX_CHARS)):
        sg.render_sticker("a" * (sg.MAX_CHARS + 1))


def test_text_at_the_limit_is_accepted():
    data = sg.render_sticker("a " * (sg.MAX_CHARS // 2))
    assert _open(data).size == (sg.SIZE, sg.SIZE)


def test_a_single_word_longer_than_the_line_is_broken_by_character():
    lines = sg._wrap(_DummyDraw(), "a" * 100, _DummyFont(char_width=10), max_width=100)
    assert len(lines) > 1
    assert all(len(line) <= 10 for line in lines)
    assert "".join(lines) == "a" * 100


def test_wrap_keeps_whole_words_together_when_they_fit():
    lines = sg._wrap(_DummyDraw(), "uno dos tres", _DummyFont(char_width=10), max_width=90)
    assert lines == ["uno dos", "tres"]


def test_wrap_preserves_explicit_newlines():
    lines = sg._wrap(_DummyDraw(), "linea uno\nlinea dos", _DummyFont(char_width=10), max_width=1000)
    assert lines == ["linea uno", "linea dos"]


def test_a_short_phrase_renders_without_raising():
    for text in ("GG", "Hi", "😀", "¿Qué onda?"):
        data = sg.render_sticker(text)
        assert _open(data).size == (sg.SIZE, sg.SIZE)


def test_a_word_with_no_spaces_that_still_fits_is_not_split():
    text = "supercalifragilisticoso"
    lines = sg._wrap(_DummyDraw(), text, _DummyFont(char_width=5), max_width=1000)
    assert lines == [text]


class _DummyFont:
    """A monospace stand-in so `_wrap`'s width math is exact and independent of the real font file."""

    def __init__(self, char_width: int):
        self.char_width = char_width


class _DummyDraw:
    def textlength(self, text: str, font: _DummyFont) -> float:
        return len(text) * font.char_width
