import pytest

from app.services import tts_generator
from tests.test_mention import raw

API_KEY = "test-key"


@pytest.fixture
def fake_synthesize(monkeypatch):
    """Replaces the real network+ffmpeg call. calls[] records (text, voice, timeout_seconds)."""
    calls = []

    def fake(text, voice, timeout_seconds):
        calls.append((text, voice, timeout_seconds))
        return b"FAKE-OGG-BYTES"

    monkeypatch.setattr(tts_generator, "synthesize", fake)
    return calls


def get(client, audio_id, key=API_KEY):
    headers = {"X-API-Key": key} if key else {}
    return client.get(f"/api/v1/audios/{audio_id}", headers=headers)


def test_default_voice_is_hombre(client, fake_synthesize):
    (reply,) = raw(client, "!voz hola a todos")
    assert reply["text"] == "" and reply["mentions"] == [] and reply["sticker"] is None
    assert fake_synthesize == [("hola a todos", "hombre", pytest.approx(20))]
    assert reply["audio"]


@pytest.mark.parametrize("flag", ["-h", "-H", "--h", "–h"])  # last one: phone keyboard en dash
def test_hombre_flag_spellings(client, fake_synthesize, flag):
    raw(client, f"!voz {flag} buenas")
    assert fake_synthesize == [("buenas", "hombre", pytest.approx(20))]


@pytest.mark.parametrize("flag", ["-m", "-M", "--m", "–m"])
def test_mujer_flag_spellings(client, fake_synthesize, flag):
    raw(client, f"!voz {flag} buenas")
    assert fake_synthesize == [("buenas", "mujer", pytest.approx(20))]


def test_requires_text(client, fake_synthesize):
    for text in ("!voz", "!voz   ", "!voz -h", "!voz -h   ", "!voz -m", "!voz -m   "):
        (reply,) = raw(client, text)
        assert reply["text"].startswith("Uso:") and reply["audio"] is None
    assert fake_synthesize == []


def test_unknown_flag_is_rejected_and_nothing_is_synthesized(client, fake_synthesize):
    (reply,) = raw(client, "!voz -pepe hola")
    assert reply["text"].startswith("Uso:") and reply["audio"] is None
    assert fake_synthesize == []


def test_a_leading_hyphenated_word_that_is_not_a_known_voice_is_rejected(client, fake_synthesize):
    """Documents the trade-off: only a LEADING token is ever treated as a flag attempt, but if it starts
    with "-" it must be -h or -m or the command is rejected outright (it isn't spoken literally)."""
    (reply,) = raw(client, "!voz -1 grados hace hoy")
    assert reply["text"].startswith("Uso:")
    assert fake_synthesize == []


def test_a_non_leading_hyphenated_word_is_spoken_literally(client, fake_synthesize):
    raw(client, "!voz el resultado fue -3 por goleada")
    assert fake_synthesize == [("el resultado fue -3 por goleada", "hombre", pytest.approx(20))]


def test_serves_the_generated_audio_with_the_right_mimetype(client, fake_synthesize):
    (reply,) = raw(client, "!voz hola")
    response = get(client, reply["audio"])
    assert response.status_code == 200
    assert response.content == b"FAKE-OGG-BYTES"
    assert response.headers["content-type"] == "audio/ogg; codecs=opus"


def test_endpoint_requires_the_api_key(client, fake_synthesize):
    (reply,) = raw(client, "!voz hola")
    assert get(client, reply["audio"], key=None).status_code == 401
    assert get(client, reply["audio"], key="wrong").status_code == 401


def test_two_calls_get_different_ids(client, fake_synthesize):
    (first,) = raw(client, "!voz uno")
    (second,) = raw(client, "!voz dos")
    assert first["audio"] != second["audio"]
    assert get(client, first["audio"]).status_code == 200
    assert get(client, second["audio"]).status_code == 200


def test_text_too_long_is_a_friendly_error(client, monkeypatch):
    def fake(text, voice, timeout_seconds):
        raise tts_generator.TextTooLong("El texto es demasiado largo (máx. 300 caracteres).")

    monkeypatch.setattr(tts_generator, "synthesize", fake)
    (reply,) = raw(client, "!voz " + "x" * 400)
    assert reply["audio"] is None and "demasiado largo" in reply["text"]


def test_synthesis_failure_is_a_friendly_error_not_a_crash(client, monkeypatch):
    def fake(text, voice, timeout_seconds):
        raise tts_generator.TtsError("El servicio de voz no respondió a tiempo. Probá de nuevo en un rato.")

    monkeypatch.setattr(tts_generator, "synthesize", fake)
    (reply,) = raw(client, "!voz hola")
    assert reply["audio"] is None and "no respondió a tiempo" in reply["text"]


def test_the_timeout_setting_is_forwarded(client, settings, fake_synthesize):
    object.__setattr__(settings, "TTS_TIMEOUT_SECONDS", 33)
    raw(client, "!voz hola")
    assert fake_synthesize[0][2] == 33
