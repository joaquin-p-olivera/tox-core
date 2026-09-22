import io

import pytest
from PIL import Image

from tests.test_mention import raw

API_KEY = "test-key"


def get(client, sticker_id, key=API_KEY):
    headers = {"X-API-Key": key} if key else {}
    return client.get(f"/api/v1/stickers/{sticker_id}", headers=headers)


def test_generates_and_serves_a_real_webp(client):
    (reply,) = raw(client, "!sticker hola mundo")
    assert reply["text"] == "" and reply["mentions"] == [] and reply["audio"] is None
    sticker_id = reply["sticker"]
    assert sticker_id

    response = get(client, sticker_id)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "WEBP" and image.size == (512, 512)


def test_requires_text(client):
    for text in ("!sticker", "!sticker   "):
        (reply,) = raw(client, text)
        assert reply["sticker"] is None and reply["text"].startswith("Uso:")


def test_text_over_the_limit_gives_a_friendly_error(client):
    (reply,) = raw(client, "!sticker " + "x" * 300)
    assert reply["sticker"] is None
    assert "demasiado largo" in reply["text"]


def test_two_generations_get_different_ids_and_both_are_valid(client):
    (first,) = raw(client, "!sticker uno")
    (second,) = raw(client, "!sticker dos")
    assert first["sticker"] != second["sticker"]
    assert get(client, first["sticker"]).status_code == 200
    assert get(client, second["sticker"]).status_code == 200


def test_endpoint_requires_the_api_key(client):
    (reply,) = raw(client, "!sticker hola")
    assert get(client, reply["sticker"], key=None).status_code == 401
    assert get(client, reply["sticker"], key="wrong").status_code == 401


def test_unknown_or_expired_id_is_404(client):
    assert get(client, "does-not-exist").status_code == 404


def test_expires_after_media_cache_seconds(client, settings):
    object.__setattr__(settings, "MEDIA_CACHE_SECONDS", 10)  # minimum allowed by the Settings field
    (reply,) = raw(client, "!sticker se va a vencer")
    assert get(client, reply["sticker"]).status_code == 200

    from app.services import media_cache
    # Force the entry to look already expired without a real 10-second sleep in the test.
    entry = media_cache._store[reply["sticker"]]
    media_cache._store[reply["sticker"]] = entry.__class__(entry.data, entry.mimetype, expires_at=0)
    assert get(client, reply["sticker"]).status_code == 404
