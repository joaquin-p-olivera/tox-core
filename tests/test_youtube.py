"""!yt / !ytm. Nothing here reaches YouTube: what is worth testing is the picking and the refusing, which both
happen over the metadata, and that the bytes are handed over without anything being left on disk."""

import pytest

from app.config import Settings
from app.services import media_cache, youtube

# A video and an audio stream the way yt-dlp lists them, trimmed to the keys the picking reads.
def video_format(format_id: str, height: int, size: int, vcodec: str = "avc1.4d401e") -> dict:
    return {"format_id": format_id, "ext": "mp4", "height": height, "vcodec": vcodec,
            "acodec": "none", "filesize": size}


def audio_format(format_id: str, kbps: int, size: int, ext: str = "m4a", acodec: str = "mp4a.40.2") -> dict:
    return {"format_id": format_id, "ext": ext, "vcodec": "none", "acodec": acodec,
            "abr": kbps, "filesize": size}


FORMATS = [
    audio_format("139", 49, 3_200_000),
    audio_format("140", 129, 8_600_000),
    audio_format("251", 130, 8_610_000, ext="webm", acodec="opus"),
    video_format("133", 240, 10_000_000),
    video_format("134", 360, 19_200_000),
    video_format("135", 480, 33_600_000),
    video_format("244", 480, 25_700_000, vcodec="vp9"),  # would have to be re-encoded: never picked
]


def test_link_is_found_in_every_shape_youtube_uses():
    for text in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                 "mirá esto https://youtu.be/dQw4w9WgXcQ es un clásico",
                 "https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=30s",
                 "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
                 "https://www.youtube.com/shorts/dQw4w9WgXcQ"):
        assert youtube.link_in(text) is not None, text
    for text in ("https://vimeo.com/123456789", "youtube", "https://www.youtube.com/watch?v=corto", ""):
        assert youtube.link_in(text) is None, text


def test_audio_is_the_best_aac_that_fits():
    assert youtube.pick_audio(FORMATS, 557, 16_000_000)["format_id"] == "140"  # 129 kbps, not the webm opus
    assert youtube.pick_audio(FORMATS, 557, 4_000_000)["format_id"] == "139"  # only the small one fits
    assert youtube.pick_audio(FORMATS, 557, 1_000_000) is None


def test_video_quality_is_stepped_down_until_it_fits():
    # 480p + audio is 37 MB, 360p is 22 MB: at 16 MB only 240p plus the small audio gets there
    video, sound = youtube.pick_video(FORMATS, 557, 16_000_000)
    assert video["format_id"] == "133" and sound["format_id"] == "139"
    # with room to spare, the best H.264 picture wins, and never the vp9 one of the same height
    video, sound = youtube.pick_video(FORMATS, 557, 60_000_000)
    assert video["format_id"] == "135" and sound["format_id"] == "139"
    assert youtube.pick_video(FORMATS, 557, 2_000_000) is None


def test_a_format_that_already_has_both_needs_no_audio():
    progressive = {"format_id": "18", "ext": "mp4", "height": 360, "vcodec": "avc1.42001E",
                   "acodec": "mp4a.40.2", "filesize": 12_000_000}
    video, sound = youtube.pick_video([*FORMATS, progressive], 557, 16_000_000)
    assert video["format_id"] == "18" and sound is None


def test_size_falls_back_to_the_bitrate_when_yt_dlp_gives_none():
    # 290 kbps over 557 s is about 20 MB: too much for 16, fine for 30
    guessed = {"format_id": "134", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "none", "tbr": 290}
    small = audio_format("139", 49, 3_200_000)
    assert youtube.pick_video([guessed, small], 557, 16_000_000) is None
    assert youtube.pick_video([guessed, small], 557, 30_000_000)[0]["format_id"] == "134"


@pytest.fixture(autouse=True)
def one_job_at_a_time():
    """jobs are a module-level dict: each test starts with it empty so `_job_replies` cannot read a leftover."""
    from app.services import jobs

    jobs.clear()
    yield
    jobs.clear()


@pytest.fixture
def no_download(monkeypatch, tmp_path):
    """yt-dlp replaced by the metadata of a 3-minute video, and a download that writes one small file."""
    def fake_metadata(url, timeout):
        return {"title": "Un video", "duration": 200, "formats": FORMATS}

    def fake_run(args, timeout):
        if "-J" in args:
            raise AssertionError("the metadata goes through _metadata")
        target = args[args.index("-o") + 1].replace("%(ext)s", "mp4")
        with open(target, "wb") as handle:
            handle.write(b"video bytes")

        class Done:
            returncode = 0
            stderr = ""
        return Done()

    monkeypatch.setattr(youtube, "_metadata", fake_metadata)
    monkeypatch.setattr(youtube, "_run", fake_run)
    return tmp_path


def test_fetch_hands_over_the_bytes_and_leaves_nothing_behind(no_download):
    settings = Settings(_env_file=None, API_KEY="k", YT_WORK_DIR=str(no_download))
    media = youtube.fetch("https://youtu.be/dQw4w9WgXcQ", settings, audio_only=False)
    assert media.data == b"video bytes" and media.mimetype == "video/mp4"
    assert media.title == "Un video" and media.quality == "240p"
    assert list(no_download.iterdir()) == [], "the temp folder is deleted before the reply is built"


def test_fetch_of_the_audio_asks_for_the_audio(no_download):
    settings = Settings(_env_file=None, API_KEY="k", YT_WORK_DIR=str(no_download))
    media = youtube.fetch("https://youtu.be/dQw4w9WgXcQ", settings, audio_only=True)
    assert media.mimetype == "audio/mp4" and media.quality == "129 kbps"


def test_a_video_over_the_limit_is_refused_before_anything_is_downloaded(monkeypatch):
    monkeypatch.setattr(youtube, "_metadata", lambda url, timeout: {"title": "Largo", "duration": 557, "formats": FORMATS})
    monkeypatch.setattr(youtube, "_run", lambda args, timeout: pytest.fail("nothing is downloaded"))
    settings = Settings(_env_file=None, API_KEY="k")
    with pytest.raises(youtube.YoutubeError, match="dura 9:17 y el máximo es 8:00"):
        youtube.fetch("https://youtu.be/dQw4w9WgXcQ", settings, audio_only=False)


def test_a_live_is_refused(monkeypatch):
    monkeypatch.setattr(youtube, "_metadata",
                        lambda url, timeout: {"title": "Vivo", "duration": 60, "is_live": True, "formats": FORMATS})
    with pytest.raises(youtube.YoutubeError, match="vivo"):
        youtube.fetch("https://youtu.be/dQw4w9WgXcQ", Settings(_env_file=None, API_KEY="k"), audio_only=False)


def test_a_link_that_is_not_youtube_never_runs_yt_dlp(monkeypatch):
    monkeypatch.setattr(youtube, "_run", lambda args, timeout: pytest.fail("yt-dlp must not run"))
    with pytest.raises(youtube.YoutubeError, match="no es de YouTube"):
        youtube.fetch("https://vimeo.com/1", Settings(_env_file=None, API_KEY="k"), audio_only=True)


# --- the commands ---

def test_yt_without_a_link_says_how_to_use_it(send):
    assert send("!yt")[0].startswith("Pasame un link de YouTube")
    assert send("!ytm hola")[0].startswith("Pasame un link de YouTube")


def test_yt_answers_with_a_job_and_sends_the_video(send, monkeypatch):
    monkeypatch.setattr(youtube, "fetch", lambda url, settings, *, audio_only: youtube.Media(
        data=b"mp4", mimetype="video/mp4", title="Un video", duration=200, quality="360p"))
    assert send("!yt https://youtu.be/dQw4w9WgXcQ") == ["⏳"]
    # The job is finished by the thread: what the chat gets is the video and then its title
    replies = _job_replies(send)
    assert replies[0]["reaction"] == "" and replies[1]["video"]
    assert replies[2]["text"] == "🎬 Un video (3:20 · 360p)"
    assert media_cache.get(replies[1]["video"])[1] == "video/mp4"


def test_ytm_sends_it_as_audio(send, monkeypatch):
    monkeypatch.setattr(youtube, "fetch", lambda url, settings, *, audio_only: youtube.Media(
        data=b"m4a", mimetype="audio/mp4", title="Una canción", duration=200, quality="129 kbps"))
    send("!ytm https://youtu.be/dQw4w9WgXcQ")
    replies = _job_replies(send)
    assert replies[1]["audio"] and replies[1]["video"] is None


def test_the_link_can_be_in_the_message_being_replied_to(send, monkeypatch):
    asked: list[str] = []

    def fake_fetch(url, settings, *, audio_only):
        asked.append(url)
        return youtube.Media(data=b"mp4", mimetype="video/mp4", title="t", duration=10, quality="360p")

    monkeypatch.setattr(youtube, "fetch", fake_fetch)
    send("!yt", quoted={"text": "miren esto https://youtu.be/dQw4w9WgXcQ", "is_bot": False})
    _job_replies(send)
    assert asked == ["https://youtu.be/dQw4w9WgXcQ"]


def test_what_youtube_refuses_is_what_the_chat_reads(send, monkeypatch):
    def fake_fetch(url, settings, *, audio_only):
        raise youtube.YoutubeError("Ese video es privado.")

    monkeypatch.setattr(youtube, "fetch", fake_fetch)
    send("!yt https://youtu.be/dQw4w9WgXcQ")
    replies = _job_replies(send)
    assert replies[0]["reaction"] == "⚠️" and replies[1]["text"] == "Ese video es privado."


def _job_replies(send) -> list[dict]:
    """Waits for the download thread and returns what the job holds, the way the bot fetches it."""
    import time

    from app.services import jobs

    job_id = list(jobs._jobs)[-1]
    for _ in range(200):
        done, replies = jobs.get(job_id)
        if done:
            return [r.model_dump() for r in replies]
        time.sleep(0.01)
    raise AssertionError("the job never finished")
