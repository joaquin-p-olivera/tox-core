from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import CountdownState, PendingAlert
from app.services import gta_card, gta_countdown
from tests.test_mention import raw

GROUPS = "1@g.us,2@g.us"
BEFORE_RELEASE = datetime(2026, 10, 26, 21, 30, 0)  # 18:30 in Montevideo, 23 days to go
MIDNIGHT = datetime(2026, 10, 27, 3, 0, 0)  # 00:00 in Montevideo
SIX_HOURLY_DAY = datetime(2026, 11, 16, 15, 0, 0)  # 12:00 of the 16th, one of the slots every six hours
HOURLY_DAY = datetime(2026, 11, 18, 15, 0, 0)  # 12:00 of the 18th, the day it goes out every hour
# Spelled here and not read from the source, so changing the welcome has to be meant in both places
WELCOME = "🌴 Llegó el gran día\nBienvenidos a Vice City 🌴"


@pytest.fixture
def alerting(settings):
    """The same groups the alerts go to: that is where the countdown is meant to land."""
    object.__setattr__(settings, "ALERT_WHATSAPP_GROUP_JIDS", GROUPS)
    return settings


def queued(db):
    return [(row.chat_id, row.text) for row in db.scalars(select(PendingAlert).order_by(PendingAlert.id))]


def test_the_message_counts_months_days_hours_minutes_and_seconds():
    assert gta_countdown.message(BEFORE_RELEASE) == "🌴 Falta 00:23:05:30:00 para Grand Theft Auto VI 🦩"
    # exactly one calendar month before, not 30 days
    assert gta_countdown.remaining(datetime(2026, 10, 19, 3, 0, 0)) == (1, 0, 0, 0, 0)
    assert gta_countdown.remaining(datetime(2026, 11, 18, 23, 59, 59)) == (0, 0, 3, 0, 1)


def test_the_one_a_day_message_counts_days_and_nothing_else():
    """Nobody asked for this one, so it carries the only number that means anything from a day's distance."""
    assert gta_countdown.day_message(BEFORE_RELEASE) == "🌴 23 días para Grand Theft Auto VI 🦩"
    assert gta_countdown.day_message(datetime(2026, 11, 14, 3, 0, 0)) == "🌴 5 días para Grand Theft Auto VI 🦩"
    # the singular and the last day cannot come up while the hourly stretch starts where it does, but the line
    # is written for any number of days and says so
    assert gta_countdown.day_message(datetime(2026, 11, 17, 12, 0, 0)) == "🌴 1 día para Grand Theft Auto VI 🦩"
    assert gta_countdown.day_message(datetime(2026, 11, 18, 12, 0, 0)) == "🌴 Último día para Grand Theft Auto VI 🦩"
    assert gta_countdown.day_message(datetime(2026, 11, 19, 3, 0, 0)) == WELCOME


def test_going_out_more_than_once_a_day_brings_the_whole_clock_back(db_session, alerting):
    """One table governs both: the day the messages stop being one a day is the day the card counts hours again."""
    gta_countdown.queue_due(db_session, alerting, BEFORE_RELEASE)
    gta_countdown.queue_due(db_session, alerting, MIDNIGHT)
    assert queued(db_session)[-1][1] == "🌴 23 días para Grand Theft Auto VI 🦩"

    gta_countdown.queue_due(db_session, alerting, SIX_HOURLY_DAY)
    assert queued(db_session)[-1][1] == "🌴 Falta 00:02:12:00:00 para Grand Theft Auto VI 🦩"

    gta_countdown.queue_due(db_session, alerting, HOURLY_DAY)
    assert queued(db_session)[-1][1] == "🌴 Falta 00:00:12:00:00 para Grand Theft Auto VI 🦩"


def test_which_card_is_drawn_follows_the_same_line(settings, monkeypatch):
    """The same artwork, two different bars: which one is drawn is the whole of this change."""
    drawn = []
    # The artwork goes in as the last positional argument in both, and is not what this is about
    monkeypatch.setattr(gta_card, "render_days", lambda days, art=None: drawn.append(("days", days)) or b"png")
    monkeypatch.setattr(gta_card, "render", lambda *args: drawn.append(("full", args[:4])) or b"png")

    gta_countdown.card(settings, BEFORE_RELEASE, by_day=True)
    gta_countdown.card(settings, BEFORE_RELEASE)  # what `!vi` asks for: the default, whatever day it is
    gta_countdown.card(settings, SIX_HOURLY_DAY, by_day=False)
    assert drawn == [("days", 23), ("full", (23, 5, 30, 0)), ("full", (2, 12, 0, 0))]


def test_the_days_the_cadence_changes_are_the_days_it_was_asked_to(settings):
    """Read in Montevideo, where 00:00 is 03:00 UTC: one a day, then every six hours, then every hour."""
    every = lambda when: gta_countdown.every(when, settings.TIMEZONE)
    assert every(datetime(2026, 11, 15, 2, 0, 0)) == 24  # 23:00 of the 14th
    assert every(datetime(2026, 11, 15, 3, 0, 0)) == 6   # 00:00 of the 15th
    assert every(datetime(2026, 11, 18, 2, 0, 0)) == 6   # 23:00 of the 17th
    assert every(datetime(2026, 11, 18, 3, 0, 0)) == 1   # 00:00 of the 18th


def test_once_the_day_comes_it_welcomes_everybody_to_vice_city():
    assert gta_countdown.message(datetime(2026, 11, 19, 2, 59, 59)) == "🌴 Falta 00:00:00:00:01 para Grand Theft Auto VI 🦩"
    assert gta_countdown.message(datetime(2026, 11, 19, 3, 0, 0)) == WELCOME
    assert gta_countdown.message(datetime(2027, 5, 1, 0, 0, 0)) == WELCOME


def test_the_first_poll_only_writes_down_where_the_schedule_stands(db_session, alerting):
    # Nothing is sent the moment the API starts: with no state there is no missed slot to tell apart from this one
    assert gta_countdown.queue_due(db_session, alerting, BEFORE_RELEASE) == 0
    assert queued(db_session) == []
    assert db_session.get(CountdownState, "gta6").last_slot == datetime(2026, 10, 26, 3, 0, 0)


def test_one_message_a_day_to_every_alert_group_however_often_it_is_polled(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, BEFORE_RELEASE)  # the first poll, which sends nothing

    assert gta_countdown.queue_due(db_session, alerting, MIDNIGHT) == 2  # the two groups
    assert queued(db_session) == [("1@g.us", "🌴 23 días para Grand Theft Auto VI 🦩"),
                                  ("2@g.us", "🌴 23 días para Grand Theft Auto VI 🦩")]

    # polled again and again inside the same day: the slot is already sent
    assert gta_countdown.queue_due(db_session, alerting, MIDNIGHT) == 0
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 10, 27, 20, 0, 0)) == 0
    assert len(queued(db_session)) == 2

    # the next midnight, one more
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 10, 28, 3, 0, 0)) == 2
    assert len(queued(db_session)) == 4


def test_a_day_the_api_spent_down_is_one_missed_message_not_a_burst(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, BEFORE_RELEASE)
    gta_countdown.queue_due(db_session, alerting, MIDNIGHT)
    # back three days later: the slots in between are gone, the current one is sent late
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 10, 30, 14, 25, 0)) == 2
    assert queued(db_session)[-1][1] == "🌴 19 días para Grand Theft Auto VI 🦩"


def test_over_the_last_four_days_it_goes_out_every_six_hours(db_session, alerting):
    """00:00, 06:00, 12:00 and 18:00 in Montevideo, and nothing in between."""
    gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 3, 0, 0))  # the first poll of the window

    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 5, 0, 0)) == 0   # 02:00, same slot
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 9, 0, 0)) == 2   # 06:00
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 12, 30, 0)) == 0  # 09:30, same slot
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 15, 0, 0)) == 2  # 12:00
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 21, 0, 0)) == 2  # 18:00
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 16, 3, 0, 0)) == 2   # 00:00 of the 16th
    assert len(queued(db_session)) == 8
    assert queued(db_session)[-1][1] == "🌴 Falta 00:03:00:00:00 para Grand Theft Auto VI 🦩"


def test_the_last_day_it_goes_out_every_hour(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 3, 0, 0))  # the first poll of the last day

    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 4, 0, 0)) == 2
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 4, 59, 0)) == 0  # still that hour
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 5, 0, 0)) == 2
    assert gta_countdown.queue_due(db_session, alerting, HOURLY_DAY) == 2
    assert len(queued(db_session)) == 6
    assert queued(db_session)[-1][1] == "🌴 Falta 00:00:12:00:00 para Grand Theft Auto VI 🦩"


def test_the_six_hourly_stretch_hands_over_to_the_hourly_one_at_midnight(db_session, alerting):
    """The 18th at 00:00 is a slot in both cadences, so the handover owes nobody a message and repeats none."""
    gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 17, 21, 0, 0))  # 18:00 of the 17th, the first
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 2, 0, 0)) == 0  # 23:00, nothing due

    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 3, 0, 0)) == 2  # 00:00 of the 18th
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 3, 30, 0)) == 0
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 18, 4, 0, 0)) == 2  # 01:00, by the hour
    assert len(queued(db_session)) == 4


def test_the_day_before_the_window_is_still_one_a_day(db_session, alerting):
    """Up to and including the 14th nothing changes: one message, at midnight, and the days alone on the card."""
    gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 14, 3, 0, 0))
    for hour in (4, 10, 20):  # 01:00, 07:00 and 17:00 in Montevideo, all of 14 November
        assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 14, hour, 0, 0)) == 0
    assert queued(db_session) == []


def test_the_welcome_is_sent_once_and_then_the_countdown_is_over_for_good(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, HOURLY_DAY)

    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 19, 3, 0, 0)) == 2
    assert queued(db_session) == [("1@g.us", WELCOME), ("2@g.us", WELCOME)]
    assert db_session.get(CountdownState, "gta6").finished is True

    # never again, whatever the next poll looks like
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 19, 4, 0, 0)) == 0
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 20, 3, 0, 0)) == 0
    assert len(queued(db_session)) == 2


def test_an_api_that_only_wakes_up_after_the_release_still_says_it(db_session, alerting):
    """No state at all and the day already gone by: the welcome is the one message that does not wait for a slot."""
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 20, 12, 0, 0)) == 2
    assert queued(db_session) == [("1@g.us", WELCOME), ("2@g.us", WELCOME)]
    assert db_session.get(CountdownState, "gta6").finished is True


def test_without_alert_groups_configured_nothing_is_queued(db_session, settings):
    assert settings.alert_whatsapp_group_jids == [] and settings.alert_telegram_chat_ids == []
    assert gta_countdown.queue_due(db_session, settings, MIDNIGHT) == 0
    assert queued(db_session) == [] and db_session.get(CountdownState, "gta6") is None


def test_the_countdown_rides_the_poll_the_bots_already_make(client, alerting, db_session):
    """No background thread: the bots' poll for pending alerts is what queues and delivers it."""
    db_session.add(CountdownState(key="gta6", last_slot=datetime(2026, 10, 1, 3, 0, 0)))
    db_session.commit()

    body = client.get("/api/v1/alerts/pending", params={"platform": "whatsapp"}, headers={"X-API-Key": "test-key"}).json()
    assert [row["chat_id"] for row in body] == ["1@g.us", "2@g.us"]
    assert "para Grand Theft Auto VI" in body[0]["text"]
    # fetch-and-delete, and the slot is already sent: the next poll brings nothing
    assert client.get("/api/v1/alerts/pending", params={"platform": "whatsapp"}, headers={"X-API-Key": "test-key"}).json() == []


def test_vi_answers_by_hand_with_the_full_clock(client):
    assert raw(client, "!vi")[0]["image"]  # the card, not a text message
    assert raw(client, "!gta6")[0]["image"]


def test_vi_works_in_the_personal_mode_too(client):
    """The personal bot never polls for alerts, so the command is the only way it can say anything about this."""
    response = client.post("/api/v1/messages", headers={"X-API-Key": "test-key"},
                           json={"platform": "whatsapp", "chat_id": "me", "user_id": "owner", "user_name": "Joaco",
                                 "text": ".b vi", "is_group": False, "personal": True})
    assert response.status_code == 200
    assert response.json()["replies"][0]["image"]
