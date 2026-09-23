from tests.test_mention import raw


def add_audio(directory, name="risa.ogg", size=10):
    (directory / name).write_bytes(b"x" * size)


def test_risa_sends_an_audio(client, risa_audios_dir):
    add_audio(risa_audios_dir)
    (reply,) = raw(client, "!risa")
    assert reply["audio"] == "risa.ogg"


def test_risa_says_so_when_there_are_no_audios(client):
    (reply,) = raw(client, "!risa")
    assert reply["audio"] is None and "No tengo" in reply["text"]


def test_risa_does_not_share_m_s_no_repeat_history(client, risa_audios_dir, audios_dir):
    """Both commands track "last audio sent" independently: sending one must not affect the other."""
    add_audio(risa_audios_dir, "unica.ogg")
    add_audio(audios_dir, "unica.ogg")  # same filename, different folder: must not collide either
    assert raw(client, "!risa")[0]["audio"] == "unica.ogg"
    assert raw(client, "!risa")[0]["audio"] == "unica.ogg"  # only one clip: repeats, unaffected by !m


def test_default_risa_audio_folder_is_media_risa():
    from app.config import Settings

    assert Settings(_env_file=None, API_KEY="k").RISA_AUDIOS_DIR == "media/risa"
