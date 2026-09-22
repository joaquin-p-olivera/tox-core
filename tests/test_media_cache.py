import time

import pytest

from app.services import media_cache


@pytest.fixture(autouse=True)
def clean_cache():
    media_cache.clear()
    yield
    media_cache.clear()


def test_store_and_get_roundtrip():
    key = media_cache.store(b"hello", "text/plain", ttl_seconds=60)
    assert media_cache.get(key) == (b"hello", "text/plain")


def test_get_unknown_key_returns_none():
    assert media_cache.get("does-not-exist") is None


def test_each_store_gets_a_different_id():
    a = media_cache.store(b"1", "text/plain", 60)
    b = media_cache.store(b"2", "text/plain", 60)
    assert a != b
    assert media_cache.get(a) == (b"1", "text/plain")
    assert media_cache.get(b) == (b"2", "text/plain")


def test_entries_expire_after_their_ttl(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(media_cache.time, "monotonic", lambda: fake_now[0])

    key = media_cache.store(b"data", "text/plain", ttl_seconds=10)
    assert media_cache.get(key) == (b"data", "text/plain")

    fake_now[0] += 10.001  # just past expiry
    assert media_cache.get(key) is None


def test_get_prunes_expired_entries_from_the_store(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(media_cache.time, "monotonic", lambda: fake_now[0])

    key = media_cache.store(b"data", "text/plain", ttl_seconds=5)
    fake_now[0] += 10
    media_cache.get("anything")  # any call prunes, even a miss
    assert key not in media_cache._store


def test_store_also_prunes_expired_entries(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(media_cache.time, "monotonic", lambda: fake_now[0])

    old_key = media_cache.store(b"old", "text/plain", ttl_seconds=1)
    fake_now[0] += 5
    media_cache.store(b"new", "text/plain", ttl_seconds=60)
    assert old_key not in media_cache._store


def test_clear_drops_everything():
    media_cache.store(b"x", "text/plain", 60)
    media_cache.clear()
    assert media_cache._store == {}


def test_real_time_smoke():
    """One check against the real clock (not monkeypatched), so a wiring mistake in `time.monotonic`
    usage would still be caught even if the fake-clock tests above had a bug."""
    key = media_cache.store(b"abc", "audio/ogg", ttl_seconds=0.2)
    assert media_cache.get(key) == (b"abc", "audio/ogg")
    time.sleep(0.3)
    assert media_cache.get(key) is None
