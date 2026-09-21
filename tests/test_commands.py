import re


def test_ping_both_prefixes(send):
    assert send("!ping") == ["pong"]
    assert send("/ping") == ["pong"]


def test_non_command_and_unknown_command_are_silent(send):
    assert send("buen día") == []
    assert send("!nolaconozco") == []


def test_old_english_command_names_are_gone(send):
    for text in ("!joke", "!roll", "!flip", "!choose a | b", "!whoami"):
        assert send(text) == [], text


def test_chiste(send):
    for text in ("!chiste", "/chiste", "!CHISTE"):
        replies = send(text)
        assert len(replies) == 1 and replies[0]


def test_ayuda_lists_every_category_in_spanish(send):
    (text,) = send("!ayuda")
    for expected in ("Comandos disponibles", "!ping", "!chiste", "!trivia", "!dado", "!m",
                     "!uuid", "General", "Diversión", "Juegos", "Herramientas"):
        assert expected in text
    assert text.index("General") < text.index("Diversión") < text.index("Juegos") < text.index("Herramientas")


def test_help_alias_still_works(send):
    assert send("!help")[0].startswith("Comandos disponibles")


def test_ayuda_uses_the_prefix_the_user_typed(send):
    (text,) = send("/ayuda")
    assert "/ping" in text and "!ping" not in text


def test_ayuda_for_one_command(send):
    (text,) = send("!ayuda dado")
    assert "!dado" in text and "!dados" in text
    assert send("!ayuda nada") == ["No conozco ese comando. Probá !ayuda"]


def test_dado(send):
    assert re.fullmatch(r"🎲 [1-6] \(d6\)", send("!dado")[0])
    assert re.fullmatch(r"🎲 [1-9]\d* \(d20\)", send("!dado 20")[0])
    match = re.fullmatch(r"🎲 (\d+) \+ (\d+) = (\d+) \(2d6\)", send("!dados 2d6")[0])
    assert match and int(match[1]) + int(match[2]) == int(match[3])


def test_dado_rejects_bad_input(send):
    assert send("!dado banana")[0].startswith("Uso:")
    assert send("!dado 99d6")[0].startswith("Usá entre 1 y")
    assert send("!dado 1d1")[0].startswith("Usá entre 1 y")


def test_moneda(send):
    assert send("!moneda")[0] in ("🪙 Cara", "🪙 Cruz")


def test_elegir(send):
    assert send("!elegir pizza | sushi")[0] in ("🤔 pizza", "🤔 sushi")
    assert send("!elegir a, b, c")[0] in ("🤔 a", "🤔 b", "🤔 c")
    assert send("!elegir solo")[0].startswith("Dame al menos dos opciones")


def test_uuid(send):
    assert re.fullmatch(r"[0-9a-f-]{36}", send("!uuid")[0])


def test_base64_roundtrip(send):
    assert send("!base64 codificar hola mundo") == ["aG9sYSBtdW5kbw=="]
    assert send("!b64 decodificar aG9sYSBtdW5kbw==") == ["hola mundo"]


def test_base64_errors(send):
    assert send("!base64 decodificar ???")[0].startswith("Eso no es Base64")
    assert send("!base64 gritar hola")[0].startswith("Uso:")
    assert send("!base64")[0].startswith("Uso:")


def test_http_status_in_spanish(send):
    assert send("!http 404") == ["404 Not Found\nNo existe lo que buscás."]
    assert send("!http 418")[0].startswith("418 I'm a Teapot\nSoy una tetera")
    assert "(sin descripción en español)" in send("!http 226")[0]
    assert "no es un código de estado HTTP estándar" in send("!http 999")[0]
    assert send("!http")[0].startswith("Uso:")


def test_id_command(send):
    (text,) = send("!id", user="u9", chat="g9", name="Zed")
    assert "ID del chat: g9 (grupo)" in text and "ID de usuario: u9" in text and "Nombre: Zed" in text


def test_every_command_description_is_in_spanish(send):
    """Guards against English leaking into user-facing text: no leftover English words in !ayuda."""
    (text,) = send("!ayuda")
    for english in ("Available", "Fun", "Games", "Dev tools", "Tell a random", "Roll dice", "Flip a coin"):
        assert english not in text
