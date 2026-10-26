from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import CountdownState, PendingAlert
from app.services import gta_countdown
from tests.test_mention import raw

GROUPS = "1@g.us,2@g.us"
BEFORE_RELEASE = datetime(2026, 10, 26, 21, 30, 0)  # 18:30 in Montevideo, 23 days to go
MIDNIGHT = datetime(2026, 10, 27, 3, 0, 0)  # 00:00 in Montevideo
HOURLY_DAY = datetime(2026, 11, 16, 15, 0, 0)  # 12:00 of one of the last four days


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


def test_once_the_day_comes_it_welcomes_everybody_to_vice_city():
    assert gta_countdown.message(datetime(2026, 11, 19, 2, 59, 59)) == "🌴 Falta 00:00:00:00:01 para Grand Theft Auto VI 🦩"
    assert gta_countdown.message(datetime(2026, 11, 19, 3, 0, 0)) == "🌴 Bienvenidos a VICE CITY. 🌴"
    assert gta_countdown.message(datetime(2027, 5, 1, 0, 0, 0)) == "🌴 Bienvenidos a VICE CITY. 🌴"


def test_the_first_poll_only_writes_down_where_the_schedule_stands(db_session, alerting):
    # Nothing is sent the moment the API starts: with no state there is no missed slot to tell apart from this one
    assert gta_countdown.queue_due(db_session, alerting, BEFORE_RELEASE) == 0
    assert queued(db_session) == []
    assert db_session.get(CountdownState, "gta6").last_slot == datetime(2026, 10, 26, 3, 0, 0)


def test_one_message_a_day_to_every_alert_group_however_often_it_is_polled(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, BEFORE_RELEASE)  # the first poll, which sends nothing

    assert gta_countdown.queue_due(db_session, alerting, MIDNIGHT) == 2  # the two groups
    assert queued(db_session) == [("1@g.us", "🌴 Falta 00:23:00:00:00 para Grand Theft Auto VI 🦩"),
                                  ("2@g.us", "🌴 Falta 00:23:00:00:00 para Grand Theft Auto VI 🦩")]

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
    assert queued(db_session)[-1][1] == "🌴 Falta 00:19:12:35:00 para Grand Theft Auto VI 🦩"


def test_over_the_last_four_days_it_goes_out_every_hour(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 3, 0, 0))  # the first poll of the window

    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 4, 0, 0)) == 2
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 4, 59, 0)) == 0  # still that hour
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 15, 5, 0, 0)) == 2
    assert gta_countdown.queue_due(db_session, alerting, HOURLY_DAY) == 2
    assert len(queued(db_session)) == 6


def test_the_day_before_the_window_is_still_one_a_day(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 14, 3, 0, 0))
    for hour in (4, 10, 20):  # 01:00, 07:00 and 17:00 in Montevideo, all of 14 November
        assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 14, hour, 0, 0)) == 0
    assert queued(db_session) == []


def test_the_welcome_is_sent_once_and_then_the_countdown_is_over_for_good(db_session, alerting):
    gta_countdown.queue_due(db_session, alerting, HOURLY_DAY)

    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 19, 3, 0, 0)) == 2
    assert queued(db_session) == [("1@g.us", "🌴 Bienvenidos a VICE CITY. 🌴"), ("2@g.us", "🌴 Bienvenidos a VICE CITY. 🌴")]
    assert db_session.get(CountdownState, "gta6").finished is True

    # never again, whatever the next poll looks like
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 19, 4, 0, 0)) == 0
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 20, 3, 0, 0)) == 0
    assert len(queued(db_session)) == 2


def test_an_api_that_only_wakes_up_after_the_release_still_says_it(db_session, alerting):
    """No state at all and the day already gone by: the welcome is the one message that does not wait for a slot."""
    assert gta_countdown.queue_due(db_session, alerting, datetime(2026, 11, 20, 12, 0, 0)) == 2
    assert queued(db_session) == [("1@g.us", "🌴 Bienvenidos a VICE CITY. 🌴"), ("2@g.us", "🌴 Bienvenidos a VICE CITY. 🌴")]
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


def test_vi_answers_the_same_message_by_hand(client):
    assert raw(client, "!vi")[0]["image"]  # the card, not a text message
    assert raw(client, "!gta6")[0]["image"]


def test_vi_works_in_the_personal_mode_too(client):
    """The personal bot never polls for alerts, so the command is the only way it can say anything about this."""
    response = client.post("/api/v1/messages", headers={"X-API-Key": "test-key"},
                           json={"platform": "whatsapp", "chat_id": "me", "user_id": "owner", "user_name": "Joaco",
                                 "text": ".b vi", "is_group": False, "personal": True})
    assert response.status_code == 200
    assert response.json()["replies"][0]["image"]
