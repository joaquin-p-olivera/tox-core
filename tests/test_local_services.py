import json
import logging
import os
import re
import sys
import time

import pytest

from app.services import service_control

ADMIN = "admin1@lid"
PY = sys.executable


@pytest.fixture
def services_file(settings):
    """Writes the per-test allowlist (the developer's real services.json is never used by tests)."""
    from pathlib import Path

    def write(config):
        Path(settings.SERVICES_FILE).write_text(json.dumps(config) if not isinstance(config, str) else config)

    return write


def ask(send, text, user=ADMIN, **kwargs):
    return send(text, user=user, **kwargs)[0]


def demo(marker=None):
    """A harmless service: start/stop touch or remove a marker file, status says which one it is."""
    return {"demo": {
        "description": "Servicio de prueba",
        "start": ["touch", str(marker)],
        "stop": ["rm", "-f", str(marker)],
        "status": [PY, "-c", f"import os,sys; print('active' if os.path.exists({str(marker)!r}) else 'inactive'); "
                             f"sys.exit(0 if os.path.exists({str(marker)!r}) else 3)"],
    }}


# ---- access -------------------------------------------------------------------------------------

def test_non_admins_can_never_run_anything(send, services_file, tmp_path):
    marker = tmp_path / "started"
    services_file(demo(marker))
    for text in ("!service -L demo -up", "!service -l demo -down", "!service --local demo -up", "!service -L demo", "!service -L"):
        assert send(text, user="someone@lid") == ["Este comando es solo para administradores."]
    assert not marker.exists(), "a non-admin must never get a program executed"


def test_admin_ids_are_per_platform(send, services_file, tmp_path):
    marker = tmp_path / "started"
    services_file(demo(marker))
    # "99" is an admin on Telegram only
    assert ask(send, "/service -L demo -up", user="99", platform="telegram").startswith("✅")
    assert send("!service -L demo -down", user="99", platform="whatsapp") == ["Este comando es solo para administradores."]


@pytest.mark.parametrize("flag", ["-L", "-l", "--local", "–L", "—local", "-LOCAL".replace("-LOCAL", "--LOCAL")])
def test_the_flag_accepts_phone_keyboard_dashes_and_case(send, services_file, tmp_path, flag):
    services_file(demo(tmp_path / "m"))
    assert ask(send, f"!service {flag} demo -up") == "✅ demo: levantado"


def test_without_the_flag_it_is_the_hosted_status_command(send, settings):
    """`!service` alone must never touch local services: it is the read-only hosted status."""
    assert ask(send, "!service") == "El comando no está configurado: falta la API key en el .env de la API."


# ---- start / stop / status ----------------------------------------------------------------------

def test_start_stop_and_status_cycle(send, services_file, tmp_path):
    marker = tmp_path / "running"
    services_file(demo(marker))
    assert ask(send, "!service -L demo") == "🔴 demo: detenido"            # no flag = status
    assert ask(send, "!service -L demo -up") == "✅ demo: levantado"
    assert marker.exists()
    assert ask(send, "!service -L demo") == "🟢 demo: activo"
    assert ask(send, "!service -L demo -down") == "✅ demo: apagado"
    assert not marker.exists()
    assert ask(send, "!service -L demo") == "🔴 demo: detenido"


def test_flags_can_go_before_or_after_the_name_and_use_long_or_dash_forms(send, services_file, tmp_path):
    marker = tmp_path / "m"
    services_file(demo(marker))
    assert ask(send, "!service -L -up demo") == "✅ demo: levantado" and marker.exists()
    assert ask(send, "!service -L demo --down") == "✅ demo: apagado" and not marker.exists()
    assert ask(send, "!service -L demo –up") == "✅ demo: levantado"   # en dash from a phone keyboard
    assert ask(send, "!service -L demo —down") == "✅ demo: apagado"   # em dash for "--"
    assert ask(send, "!service -L DEMO -UP") == "✅ demo: levantado"   # case doesn't matter


def test_the_old_words_no_longer_work(send, services_file, tmp_path):
    marker = tmp_path / "m"
    services_file(demo(marker))
    for word in ("iniciar", "detener", "estado", "levantar", "bajar", "parar", "start", "stop", "status"):
        assert ask(send, f"!service -L demo {word}").startswith("Uso:"), word
    assert not marker.exists()


@pytest.mark.parametrize("text", ["!service -L demo -up -down", "!service -L demo -up -up", "!service -L demo -x",
                                  "!service -L demo -upp", "!service -L -up", "!service -L demo otro -up",
                                  "!service -L demo -down demo"])
def test_invalid_flag_combinations_are_rejected_and_run_nothing(send, services_file, tmp_path, text):
    marker = tmp_path / "m"
    services_file(demo(marker))
    assert ask(send, text).startswith("Uso: !service -L <nombre> [-up | -down]")
    assert not marker.exists()


def test_only_two_flags_change_anything():
    assert service_control.FLAG_ACTIONS == {"-up": "start", "--up": "start", "-down": "stop", "--down": "stop"}


@pytest.mark.parametrize("word, icon, label", [
    ("active", "🟢", "activo"), ("inactive", "🔴", "detenido"), ("failed", "🔴", "falló"),
    ("activating", "🟡", "iniciando"), ("deactivating", "🟡", "deteniéndose")])
def test_status_understands_systemd_words(send, services_file, word, icon, label):
    services_file({"x": {"status": ["echo", word]}})
    assert ask(send, "!service -L x") == f"{icon} x: {label}"


def test_status_of_a_non_systemd_command_uses_the_exit_code(send, services_file):
    services_file({"ok": {"status": ["true"]}, "ko": {"status": [PY, "-c", "print('caído'); raise SystemExit(2)"]}})
    assert ask(send, "!service -L ok") == "🟢 ok: activo"
    assert ask(send, "!service -L ko") == "🔴 ko: no está activo (caído)"


def test_failures_show_the_exit_code_and_output(send, services_file):
    services_file({"x": {"start": [PY, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(4)"]}})
    assert ask(send, "!service -L x -up") == "❌ x: no se pudo levantar (código 4)\nboom"


def test_missing_program_is_reported_not_crashed(send, services_file):
    services_file({"x": {"start": ["/nonexistent/program"]}})
    assert ask(send, "!service -L x -up") == '❌ x: no pude levantar: no encontré el programa "/nonexistent/program".'


def test_a_slow_command_is_killed_at_the_timeout(send, services_file):
    services_file({"lento": {"start": ["sleep", "30"]}})
    started = time.monotonic()
    reply = ask(send, "!service -L lento -up")
    assert time.monotonic() - started < 6, "the command was not stopped at the timeout"
    assert reply == "⏳ lento: levantar no terminó en 2 s. Puede que igual haya funcionado: probá !service -L lento."


def test_long_output_is_cut(send, services_file):
    services_file({"x": {"start": [PY, "-c", "print('y' * 2000)"]}})
    assert len(ask(send, "!service -L x -up")) < 500


# ---- injection and abuse ------------------------------------------------------------------------

def test_nothing_typed_in_the_chat_is_ever_executed(send, services_file, tmp_path):
    marker, evil = tmp_path / "m", tmp_path / "pwned"
    services_file(demo(marker))
    attacks = [
        f"!service -L demo -up; touch {evil}",
        f"!service -L demo -up && touch {evil}",
        f"!service -L demo -up | touch {evil}",
        f"!service -L demo -up $(touch {evil})",
        f"!service -L demo -up `touch {evil}`",
        f"!service -L demo;touch {evil} -up",
        f"!service -L touch {evil} -up",
        f"!service -L demo -up {evil}",
        "!service -L demo -up extra",
        "!service -L ../../etc/passwd -up",
        "!service -L ../../etc/passwd",
        "!service -L demo -up;id",
    ]
    for text in attacks:
        assert ask(send, text).startswith(("Uso:", "No tengo un servicio local")), text
    assert not evil.exists() and not marker.exists()


def test_commands_run_without_a_shell(send, services_file):
    """Metacharacters in the *configured* argv stay literal too: no shell ever interprets them."""
    services_file({"x": {"start": ["echo", "$HOME; ls | cat `id`"]}})
    assert ask(send, "!service -L x -up") == "✅ x: levantado\n$HOME; ls | cat `id`"


def test_unknown_service_and_missing_action(send, services_file):
    services_file({"solo-estado": {"status": ["true"]}, "solo-up": {"start": ["true"]}})
    assert ask(send, "!service -L nada -up") == 'No tengo un servicio local llamado "nada".'
    assert ask(send, "!service -L nada") == 'No tengo un servicio local llamado "nada".'
    assert ask(send, "!service -L solo-estado -up") == "solo-estado no tiene configurada la acción -up."
    assert ask(send, "!service -L solo-estado -down") == "solo-estado no tiene configurada la acción -down."
    assert ask(send, "!service -L solo-up") == "solo-up no tiene configurado el estado."


def test_only_the_listed_program_runs_and_never_with_a_stdin(send, services_file):
    services_file({"x": {"start": [PY, "-c", "import sys; print(repr(sys.stdin.read()))"]}})
    assert ask(send, "!service -L x -up") == "✅ x: levantado\n''"  # stdin is closed: it can't hang waiting for input


# ---- "-L" alone: usage, never a list ------------------------------------------------------------

USAGE = "Uso: !service -L <nombre> [-up | -down]"


def test_the_flag_alone_shows_how_to_use_it_and_lists_nothing(send, services_file, tmp_path):
    marker = tmp_path / "status-ran"
    services_file({"secreto": {"status": ["touch", str(marker)], "description": "no debería aparecer"}})
    reply = ask(send, "!service -L")
    assert reply == USAGE
    assert "\n" not in reply, "just the one usage line: no explanations, no example"
    assert "secreto" not in reply and "no debería aparecer" not in reply
    assert not marker.exists(), "showing the usage must not run any command"


@pytest.mark.parametrize("text", ["!service -L", "!service -l", "!service --local", "!service –L", "!service -L -up", "!service -L -down"])
def test_no_service_name_always_means_usage(send, services_file, text):
    services_file({"pg": {"status": ["true"]}})
    assert ask(send, text) == USAGE


def test_the_usage_uses_the_prefix_the_user_typed(send):
    assert ask(send, "/service -L", user="99", platform="telegram") == USAGE.replace("!", "/")


def test_an_unknown_name_without_a_config_file_says_so(send):
    assert ask(send, "!service -L pg") == ('No tengo un servicio local llamado "pg".\n'
                                           "No hay servicios locales configurados (falta services.json, mirá services.example.json).")


def test_broken_config_entries_are_reported_when_the_name_is_not_found(send, services_file, tmp_path):
    config = demo(tmp_path / "m")
    config.update({"MAL NOMBRE": {"start": ["true"]}, "sin-acciones": {"description": "x"},
                   "argv-roto": {"start": "systemctl start x"}})
    services_file(config)
    reply = ask(send, "!service -L pg")
    assert reply.startswith('No tengo un servicio local llamado "pg".\n⚠️ Problemas en la configuración:')
    assert "MAL NOMBRE" in reply and "sin-acciones" in reply and "argv-roto.start" in reply


def test_broken_entries_do_not_get_in_the_way_of_a_working_service(send, services_file, tmp_path):
    config = demo(tmp_path / "m")
    config["argv-roto"] = {"start": "systemctl start x"}
    services_file(config)
    assert ask(send, "!service -L demo -up") == "✅ demo: levantado"  # no noise about the broken one


def test_invalid_json_is_reported_when_the_name_is_not_found(send, services_file):
    services_file("{ esto no es json")
    assert "No pude leer services.json" in ask(send, "!service -L pg")


# ---- audit log ----------------------------------------------------------------------------------

def test_every_start_and_stop_is_logged_with_who_did_it(send, services_file, tmp_path, caplog):
    services_file(demo(tmp_path / "m"))
    with caplog.at_level(logging.INFO, logger="tox.services"):
        ask(send, "!service -L demo -up")
        ask(send, "!service -L demo")
    records = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert ("WARNING", "LOCAL SERVICE by whatsapp:admin1@lid: demo start -> exit 0") in records
    assert ("INFO", "LOCAL SERVICE by whatsapp:admin1@lid: demo status -> exit 0") in records


# ---- catalog validation and environment ---------------------------------------------------------

def test_catalog_validation(tmp_path):
    file = tmp_path / "s.json"
    file.write_text(json.dumps({
        "ok": {"start": ["a"], "description": "d" * 500, "desconocida": ["x"]},
        "Mayusculas": {"start": ["a"]}, "-guion": {"start": ["a"]}, "x" * 40: {"start": ["a"]},
        "vacio": {"start": []}, "no-str": {"start": ["a", 1]}, "larga": {"start": ["a"] * 21},
        "arg-largo": {"start": ["a" * 301]}, "no-dict": ["start"]}))
    catalog = service_control.load_catalog(str(file))
    assert list(catalog.services) == ["ok"] and len(catalog.services["ok"].description) == 100
    assert set(catalog.services["ok"].commands) == {"start"}  # unknown action keys are ignored
    assert len(catalog.problems) == 8


def test_systemctl_gets_a_runtime_dir_even_if_the_api_was_started_without_one(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    service = service_control.ServiceDef("x", "", {"status": (PY, "-c", "import os; print(os.environ['XDG_RUNTIME_DIR'])")})
    assert service_control.run_action(service, "status", 5).output == f"/run/user/{os.getuid()}"
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/custom")
    assert service_control.run_action(service, "status", 5).output == "/custom"


def test_ayuda_documents_the_flag_only_for_admins(send):
    text = send("!ayuda service", user=ADMIN)[0]
    assert "-L" in text and "-up" in text and "-down" in text
    assert send("!ayuda service", user="someone@lid") == ["No conozco ese comando. Probá !ayuda"]


# ---- detailed status ----------------------------------------------------------------------------

@pytest.fixture
def fast_cpu_sample(monkeypatch):
    from app.commands import service as service_command
    monkeypatch.setattr(service_command, "CPU_SAMPLE_SECONDS", 0.05)


def systemd_stamp(moment):
    return f"Mon {moment:%Y-%m-%d %H:%M:%S} -03"


def show_script(tmp_path, *, active, started_ago=125, stopped_ago=300, ran_for=90, cpu_start=1_000_000_000, cpu_step=0):
    """A Python one-liner that prints `systemctl show`-style output. cpu_step makes the CPU counter grow on every call."""
    from datetime import datetime, timedelta
    now = datetime.now()
    counter = tmp_path / "cpu-counter"
    lines = [f"ActiveState={'active' if active else 'inactive'}", "Result=success", "NRestarts=2", "ExecMainStatus=0"]
    if active:
        lines += [f"ActiveEnterTimestamp={systemd_stamp(now - timedelta(seconds=started_ago))}",
                  "MainPID=4242", "MemoryCurrent=31457280", "MemoryPeak=33554432"]
    else:
        stop = now - timedelta(seconds=stopped_ago)
        lines += [f"ActiveEnterTimestamp={systemd_stamp(stop - timedelta(seconds=ran_for))}",
                  f"ActiveExitTimestamp={systemd_stamp(stop)}", f"InactiveEnterTimestamp={systemd_stamp(stop)}",
                  "MainPID=0", "MemoryCurrent=[not set]", "MemoryPeak=[not set]"]
    code = (
        "import os\n"
        f"c = {str(counter)!r}\n"
        f"n = int(open(c).read()) if os.path.exists(c) else {cpu_start}\n"
        f"open(c, 'w').write(str(n + {cpu_step}))\n"
        f"print('CPUUsageNSec=' + str(n))\n"
        + "".join(f"print({line!r})\n" for line in lines))
    # As a file: the catalog rejects any single argument over 300 characters (on purpose).
    script = tmp_path / f"show-{'active' if active else 'stopped'}.py"
    script.write_text(code)
    return [PY, str(script)]


def detailed(tmp_path, *, active, extra=None, **kwargs):
    config = {"status": ["echo", "active" if active else "inactive"] if active else [PY, "-c", "print('inactive'); raise SystemExit(3)"],
              "info": show_script(tmp_path, active=active, **kwargs)}
    config.update(extra or {})
    return {"demo": config}


def test_status_of_a_running_service_shows_uptime_memory_and_cpu(send, services_file, tmp_path, fast_cpu_sample):
    services_file(detailed(tmp_path, active=True, cpu_step=20_000_000))
    lines = ask(send, "!service -L demo").splitlines()
    assert lines[0] == "🟢 demo: activo"
    assert re.fullmatch(r"⏱️ Activo desde \d\d:\d\d:\d\d \(hace 2 min \d+ s\)", lines[1]), lines[1]
    assert lines[2] == "💾 Memoria: 30.0 MB (pico 32.0 MB)"
    assert re.fullmatch(r"⚙️ CPU: \d+\.\d % ahora · \d+\.\d % promedio · 1\.0 s en total", lines[3]), lines[3]
    assert float(re.search(r"([\d.]+) % ahora", lines[3])[1]) > 0, "the CPU counter grew, so 'now' must be above 0"
    assert lines[4] == "🔁 PID 4242 · reinicios automáticos: 2"


def test_an_idle_service_shows_zero_cpu_now(send, services_file, tmp_path, fast_cpu_sample):
    services_file(detailed(tmp_path, active=True, cpu_step=0))
    assert "0.0 % ahora" in ask(send, "!service -L demo")


def test_status_of_a_stopped_service_shows_since_when_and_the_last_run(send, services_file, tmp_path):
    journal = ["echo", "demo.service: Consumed 1.794s CPU time, 34.8M memory peak, 0B memory swap peak."]
    services_file(detailed(tmp_path, active=False, extra={"last_usage": journal}, stopped_ago=300, ran_for=90))
    lines = ask(send, "!service -L demo").splitlines()
    assert lines[0] == "🔴 demo: detenido"
    assert re.fullmatch(r"🕒 Detenido desde (\d\d/\d\d )?\d\d:\d\d:\d\d \(hace 5 min( 1 s)?\)", lines[1]), lines[1]
    assert lines[2] == "📊 Última ejecución: duró 1 min 30 s · CPU 1.0 s · memoria pico 34.8 MB"
    assert len(lines) == 3


def test_running_and_stopped_do_not_take_a_second_cpu_sample_when_stopped(send, services_file, tmp_path):
    """A stopped service must not wait for the CPU sample interval (nothing to measure)."""
    services_file(detailed(tmp_path, active=False))
    started = time.monotonic()
    ask(send, "!service -L demo")
    assert time.monotonic() - started < 0.9


def test_without_info_the_status_is_just_the_state_line(send, services_file):
    services_file({"demo": {"status": ["echo", "active"]}})
    assert ask(send, "!service -L demo") == "🟢 demo: activo"


def test_if_info_fails_the_status_still_answers(send, services_file):
    services_file({"demo": {"status": ["echo", "active"], "info": ["/nonexistent/program"],
                            "last_usage": [PY, "-c", "raise SystemExit(1)"]}})
    assert ask(send, "!service -L demo") == "🟢 demo: activo"


def test_a_slow_info_command_cannot_hang_the_reply(send, services_file):
    services_file({"demo": {"status": ["echo", "active"], "info": ["sleep", "30"]}})
    started = time.monotonic()
    assert ask(send, "!service -L demo") == "🟢 demo: activo"
    assert time.monotonic() - started < 5


def test_the_extra_lookups_cannot_be_run_from_the_chat(send, services_file, tmp_path):
    marker = tmp_path / "ran"
    services_file({"demo": {"status": ["true"], "info": ["touch", str(marker)], "last_usage": ["touch", str(marker) + "2"]}})
    for text in ("!service -L demo -info", "!service -L demo info", "!service -L demo -last_usage", "!service -L demo last_usage"):
        assert ask(send, text).startswith("Uso:"), text
    assert not marker.exists()
    ask(send, "!service -L demo")  # only the status view runs them (they are read-only lookups you configured)
    assert marker.exists()


def test_the_extra_lookups_are_validated_like_any_other_command(tmp_path):
    file = tmp_path / "s.json"
    file.write_text(json.dumps({"ok": {"status": ["true"], "info": ["a"], "last_usage": ["b"]},
                                "roto": {"status": ["true"], "info": "systemctl show x"},
                                "solo-extra": {"info": ["a"]}}))
    catalog = service_control.load_catalog(str(file))
    assert set(catalog.services) == {"ok", "roto"} - {"roto"} | {"roto"}  # roto keeps working, without its bad extra
    assert set(catalog.services["ok"].commands) == {"status", "info", "last_usage"}
    assert "info" not in catalog.services["roto"].commands
    assert any("roto.info" in problem for problem in catalog.problems)
    assert any("solo-extra" in problem for problem in catalog.problems), "an entry with only extras has nothing to run"
    assert "solo-extra" not in catalog.services


# ---- health line (e.g. pg_isready) --------------------------------------------------------------

def test_a_running_service_shows_whether_it_answers(send, services_file, tmp_path, fast_cpu_sample):
    health = ["echo", "/var/run/postgresql:5432 - accepting connections"]
    services_file(detailed(tmp_path, active=True, extra={"health": health}))
    lines = ask(send, "!service -L demo").splitlines()
    assert lines[-1] == "🩺 /var/run/postgresql:5432 - acepta conexiones"


@pytest.mark.parametrize("output, code, expected", [
    ("/var/run/postgresql:5432 - no response", 2, "🩺 ⚠️ /var/run/postgresql:5432 - no responde"),
    ("/var/run/postgresql:5432 - rejecting connections", 1, "🩺 ⚠️ /var/run/postgresql:5432 - rechaza conexiones (arrancando o recuperándose)"),
    ("algo raro que no conocemos", 3, "🩺 ⚠️ algo raro que no conocemos"),
])
def test_health_problems_are_flagged(send, services_file, tmp_path, fast_cpu_sample, output, code, expected):
    health = [PY, "-c", f"print({output!r}); raise SystemExit({code})"]
    services_file(detailed(tmp_path, active=True, extra={"health": health}))
    assert ask(send, "!service -L demo").splitlines()[-1] == expected


def test_health_is_not_asked_of_a_stopped_service(send, services_file, tmp_path):
    marker = tmp_path / "health-ran"
    services_file(detailed(tmp_path, active=False, extra={"health": ["echo", "should not show"]}))
    assert "🩺" not in ask(send, "!service -L demo")


def test_a_broken_or_slow_health_check_is_reported_not_fatal(send, services_file, tmp_path, fast_cpu_sample):
    services_file(detailed(tmp_path, active=True, extra={"health": ["/nonexistent/pg_isready"]}))
    assert ask(send, "!service -L demo").splitlines()[-1] == '🩺 ⚠️ no pude comprobarlo: no encontré el programa "/nonexistent/pg_isready"'
    services_file(detailed(tmp_path, active=True, extra={"health": ["sleep", "30"]}))
    started = time.monotonic()
    assert ask(send, "!service -L demo").splitlines()[-1] == "🩺 ⚠️ no respondió a tiempo"
    assert time.monotonic() - started < 6


def test_the_health_check_cannot_be_run_from_the_chat(send, services_file, tmp_path):
    marker = tmp_path / "ran"
    services_file({"demo": {"status": ["true"], "health": ["touch", str(marker)]}})
    for text in ("!service -L demo -health", "!service -L demo health", "!service -L demo --health"):
        assert ask(send, text).startswith("Uso:"), text
    assert not marker.exists()


def test_service_names_like_pg_work_like_any_other(send, services_file, tmp_path):
    marker = tmp_path / "up"
    services_file({"pg": {"start": ["touch", str(marker)], "stop": ["rm", "-f", str(marker)], "status": ["true"]}})
    assert ask(send, "!service -L pg -up") == "✅ pg: levantado" and marker.exists()
    assert ask(send, "!service -L PG -down") == "✅ pg: apagado" and not marker.exists()
