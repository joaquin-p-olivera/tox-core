import io

from PIL import Image

from app.services import table_image as ti
from app.services.football_client import Standing


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_renders_a_png_sized_to_the_number_of_rows():
    rows = [Standing(1, "Peñarol", 30, 15, 9, 3, 3), Standing(2, "Nacional", 28, 15, 8, 4, 3)]
    image = _open(ti.render_table("Uruguay", rows))
    assert image.format == "PNG"
    assert image.width == ti.WIDTH
    # 2 groups worth of rows would be taller than 1's: a cheap sanity check that height scales with content.
    taller = _open(ti.render_table("Uruguay", rows * 3))
    assert taller.height > image.height


def test_two_real_groups_both_appear():
    rows = [Standing(1, "Estudiantes", 30, 16, 9, 3, 4, group="Zona A"),
            Standing(1, "River Plate", 29, 16, 9, 2, 5, group="Zona B")]
    image = _open(ti.render_table("Argentina", rows)).convert("RGB")
    # Group header bars are drawn in a distinct grey, so at least one pixel in that row must show it.
    assert ti.COLOR_GROUP_BG in {image.getpixel((x, y)) for x in range(0, image.width, 5)
                                  for y in range(0, image.height, 5)}


def test_a_very_long_team_name_is_truncated_not_overflowed():
    rows = [Standing(1, "Un Nombre De Equipo Absurdamente Largo Para Una Sola Celda De La Tabla", 10, 5, 2, 1, 2)]
    # Must not raise, and must produce the expected fixed width regardless of content length.
    image = _open(ti.render_table("España", rows))
    assert image.width == ti.WIDTH
