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


# ---- json ---------------------------------------------------------------------------------------

def test_json_formats(send):
    assert send('!json {"a":1,"b":[true,null],"c":"ñandú"}') == [
        '✅ JSON válido\n{\n  "a": 1,\n  "b": [\n    true,\n    null\n  ],\n  "c": "ñandú"\n}']


def test_json_accepts_curly_quotes_from_phones(send):
    assert send("!json {“a”: 1}")[0].startswith("✅ JSON válido")


def test_json_reports_where_it_breaks(send):
    assert send("!json {'a': 1}")[0] == "❌ JSON inválido: Expecting property name enclosed in double quotes (línea 1, columna 2)"
    assert send('!json {"a": }')[0].startswith("❌ JSON inválido: Expecting value")
    assert send("!json")[0].startswith("Uso:")


def test_json_survives_absurd_nesting(send):
    assert send("!json " + "[" * 3000)[0].startswith("❌ JSON")


def test_json_output_is_truncated(send):
    (reply,) = send("!json [" + ",".join(["1"] * 1500) + "]")
    assert reply.endswith("…") and len(reply) < 1100


# ---- url ----------------------------------------------------------------------------------------

def test_url_roundtrip(send):
    assert send("!url codificar hola mundo & ñandú/?") == ["hola%20mundo%20%26%20%C3%B1and%C3%BA%2F%3F"]
    assert send("!url decodificar hola%20mundo%20%26%20%C3%B1and%C3%BA%2F%3F") == ["hola mundo & ñandú/?"]
    assert send("!url mezclar hola")[0].startswith("Uso:") and send("!url")[0].startswith("Uso:")


# ---- base ---------------------------------------------------------------------------------------

def test_base_conversions(send):
    for text in ("255", "0xff", "0b11111111", "0o377", "0xFF", "255"):
        assert send(f"!base {text}") == ["🔢 255\n• Hex: 0xff\n• Binario: 0b11111111\n• Octal: 0o377"]
    assert send("!base 007")[0].startswith("🔢 7\n")  # leading zeros are plain decimal
    assert send("!base -10") == ["🔢 -10\n• Hex: -0xa\n• Binario: -0b1010\n• Octal: -0o12"]
    assert send("!base 1_000")[0].startswith("🔢 1000")


def test_base_rejects_bad_input(send):
    assert send("!base")[0].startswith("Uso:")
    assert send("!base hola")[0].startswith("Uso:")
    assert send("!base 0xzz")[0].startswith("Uso:")
    assert send("!base " + "9" * 41)[0].startswith("Uso:")


def test_removed_tools_are_gone(send):
    for text in ("!epoch", "!epoch 0", "!timestamp", "!color #ff8800", "!chmod 755", "!puerto 443", "!port 22"):
        assert send(text) == [], text


def test_ayuda_lists_the_new_tools(send):
    (text,) = send("!ayuda")
    for command in ("!hash", "!json", "!url", "!base"):
        assert command in text
    assert "!ping" not in text and "!moneda" not in text
    for removed in ("!epoch", "!color", "!chmod", "!puerto"):
        assert removed not in text
