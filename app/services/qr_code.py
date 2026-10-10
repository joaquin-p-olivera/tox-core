"""Renders a QR code as a PNG. Pure image work: nothing touches the disk or the network."""

import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M

MAX_CHARS = 500  # a QR of this size is still easy to scan from a phone screen


class TextTooLong(ValueError):
    """The text doesn't fit the length limit."""


def render_qr(text: str) -> bytes:
    text = text.strip()
    if not text:
        raise ValueError("El texto no puede estar vacío.")
    if len(text) > MAX_CHARS:
        raise TextTooLong(f"El texto es demasiado largo (máx. {MAX_CHARS} caracteres).")
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=10, border=4)
    code.add_data(text)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return buffer.getvalue()
