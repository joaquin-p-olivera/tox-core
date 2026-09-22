import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import AlertState, PendingAlert
from app.services.alert_monitor import AlertMonitor

PY = sys.executable


def demo_service(status: str):
    """A fake local service whose `status` action always prints a fixed word and exits accordingly."""
    exit_code = 0 if status == "active" else 3
    return {"demo": {
        "description": "Servicio de prueba",
        "status": [PY, "-c", f"print({status!r}); import sys; sys.exit({exit_code})"],
    }}


def write_services(settings, config):
    Path(settings.SERVICES_FILE).write_text(json.dumps(config))


@pytest.fixture
def factory(db_session):
    class Shared:
        def __enter__(self):
            return db_session

        def __exit__(self, *exc):
            return False

    return Shared


def make(factory, settings, **overrides):
    settings = settings.model_copy(update={"ALERT_SERVICES": "demo", **overrides})
    return AlertMonitor(factory, settings)


def test_admins_of_that_platform_are_tagged(factory, settings, db_session):
    # the shared `settings` fixture already sets ADMIN_USER_IDS="whatsapp:admin1@lid, telegram:99"
    write_services(settings, demo_service("inactive"))
    make(factory, settings, ALERT_WHATSAPP_GROUP_JIDS="g@g.us").check_once()

    alert = db_session.scalars(select(PendingAlert)).one()
    assert alert.mentions == "admin1@lid"
    assert alert.text.endswith("{@0}")


def test_a_service_going_down_alerts_exactly_once(factory, settings, db_session):
    write_services(settings, demo_service("inactive"))
    monitor = make(factory, settings, ALERT_TELEGRAM_CHAT_IDS="-100")

    monitor.check_once()
    monitor.check_once()  # still down: no second alert

    alerts = list(db_session.scalars(select(PendingAlert)))
    assert len(alerts) == 1
    assert alerts[0].platform == "telegram" and alerts[0].chat_id == "-100"
    assert "caído" in alerts[0].text and "demo" in alerts[0].text


def test_recovery_is_announced_after_a_previous_failure(factory, settings, db_session):
    write_services(settings, demo_service("inactive"))
    monitor = make(factory, settings, ALERT_TELEGRAM_CHAT_IDS="-100")
    monitor.check_once()  # goes down, queues an alert

    write_services(settings, demo_service("active"))
    monitor.check_once()

    texts = [a.text for a in db_session.scalars(select(PendingAlert)).all()]
    assert any("se recuperó" in t for t in texts)


def test_a_service_already_healthy_on_the_first_check_stays_silent(factory, settings, db_session):
    write_services(settings, demo_service("active"))
    make(factory, settings, ALERT_TELEGRAM_CHAT_IDS="-100").check_once()

    assert list(db_session.scalars(select(PendingAlert))) == []
    assert db_session.get(AlertState, "local:demo").ok is True


def test_no_services_configured_does_nothing(factory, settings, db_session):
    AlertMonitor(factory, settings.model_copy(update={"ALERT_SERVICES": ""})).check_once()
    assert list(db_session.scalars(select(PendingAlert))) == []


def test_a_down_alert_includes_the_extra_detail_when_the_service_defines_info(factory, settings, db_session):
    info = "ActiveState=inactive\nInactiveEnterTimestamp=Mon 2020-01-01 00:00:00 -03\nResult=success\n"
    write_services(settings, {"demo": {
        "description": "Servicio de prueba",
        "status": [PY, "-c", "print('inactive'); import sys; sys.exit(3)"],
        "info": ["echo", info],
    }})
    make(factory, settings, ALERT_TELEGRAM_CHAT_IDS="-100").check_once()

    text = db_session.scalars(select(PendingAlert)).one().text
    assert "caído" in text and "Detenido desde" in text
