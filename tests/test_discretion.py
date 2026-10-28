"""Whether Tox keeps a remark to itself: the objections decided before Claude is ever called."""

from datetime import datetime, timedelta

import pytest

from app.services import discretion
from app.services.chat_history import Saved
from app.utils import utcnow

CHAT = "whatsapp:g1"
BOT = "tox-bot"


def said(sender: str, text: str = "algo") -> Saved:
    return Saved(sent_at=utcnow(), sender_id=sender, sender_name=sender, text=text)


def moment(**changes) -> discretion.Moment:
    base = dict(chat_key=CHAT, now_utc=datetime(2026, 10, 27, 18, 0), timezone_name="America/Montevideo",
                start_hour=9, end_hour=1, patience=2, share_limit=2.0, bot_id=BOT,
                recent=[said("a"), said("b"), said("c")], game_open=False)
    return discretion.Moment(**{**base, **changes})


@pytest.fixture(autouse=True)
def fresh_standing():
    discretion.reset()
    yield
    discretion.reset()


def test_nothing_stops_it_in_an_ordinary_chat():
    assert discretion.objection(moment()) is None


def test_every_objection_answers_in_words_or_says_nothing():
    """The objection that fires is what gets logged, so one that fires has to be readable."""
    for rule in discretion.OBJECTIONS:
        reason = rule(moment())
        assert reason is None or (isinstance(reason, str) and reason.strip()), rule.__name__


# --- the hours ---

def test_the_window_wraps_midnight():
    # 9 to 1 in Montevideo (UTC-3 all year): awake from 09:00 to 00:59, asleep from 01:00 to 08:59
    for local_hour, awake in ((9, True), (12, True), (22, True), (23, True), (0, True),
                              (1, False), (3, False), (8, False)):
        now = datetime(2026, 10, 27, local_hour, 0) + timedelta(hours=3)  # local -> naive UTC
        assert discretion.within_hours(now, "America/Montevideo", 9, 1) is awake, local_hour


def test_a_window_of_the_whole_day_never_stops_it():
    assert discretion.within_hours(datetime(2026, 10, 27, 7, 0), "America/Montevideo", 9, 9) is True


def test_outside_the_hours_it_says_nothing():
    four_in_the_morning = datetime(2026, 10, 27, 7, 0)  # 04:00 in Montevideo
    assert discretion.objection(moment(now_utc=four_in_the_morning)) == "it is outside the hours it talks in"


# --- being ignored ---

def test_a_remark_nobody_picks_up_counts_and_two_of_them_stop_it():
    assert discretion.unanswered(CHAT) == 0
    discretion.spoke_up(CHAT, utcnow())
    assert discretion.unanswered(CHAT) == 1  # pending: nobody has answered it yet
    assert discretion.objection(moment()) is None  # one is not two

    discretion.spoke_up(CHAT, utcnow())
    assert discretion.unanswered(CHAT) == 2
    assert discretion.objection(moment()) == "its last 2 remarks went unanswered"


def test_somebody_talking_to_it_ends_the_streak():
    discretion.spoke_up(CHAT, utcnow())
    discretion.spoke_up(CHAT, utcnow())
    discretion.was_answered(CHAT)
    assert discretion.unanswered(CHAT) == 0
    assert discretion.objection(moment()) is None


def test_giving_up_can_be_turned_off():
    for _ in range(5):
        discretion.spoke_up(CHAT, utcnow())
    assert discretion.objection(moment(patience=0)) is None


def test_what_counts_as_being_called_by_name():
    assert discretion.names_the_bot("tox, qué opinás?") and discretion.names_the_bot("TOX decime") and discretion.names_the_bot("che toxi!")
    assert not discretion.names_the_bot("qué tóxico ese partido") and not discretion.names_the_bot("hola a todos")


# --- how much it talks ---

def test_it_keeps_quiet_once_it_has_talked_more_than_the_people_there():
    # two people with one message each: the average is 1, so three of its own is over twice that
    busy = [said("a"), said("b"), said(BOT), said(BOT), said(BOT)]
    assert discretion.objection(moment(recent=busy)) == "it has already talked more than the people there"
    assert discretion.objection(moment(recent=busy[:4])) is None  # two of its own is exactly twice


def test_a_chat_where_nobody_said_anything_is_left_alone():
    assert discretion.objection(moment(recent=[])) is not None
    assert discretion.objection(moment(recent=[said(BOT)])) is not None


# --- serious talk and games ---

def test_it_stays_out_of_serious_talk():
    for text in ("falleció el abuelo", "está internada en CTI", "tuvimos un accidente", "me operaron ayer"):
        reason = discretion.objection(moment(recent=[said("a"), said("b"), said("c", text)]))
        assert reason is not None and reason.startswith("the chat is talking about something serious"), text


def test_accents_and_case_do_not_hide_a_serious_word():
    assert discretion.heavy_topic([said("a", "FALLECIÓ ayer")]) == "falleci"


def test_it_stays_out_of_an_open_game():
    assert discretion.objection(moment(game_open=True)) == "a game is open"


# --- the router tells it when somebody talked to the bot ---

def test_a_command_ends_the_streak(send):
    discretion.spoke_up("whatsapp:g1", utcnow())
    discretion.spoke_up("whatsapp:g1", utcnow())
    send("!uuid")
    assert discretion.unanswered("whatsapp:g1") == 0


def test_naming_it_in_ordinary_chatter_ends_the_streak_too(send):
    discretion.spoke_up("whatsapp:g1", utcnow())
    send("tox sos un capo")
    assert discretion.unanswered("whatsapp:g1") == 0


def test_ordinary_chatter_that_is_not_for_it_does_not(send):
    discretion.spoke_up("whatsapp:g1", utcnow())
    send("alguien vio el partido?")
    assert discretion.unanswered("whatsapp:g1") == 1
