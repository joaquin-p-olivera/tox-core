import json
import logging
from datetime import datetime

import pytest

from app.models import HostSample
from app.services import host_status
from tests.test_host_status import cpu_machine, healthy_machine

ADMIN = "admin1@lid"


@pytest.fixture
def machine(monkeypatch):
    """Replaces the real computer with a fake one, and skips the 1 s CPU sample."""
    fake = cpu_machine()
    real_read = host_status.read_snapshot
    monkeypatch.setattr(host_status, "Sources", lambda: fake)
    monkeypatch.setattr(host_status, "read_snapshot",
                        lambda now, sources=None, sample_seconds=1.0, ignored_units=frozenset(): real_read(now, sources, 0, ignored_units))
    return fake


def ask(send, text, user=ADMIN):
    return send(text, user=user)[0]


def add_history(db_session, minutes=60, start=80.0, per_minute=-0.2, status="Discharging"):
    now = datetime.now().timestamp()
    db_session.add_all(HostSample(taken_at=now - (minutes - i) * 60, cpu_percent=10 + (i == 5) * 60, memory_used_percent=50,
                                  temperature_c=45, battery_percent=start + i * per_minute, battery_status=status)
                       for i in range(minutes + 1))
    db_session.commit()


# ---- names and access ---------------------------------------------------------------------------

def test_host_and_master_are_the_same_report(send, machine):
    host, master = ask(send, "!service -L host"), ask(send, "!service -L master")
    assert host == master and host.startswith("🟢 host: todo en orden\n🖥️ Linux Mint 22.3")
    assert ask(send, "!service -L HOST") == host and ask(send, "/service -L Master") == host


def test_the_old_name_is_gone(send, machine):
    assert ask(send, "!service -L pc") == 'No tengo un servicio local llamado "pc".\nNo hay servicios locales configurados (falta services.json, mirá services.example.json).'


def test_only_admins_and_nothing_is_read_for_others(send, machine):
    for name in ("host", "master"):
        assert send(f"!service -L {name}", user="someone@lid") == ["Este comando es solo para administradores."]
    assert machine.ran == [], "a non-admin must not make the API touch the machine"


@pytest.mark.parametrize("text, name", [("!service -L host -up", "host"), ("!service -L master -down", "master"), ("!service -L host --up", "host")])
def test_it_can_only_be_read_not_started_or_stopped(send, machine, text, name):
    assert ask(send, text) == f"🖥️ {name} solo se puede consultar: no se levanta ni se apaga desde acá."
    assert machine.ran == []


def test_extra_words_are_rejected(send, machine):
    assert ask(send, "!service -L host extra").startswith("Uso:") and ask(send, "!service -L host master").startswith("Uso:")
    assert machine.ran == []


def test_the_names_cannot_be_taken_by_an_allowlist_entry(send, settings, machine):
    from pathlib import Path
    Path(settings.SERVICES_FILE).write_text(json.dumps({"host": {"start": ["true"], "status": ["true"]}, "master": {"status": ["true"]}}))
    from app.services import service_control
    catalog = service_control.load_catalog(settings.SERVICES_FILE)
    assert catalog.services == {} and all("nombre reservado" in p for p in catalog.problems) and len(catalog.problems) == 2
    assert ask(send, "!service -L host").startswith("🟢 host:"), "the built-in report is still what answers"


def test_the_report_never_touches_the_services_file(send, settings, machine):
    assert not __import__("os").path.exists(settings.SERVICES_FILE)
    assert ask(send, "!service -L host").startswith("🟢 host:")


# ---- headline -----------------------------------------------------------------------------------

def test_the_headline_counts_what_needs_a_look(send, machine):
    machine.commands[("systemctl", "--failed", "--no-legend", "--plain", "--no-pager")] = "a.service l f f A\n"
    machine.exists = lambda path: path == "/var/run/reboot-required"
    reply = ask(send, "!service -L host")
    assert reply.splitlines()[0] == "🟠 host: 2 para revisar"
    assert "🟠 Servicios con fallos: 1 (a.service)" in reply and "🟠 Hay un reinicio pendiente (por actualizaciones)" in reply


def test_ignored_units_come_from_the_settings(send, settings, machine):
    machine.commands[("systemctl", "--failed", "--no-legend", "--plain", "--no-pager")] = "casper-md5check.service l f f X\n"
    assert ask(send, "!service -L host").startswith("🟠 host: 1 para revisar")
    object.__setattr__(settings, "HOST_IGNORED_UNITS", "casper-md5check.service, otra.service")
    assert ask(send, "!service -L host").startswith("🟢 host: todo en orden")


# ---- history ------------------------------------------------------------------------------------

def test_without_history_it_says_so(send, machine):
    assert ask(send, "!service -L host").splitlines()[-1] == "📈 Historial: todavía sin datos (la API empieza a registrar al arrancar)"


def test_history_goes_under_the_battery_and_peaks_at_the_end(send, machine, db_session):
    add_history(db_session)   # 80 % -> 68 % in an hour, discharging: 12 %/h
    lines = ask(send, "!service -L host").splitlines()
    battery = next(i for i, l in enumerate(lines) if l.startswith("🔋 Batería:"))
    assert lines[battery + 1].startswith("🔋 salud 100 %")
    assert lines[battery + 2].startswith("⏳ Autonomía: ≈ ") and "(12 %/h)" in lines[battery + 2]
    assert lines[battery + 3].startswith("🔋 Historial (1 h): mín. 68 %") and "1 h con batería" in lines[battery + 3]
    assert lines[battery + 4].startswith("🧠 Más memoria:")
    assert lines[-1].startswith("📈 Picos (1 h de datos): CPU 70 % (") and "memoria 50 % usada" in lines[-1] and "45 °C" in lines[-1]


def test_the_history_only_shows_recent_samples_of_this_machine(send, machine, db_session):
    db_session.add(HostSample(taken_at=datetime.now().timestamp() - 3 * 86400, cpu_percent=99, battery_percent=5, battery_status="Discharging"))
    db_session.commit()
    assert ask(send, "!service -L host").splitlines()[-1].startswith("📈 Historial: todavía sin datos")


def test_the_call_is_logged(send, machine, caplog):
    with caplog.at_level(logging.INFO, logger="tox.services"):
        ask(send, "!service -L host")
    assert "LOCAL SERVICE by whatsapp:admin1@lid: host status -> 0 to review" in [r.getMessage() for r in caplog.records]
