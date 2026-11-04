"""The flags round of the trivia: "¿de qué país es esta bandera?".

These questions are not written down like the others (`trivia_questions.json`); they are made out of
`flags.json`, a country and its continent, one line each. The flag itself is not stored: a flag emoji *is* its
ISO code, written with the two regional indicator letters, so 🇺🇾 is what "UY" looks like on a phone and there is
no picture to ship, resize or lose.

The three wrong answers come from the same continent whenever there are enough of them, which is what makes this
hard: picking Uruguay out of Uruguay, Japan, Kenya and Norway is no question at all. They are drawn with the round
id as the seed, so the question a round shows is always the same question, the same way the answer order of a
written question is (see `trivia_service._build_question`): nothing but the round id has to be stored.
"""

import random

from .content import load_json

CATEGORY = "banderas"
PREFIX = "flag-"  # the question id of a flag round: "flag-UY"
FILE = "flags.json"
CHOICES = 4
TEXT = "¿De qué país es esta bandera?"

_FIRST_LETTER = 0x1F1E6  # REGIONAL INDICATOR SYMBOL LETTER A


def emoji(code: str) -> str:
    """"UY" -> 🇺🇾. The two letters of the code, each as its regional indicator; a phone draws the pair as a flag."""
    return "".join(chr(_FIRST_LETTER + ord(letter) - ord("A")) for letter in code.upper())


def countries() -> list[dict]:
    return load_json(FILE)


def question_id(code: str) -> str:
    return f"{PREFIX}{code.upper()}"


def is_flag(question_id: str) -> bool:
    return question_id.startswith(PREFIX)


def seeds() -> list[dict]:
    """One per country, in the shape the trivia picks from: enough to choose and to remember, no more. The question
    itself is built once the round exists and has an id to draw the wrong answers with."""
    return [{"id": question_id(row["code"]), "category": CATEGORY} for row in countries()]


def question(question_id: str, round_id: int) -> dict:
    """The written-out question of a flag round, in the same shape as a question of `trivia_questions.json`."""
    code = question_id.removeprefix(PREFIX).upper()
    rows = countries()
    right = next((row for row in rows if row["code"] == code), None)
    if right is None:
        raise KeyError(question_id)

    dice = random.Random(f"{question_id}:{round_id}")
    nearby = [row for row in rows if row["continent"] == right["continent"] and row["code"] != code]
    far = [row for row in rows if row["continent"] != right["continent"]]
    wrong = dice.sample(nearby, CHOICES - 1) if len(nearby) >= CHOICES - 1 else \
        nearby + dice.sample(far, CHOICES - 1 - len(nearby))

    return {
        "id": question_id,
        "category": CATEGORY,
        "question": f"{TEXT}\n\n{emoji(code)}",
        "choices": [row["name"] for row in [right, *wrong]],
        "answer": 0,  # the order the chat sees is shuffled by the trivia, as it is for every question
    }
