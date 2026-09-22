import hashlib

import pytest


# ---- hash ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("algorithm", ["md5", "sha1", "sha256", "sha512", "SHA-256"])
def test_hash_matches_hashlib(send, algorithm):
    expected = hashlib.new(algorithm.lower().replace("-", ""), "hola mundo".encode()).hexdigest()
    assert send(f"!hash {algorithm} hola mundo") == [f"{algorithm.lower().replace('-', '')}: {expected}"]


def test_hash_known_vector(send):
    assert send("!hash md5 abc") == ["md5: 900150983cd24fb0d6963f7d28e17f72"]  # RFC 1321 test vector


def test_hash_usage(send):
    assert send("!hash")[0].startswith("Uso:")
    assert send("!hash sha256")[0].startswith("Uso:")
    assert send("!hash crc32 hola")[0].startswith("Uso:")


# ---- url ----------------------------------------------------------------------------------------

def test_url_roundtrip(send):
    assert send("!url codificar hola mundo & ñandú/?") == ["hola%20mundo%20%26%20%C3%B1and%C3%BA%2F%3F"]
    assert send("!url decodificar hola%20mundo%20%26%20%C3%B1and%C3%BA%2F%3F") == ["hola mundo & ñandú/?"]
    assert send("!url mezclar hola")[0].startswith("Uso:") and send("!url")[0].startswith("Uso:")


def test_removed_tools_are_gone(send):
    for text in ("!epoch", "!epoch 0", "!timestamp", "!color #ff8800", "!chmod 755", "!puerto 443", "!port 22",
                "!json {}", "!base 255", "!http 404"):
        assert send(text) == [], text


def test_ayuda_lists_the_new_tools(send):
    (text,) = send("!ayuda")
    for command in ("!hash", "!url"):
        assert command in text
    assert "!ping" not in text and "!moneda" not in text
    for removed in ("!epoch", "!color", "!chmod", "!puerto", "!json", "!base ", "!base\n", "!http"):
        assert removed not in text
