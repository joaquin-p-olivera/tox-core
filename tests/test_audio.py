import random
from datetime import datetime

import pytest

from tests.conftest import API_KEY
from tests.test_mention import raw

MEMBERS = [{"user_id": "u1", "user_name": "Alice"}, {"user_id": "u2", "user_name": "Bob"}]


def add_audio(directory, name="risa.m4a", size=10):
    (directory / name).write_bytes(b"x" * size)


@pytest.fixture
def audio_probability(settings):
    def _set(value):
        object.__setattr__(settings, "M_AUDIO_PROBABILITY", value)  # settings are shared with the app via the override
    return _set


def test_m_sends_text_when_there_are_no_audios(client):
    (reply,) = raw(client, "!m", participants=MEMBERS)
    assert reply["audio"] is None and reply["mentions"][0]["user_id"] == "u2"


def test_m_sends_an_audio_when_the_dice_say_so(client, audios_dir, audio_probability):
    add_audio(audios_dir, "risa.m4a")
    audio_probability(1.0)
    (reply,) = raw(client, "!m", participants=MEMBERS)
    assert reply == {"text": "", "mentions": [], "audio": "risa.m4a"}


def test_m_tags_someone_when_the_dice_say_so(client, audios_dir, audio_probability):
    add_audio(audios_dir, "risa.m4a")
    audio_probability(0.0)
    (reply,) = raw(client, "!m", participants=MEMBERS)
    assert reply["audio"] is None and reply["text"] == "y tu mamá donde está? {@0}"
    assert reply["mentions"][0]["user_id"] == "u2"  # never the sender (u1)


def test_m_mixes_both_outcomes_and_picks_among_audios(client, audios_dir, audio_probability):
    for name in ("a.m4a", "b.mp3", "c.ogg"):
        add_audio(audios_dir, name)
    audio_probability(0.5)
    random.seed(1234)
    replies = [raw(client, "!m", participants=MEMBERS)[0] for _ in range(80)]
    audios = {r["audio"] for r in replies if r["audio"]}
    assert audios == {"a.m4a", "b.mp3", "c.ogg"}
    assert any(r["audio"] is None and r["mentions"] for r in replies)
    assert all(r["mentions"] == [] for r in replies if r["audio"])  # audios tag nobody


def test_m_falls_back_to_an_audio_when_nobody_else_is_known(client, audios_dir, audio_probability):
    add_audio(audios_dir)
    audio_probability(0.0)  # would always tag someone... but there is nobody to tag
    (reply,) = raw(client, "!m", participants=[MEMBERS[0]])
    assert reply["audio"] == "risa.m4a"


def test_m_skips_audio_when_the_folder_is_missing(client, settings, tmp_path, audio_probability):
    object.__setattr__(settings, "AUDIOS_DIR", str(tmp_path / "does" / "not" / "exist"))
    audio_probability(1.0)  # even if the dice always say "audio"
    for _ in range(10):
        (reply,) = raw(client, "!m", participants=MEMBERS)
        assert reply["audio"] is None and reply["mentions"][0]["user_id"] == "u2"


def test_m_skips_audio_when_the_folder_is_empty(client, audio_probability):
    audio_probability(1.0)  # audios_dir exists but has no files
    for _ in range(10):
        (reply,) = raw(client, "!m", participants=MEMBERS)
        assert reply["audio"] is None and reply["mentions"][0]["user_id"] == "u2"


def test_default_audio_folder_is_media_m():
    from app.config import Settings

    assert Settings(_env_file=None, API_KEY="k").AUDIOS_DIR == "media/m"


def test_m_alone_and_no_audios_says_so(client):
    (reply,) = raw(client, "!m", participants=[MEMBERS[0]])
    assert reply["audio"] is None and "No tengo a nadie" in reply["text"]


def test_new_audios_are_picked_up_without_restarting(client, audios_dir, audio_probability):
    audio_probability(1.0)
    assert raw(client, "!m", participants=MEMBERS)[0]["audio"] is None
    add_audio(audios_dir, "nuevo.m4a")
    assert raw(client, "!m", participants=MEMBERS)[0]["audio"] == "nuevo.m4a"


def test_unsupported_hidden_and_oversized_files_are_ignored(client, audios_dir, audio_probability):
    add_audio(audios_dir, "notes.txt")
    add_audio(audios_dir, ".oculto.m4a")
    add_audio(audios_dir, "enorme.m4a", size=16 * 1024 * 1024 + 1)
    (audios_dir / "carpeta.m4a").mkdir()
    audio_probability(1.0)
    assert raw(client, "!m", participants=MEMBERS)[0]["audio"] is None


def get(client, name, key=API_KEY):
    headers = {"X-API-Key": key} if key else {}
    return client.get(f"/api/v1/audios/{name}", headers=headers)


@pytest.mark.parametrize("name, mimetype", [
    ("a.m4a", "audio/mp4"), ("b.mp3", "audio/mpeg"), ("c.ogg", "audio/ogg; codecs=opus"),
    ("d.OPUS", "audio/ogg; codecs=opus"), ("e.aac", "audio/aac")])
def test_audio_endpoint_serves_the_file_with_its_mimetype(client, audios_dir, name, mimetype):
    (audios_dir / name).write_bytes(b"AUDIO-BYTES")
    response = get(client, name)
    assert response.status_code == 200 and response.content == b"AUDIO-BYTES"
    assert response.headers["content-type"] == mimetype


def test_audio_endpoint_requires_the_api_key(client, audios_dir):
    add_audio(audios_dir)
    assert get(client, "risa.m4a", key=None).status_code == 401
    assert get(client, "risa.m4a", key="wrong").status_code == 401


def test_audio_endpoint_cannot_escape_the_folder(client, audios_dir, tmp_path):
    (tmp_path / "secreto.m4a").write_bytes(b"NOPE")  # next to the audios folder, not inside it
    for name in ("../secreto.m4a", "..%2Fsecreto.m4a", "%2e%2e/secreto.m4a", "/etc/passwd", "notes.txt"):
        assert get(client, name).status_code == 404, name
    assert get(client, "no-existe.m4a").status_code == 404



# ---- no immediate repeats -----------------------------------------------------------------------

def audio_names(client, n, **kwargs):
    kwargs.setdefault("participants", MEMBERS)
    return [raw(client, "!m", **kwargs)[0]["audio"] for _ in range(n)]


def test_never_repeats_the_same_audio_twice_in_a_row(client, audios_dir, audio_probability):
    for i in range(1, 7):
        add_audio(audios_dir, f"{i}.ogg")
    audio_probability(1.0)
    names = audio_names(client, 300)
    assert all(a != b for a, b in zip(names, names[1:])), "an audio was sent twice in a row"
    assert set(names) == {f"{i}.ogg" for i in range(1, 7)}  # excluding the last one doesn't starve any audio


def test_two_audios_alternate(client, audios_dir, audio_probability):
    add_audio(audios_dir, "a.ogg")
    add_audio(audios_dir, "b.ogg")
    audio_probability(1.0)
    names = audio_names(client, 10)
    assert names[0::2] == [names[0]] * 5 and names[1::2] == [names[1]] * 5 and names[0] != names[1]


def test_a_single_audio_can_only_repeat(client, audios_dir, audio_probability):
    add_audio(audios_dir, "unico.ogg")
    audio_probability(1.0)
    assert audio_names(client, 4) == ["unico.ogg"] * 4


def test_the_last_audio_is_remembered_per_chat(client, audios_dir, audio_probability):
    add_audio(audios_dir, "a.ogg")
    add_audio(audios_dir, "b.ogg")
    audio_probability(1.0)
    first_g1 = audio_names(client, 1, chat="g1")[0]
    # another chat has its own history: it may start with any audio, including first_g1
    seen_in_g2 = {audio_names(client, 1, chat=f"other-{i}")[0] for i in range(30)}
    assert seen_in_g2 == {"a.ogg", "b.ogg"}
    assert audio_names(client, 1, chat="g1")[0] != first_g1  # ...and g1 still avoids its own last one


def test_tagging_does_not_touch_the_history(client, audios_dir, settings, audio_probability):
    """A text answer (someone tagged) isn't an audio: the last audio sent stays the last one."""
    add_audio(audios_dir, "a.ogg")
    add_audio(audios_dir, "b.ogg")
    audio_probability(1.0)
    last = audio_names(client, 1)[0]
    audio_probability(0.0)
    assert raw(client, "!m", participants=MEMBERS)[0]["audio"] is None
    audio_probability(1.0)
    assert audio_names(client, 1)[0] != last


def test_a_last_audio_that_no_longer_exists_is_harmless(client, audios_dir, audio_probability):
    add_audio(audios_dir, "viejo.ogg")
    add_audio(audios_dir, "otro.ogg")
    audio_probability(1.0)
    audio_names(client, 1)
    for gone in ("viejo.ogg", "otro.ogg"):
        (audios_dir / gone).unlink()
    add_audio(audios_dir, "nuevo.ogg")
    assert audio_names(client, 3) == ["nuevo.ogg"] * 3


def test_history_is_stored_in_the_database(db_session, audios_dir):
    from app.models import LastAudio
    from app.services import audio_library, audio_picker

    add_audio(audios_dir, "a.ogg")
    add_audio(audios_dir, "b.ogg")
    audios = audio_library.list_audios(str(audios_dir))
    picked = audio_picker.pick_audio(db_session, "whatsapp:g1", audios)
    assert db_session.get(LastAudio, "whatsapp:g1").name == picked.name
    again = audio_picker.pick_audio(db_session, "whatsapp:g1", audios)
    assert again.name != picked.name and db_session.get(LastAudio, "whatsapp:g1").name == again.name
