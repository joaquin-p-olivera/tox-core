"""Unit tests mock the network call (_synthesize_mp3) and the ffmpeg subprocess boundary, so the
suite is fast, deterministic and offline (consistent with how github_client/service_status are tested).
The real end-to-end path (actual network + actual ffmpeg) was verified manually, not as a committed test."""

import subprocess
import time

import pytest

from app.services import tts_generator as tts


def test_default_voice_is_a_known_voice():
    assert tts.DEFAULT_VOICE in tts.VOICES
    assert tts.DEFAULT_VOICE == "hombre"


def test_voices_are_uruguayan_and_named_by_gender():
    assert tts.VOICES == {"hombre": "es-UY-MateoNeural", "mujer": "es-UY-ValentinaNeural"}


def test_flags_are_derived_from_the_voice_keys():
    assert tts.FLAG_VOICES == {"h": "hombre", "m": "mujer"}


def test_empty_text_is_rejected(monkeypatch):
    monkeypatch.setattr(tts, "_synthesize_mp3", lambda text, voice: pytest.fail("must not be called"))
    with pytest.raises(ValueError, match="vacío"):
        tts.synthesize("", "mujer", 20)
    with pytest.raises(ValueError, match="vacío"):
        tts.synthesize("   ", "mujer", 20)


def test_text_over_the_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(tts, "_synthesize_mp3", lambda text, voice: pytest.fail("must not be called"))
    with pytest.raises(tts.TextTooLong, match=str(tts.MAX_CHARS)):
        tts.synthesize("x" * (tts.MAX_CHARS + 1), "mujer", 20)


def test_happy_path_calls_synthesis_then_conversion_with_the_right_voice(monkeypatch):
    calls = {}

    def fake_synthesize(text, voice):
        calls["synthesize"] = (text, voice)
        return b"fake-mp3-bytes"

    def fake_convert(mp3_data, timeout_seconds):
        calls["convert"] = (mp3_data, timeout_seconds)
        return b"fake-ogg-bytes"

    monkeypatch.setattr(tts, "_synthesize_mp3", fake_synthesize)
    monkeypatch.setattr(tts, "_convert_to_ogg_opus", fake_convert)

    result = tts.synthesize("hola che", "hombre", 15)
    assert result == b"fake-ogg-bytes"
    assert calls["synthesize"] == ("hola che", "es-UY-MateoNeural")
    assert calls["convert"] == (b"fake-mp3-bytes", 15)


def test_a_slow_synthesis_is_abandoned_at_the_timeout_not_waited_on(monkeypatch):
    """The wall-clock timeout must fire even if the underlying call would eventually finish or hang."""
    def slow_synthesize(text, voice):
        time.sleep(2)
        return b"too-late"

    monkeypatch.setattr(tts, "_synthesize_mp3", slow_synthesize)
    started = time.monotonic()
    with pytest.raises(tts.TtsError, match="no respondió a tiempo"):
        tts.synthesize("hola", "mujer", timeout_seconds=0.2)
    assert time.monotonic() - started < 1.0, "synthesize() must return promptly, not wait for the slow call"


def test_an_unexpected_error_from_synthesis_becomes_a_friendly_tts_error(monkeypatch):
    def boom(text, voice):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(tts, "_synthesize_mp3", boom)
    with pytest.raises(tts.TtsError, match="Probá de nuevo"):
        tts.synthesize("hola", "mujer", 20)


def test_no_audio_chunks_is_a_tts_error(monkeypatch):
    def no_audio(text, voice):
        raise tts.TtsError("El servicio de voz no devolvió audio. Probá con otro texto.")

    monkeypatch.setattr(tts, "_synthesize_mp3", no_audio)
    with pytest.raises(tts.TtsError, match="no devolvió audio"):
        tts.synthesize("hola", "mujer", 20)


def test_synthesize_mp3_raises_when_edge_tts_returns_nothing(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice):
            pass

        def stream_sync(self):
            return iter([{"type": "WordBoundary"}])  # no "audio" chunks at all

    monkeypatch.setattr(tts.edge_tts, "Communicate", FakeCommunicate)
    with pytest.raises(tts.TtsError, match="no devolvió audio"):
        tts._synthesize_mp3("hola", "es-UY-ValentinaNeural")


def test_synthesize_mp3_joins_audio_chunks_in_order(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice):
            pass

        def stream_sync(self):
            return iter([
                {"type": "audio", "data": b"AB"},
                {"type": "WordBoundary"},
                {"type": "audio", "data": b"CD"},
                {"type": "audio", "data": None},  # defensive: a chunk claiming to be audio with no data
            ])

    monkeypatch.setattr(tts.edge_tts, "Communicate", FakeCommunicate)
    assert tts._synthesize_mp3("hola", "es-UY-ValentinaNeural") == b"ABCD"


# ---- ffmpeg conversion boundary -------------------------------------------------------------------

def test_convert_runs_ffmpeg_with_the_expected_arguments(monkeypatch):
    captured = {}

    def fake_run(argv, input, capture_output, timeout, check):
        captured.update(argv=argv, input=input, timeout=timeout)
        return subprocess.CompletedProcess(argv, 0, stdout=b"ogg-bytes", stderr=b"")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    result = tts._convert_to_ogg_opus(b"mp3-bytes", timeout_seconds=9)
    assert result == b"ogg-bytes"
    assert captured["input"] == b"mp3-bytes"
    assert captured["timeout"] == 9
    argv = captured["argv"]
    assert argv[0] == "ffmpeg"
    assert "libopus" in argv and "pipe:0" in argv and "pipe:1" in argv


def test_convert_timeout_becomes_tts_error(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.TtsError, match="tardó demasiado"):
        tts._convert_to_ogg_opus(b"data", timeout_seconds=5)


def test_missing_ffmpeg_becomes_a_clear_tts_error(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.TtsError, match="No encontré ffmpeg"):
        tts._convert_to_ogg_opus(b"data", timeout_seconds=5)


def test_ffmpeg_nonzero_exit_becomes_a_tts_error(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"invalid data found")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.TtsError, match="No pude convertir"):
        tts._convert_to_ogg_opus(b"garbage", timeout_seconds=5)


def test_ffmpeg_empty_stdout_becomes_a_tts_error(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.TtsError, match="No pude convertir"):
        tts._convert_to_ogg_opus(b"garbage", timeout_seconds=5)


def test_other_os_error_becomes_a_tts_error(monkeypatch):
    def fake_run(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    with pytest.raises(tts.TtsError, match="No pude convertir el audio:"):
        tts._convert_to_ogg_opus(b"data", timeout_seconds=5)


def test_mimetype_constant_matches_what_the_bots_treat_as_a_voice_note():
    assert tts.MIMETYPE == "audio/ogg; codecs=opus"
