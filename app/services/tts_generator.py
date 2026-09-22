"""Text-to-speech for !voz, via Microsoft Edge's neural voices (unofficial, free, no API key —
the same backend behind Edge's "Read aloud" feature). Network-bound: no heavy model runs on this machine.

Two stages, each with its own hard wall-clock timeout:
1. edge-tts synthesizes MP3 over the network. Its own connect/receive timeouts were observed NOT to be
   fully reliable (a broken connection can hang past them), so the whole call runs in a background thread
   and is abandoned — not cancelled, just no longer waited on — if it takes too long.
2. ffmpeg converts that MP3 to Ogg/Opus, the format WhatsApp shows as a voice note (`ptt`) and the only
   one Telegram's `sendVoice` accepts as an actual voice message rather than a plain audio file.
"""

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import edge_tts

logger = logging.getLogger("tox.tts")

# key (what the chat types, by gender) -> Microsoft Edge neural voice name. Both are Uruguayan Spanish.
VOICES = {
    "hombre": "es-UY-MateoNeural",
    "mujer": "es-UY-ValentinaNeural",
}
DEFAULT_VOICE = "hombre"
# One-letter flag (what !voz accepts, e.g. "-h") -> VOICES key. Derived from the key's own initial, so a
# renamed or added voice never falls out of sync with its flag.
FLAG_VOICES = {name[0]: name for name in VOICES}
MAX_CHARS = 300
MIMETYPE = "audio/ogg; codecs=opus"


class TextTooLong(ValueError):
    """The text is over MAX_CHARS."""


class TtsError(Exception):
    """Synthesis or conversion failed. The message is safe to show in the chat."""


def _synthesize_mp3(text: str, voice: str) -> bytes:
    """The network call, run in a worker thread so the caller can give up on it without waiting."""
    communicate = edge_tts.Communicate(text, voice)
    chunks = [chunk["data"] for chunk in communicate.stream_sync() if chunk["type"] == "audio" and chunk.get("data")]
    if not chunks:
        raise TtsError("El servicio de voz no devolvió audio. Probá con otro texto.")
    return b"".join(chunks)


def _convert_to_ogg_opus(mp3_data: bytes, timeout_seconds: float) -> bytes:
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
           "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1", "-f", "ogg", "pipe:1"]
    try:
        completed = subprocess.run(
            argv, input=mp3_data, capture_output=True, timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        raise TtsError("La conversión del audio tardó demasiado.") from None
    except FileNotFoundError:
        raise TtsError("No encontré ffmpeg en esta máquina (hace falta para convertir el audio).") from None
    except OSError as error:
        raise TtsError(f"No pude convertir el audio: {error}") from None
    if completed.returncode != 0 or not completed.stdout:
        logger.warning("ffmpeg failed converting TTS audio (exit %s): %s", completed.returncode, completed.stderr[-300:])
        raise TtsError("No pude convertir el audio generado.")
    return completed.stdout


def synthesize(text: str, voice_key: str, timeout_seconds: float) -> bytes:
    """Renders ``text`` as Ogg/Opus bytes with the given voice. ``voice_key`` must be a key of VOICES.

    Raises ValueError (empty text), TextTooLong, or TtsError (network/service/conversion failure).
    """
    text = text.strip()
    if not text:
        raise ValueError("El texto no puede estar vacío.")
    if len(text) > MAX_CHARS:
        raise TextTooLong(f"El texto es demasiado largo (máx. {MAX_CHARS} caracteres).")
    voice = VOICES[voice_key]

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_synthesize_mp3, text, voice)
    try:
        mp3_data = future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        raise TtsError("El servicio de voz no respondió a tiempo. Probá de nuevo en un rato.") from None
    except TtsError:
        raise
    except Exception as error:
        logger.warning("edge-tts failed: %s", error)
        raise TtsError("No pude generar el audio. Probá de nuevo en un rato.") from error
    finally:
        # Don't block here if the worker is still running (it timed out): let it finish or die on its own.
        pool.shutdown(wait=False)

    return _convert_to_ogg_opus(mp3_data, timeout_seconds)
