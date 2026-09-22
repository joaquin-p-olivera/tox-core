import re
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import service_status

KEY = "rnd_test_secret_key"
ADMIN = "admin1@lid"


def svc(id, name, type="web_service", suspended="not_suspended"):
    return {"id": id, "name": name, "type": type, "suspended": suspended,
            "dashboardUrl": f"https://dashboard.example.com/web/{id}",
            "serviceDetails": {"url": f"https://{name}.example.com", "region": "oregon", "plan": "free"}}


def db(id, name, status="available", expires_in_days=None, suspended="not_suspended"):
    raw = {"id": id, "name": name, "status": status, "suspended": suspended, "plan": "free", "region": "oregon",
           "dashboardUrl": f"https://dashboard.example.com/d/{id}"}
    if expires_in_days is not None:
        raw["expiresAt"] = (datetime.now(timezone.utc) + timedelta(days=expires_in_days, hours=1)).isoformat()
    return raw


def dep(status, created="2026-09-20T18:00:00Z", message="Fix login"):
    return {"id": "d", "status": status, "createdAt": created, "finishedAt": created, "commit": {"message": message}}


@pytest.fixture
def status_api(monkeypatch, settings):
    """A fake provider API. Fill .services / .postgres / .key_value / .deploys, or set .fail[path] before !service."""
    class Fake:
        pass

    fake = Fake()
    fake.services, fake.postgres, fake.key_value, fake.deploys = [], [], [], {}
    fake.metrics = {}   # (metric, resource id) -> list of series, see series()
    fake.fail = {}      # path -> an httpx exception to raise, or an int status code to answer with
    fake.requests = []
    object.__setattr__(settings, "RENDER_API_KEY", KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        fake.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        failure = fake.fail.get(path, fake.fail.get("*"))
        if isinstance(failure, Exception):
            raise failure
        if isinstance(failure, int):
            return httpx.Response(failure, json={"message": "nope"})
        if path == "/services":
            return httpx.Response(200, json=[{"service": s, "cursor": "c"} for s in fake.services])
        if path == "/postgres":
            return httpx.Response(200, json=[{"postgres": d, "cursor": "c"} for d in fake.postgres])
        if path == "/key-value":
            return httpx.Response(200, json=[{"keyValue": d, "cursor": "c"} for d in fake.key_value])
        if path in ("/metrics/cpu", "/metrics/memory"):
            return httpx.Response(200, json=fake.metrics.get((path.rsplit("/", 1)[1], request.url.params["resource"]), []))
        match = re.fullmatch(r"/services/([^/]+)/deploys", path)
        if match:
            return httpx.Response(200, json=[{"deploy": d, "cursor": "c"} for d in fake.deploys.get(match[1], [])])
        return httpx.Response(404)

    def make_client(api_key, timeout):
        return httpx.Client(base_url=service_status.BASE_URL, transport=httpx.MockTransport(handler),
                            headers={"Authorization": f"Bearer {api_key}"})

    monkeypatch.setattr(service_status, "make_client", make_client)
    return fake


def points(*values, minutes_ago=0):
    """One data point per minute, the last one `minutes_ago` minutes ago (UTC, like the provider)."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return [{"timestamp": (now - timedelta(minutes=minutes_ago + len(values) - 1 - i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "value": v}
            for i, v in enumerate(values)]


def series(unit, values, instances=("srv-1-abc",)):
    """One series per instance; databases have no instances: one series labelled only by its resource."""
    labels = [[{"field": "instance", "value": i}, {"field": "resource", "value": "x"}] for i in instances] or [[{"field": "resource", "value": "x"}]]
    return [{"labels": label, "unit": unit, "values": values} for label in labels]


def ask(send, text="!service"):
    return send(text, user=ADMIN)[0]


# ---- access -------------------------------------------------------------------------------------

def test_only_admins_can_use_it(send, status_api):
    assert send("!service", user="someone@lid") == ["Este comando es solo para administradores."]
    assert status_api.requests == [], "a non-admin must never trigger a call to the provider"


def test_it_is_hidden_from_help_for_non_admins(send):
    assert "!service" not in send("!ayuda", user="someone@lid")[0]
    assert "!service" in send("!ayuda", user=ADMIN)[0]
    assert send("!ayuda service", user="someone@lid") == ["No conozco ese comando. Probá !ayuda"]
    assert send("!ayuda service", user=ADMIN)[0].startswith("!service")


def test_not_configured(send, settings):
    assert ask(send) == "El comando no está configurado: falta la API key en el .env de la API."


# ---- the list -----------------------------------------------------------------------------------

def test_lists_services_with_their_state(send, status_api):
    status_api.services = [svc("a", "trip-trace-api"), svc("b", "finview", type="static_site"),
                           svc("c", "worker-x", type="background_worker"), svc("d", "pausado", suspended="suspended")]
    status_api.deploys = {"a": [dep("live")], "b": [dep("update_in_progress", "2026-09-21T10:00:00Z"), dep("live")],
                          "c": [dep("build_failed"), dep("live", "2026-09-10T00:00:00Z")], "d": [dep("live")]}
    assert ask(send).splitlines() == [
        "☁️ 4 servicios, 2 para revisar",
        "🟡 finview (sitio) — desplegando… (sigue la versión anterior)",
        "⏸️ pausado (web) — suspendido",
        "🟢 trip-trace-api (web) — en línea",
        "🟠 worker-x (worker) — en línea, pero el último deploy falló",
    ]


def test_all_good(send, status_api):
    status_api.services = [svc("a", "api"), svc("b", "web")]
    status_api.deploys = {"a": [dep("live")], "b": [dep("live")]}
    assert ask(send).splitlines()[0] == "☁️ 2 servicios, todo en orden"


def test_databases_are_listed_too(send, status_api):
    """The bug that started this: a service's database lives on a different endpoint and was missing."""
    status_api.services = [svc("a", "finview-backend")]
    status_api.deploys = {"a": [dep("live")]}
    status_api.postgres = [db("p", "finview-database")]
    status_api.key_value = [db("k", "finview-cache", status="unavailable")]
    assert ask(send).splitlines() == [
        "☁️ 3 servicios, 1 para revisar",
        "🟢 finview-backend (web) — en línea",
        "🔴 finview-cache (key-value) — no disponible",
        "🟢 finview-database (bd) — disponible",
    ]


@pytest.mark.parametrize("kwargs, icon, label", [
    ({"status": "available"}, "🟢", "disponible"),
    ({"status": "available", "expires_in_days": 30}, "🟢", "disponible"),
    ({"status": "available", "expires_in_days": 3}, "🟠", "disponible, pero vence en 3 d"),
    ({"status": "available", "expires_in_days": 0}, "🟠", "disponible, pero vence hoy"),
    ({"status": "available", "expires_in_days": -2}, "🔴", "vencida"),
    ({"status": "creating"}, "🟡", "creándose"),
    ({"status": "maintenance_scheduled"}, "🟡", "disponible, con mantenimiento programado"),
    ({"status": "maintenance_in_progress"}, "🟡", "en mantenimiento"),
    ({"status": "unavailable"}, "🔴", "no disponible"),
    ({"status": "recovery_failed"}, "🔴", "falló la recuperación"),
    ({"status": "suspended"}, "⏸️", "suspendida"),
    ({"status": "available", "suspended": "suspended"}, "⏸️", "suspendida"),
    ({"status": "unknown"}, "⚪", "estado desconocido"),
])
def test_database_verdicts(send, status_api, kwargs, icon, label):
    status_api.postgres = [db("p", "mi-bd", **kwargs)]
    assert f"{icon} mi-bd (bd) — {label}" in ask(send)


@pytest.mark.parametrize("deploys, icon, label", [
    ([dep("live")], "🟢", "en línea"),
    ([dep("build_in_progress")], "🟡", "desplegando…"),
    ([dep("build_failed")], "🔴", "caído: el último deploy falló"),
    ([dep("canceled")], "🔴", "caído: el último deploy fue cancelado"),
    ([dep("canceled"), dep("live", "2026-09-01T00:00:00Z")], "🟠", "en línea, pero el último deploy fue cancelado"),
    ([dep("deactivated")], "⚪", "desactivado"),
    ([], "⚪", "todavía sin deploys"),
])
def test_health_verdicts(deploys, icon, label):
    infos = sorted((service_status.DeployInfo(d["status"], d["createdAt"]) for d in deploys),
                   key=lambda d: d.created_at, reverse=True)  # assess() expects newest first
    health = service_status.assess(service_status.ServiceInfo("a", "x", "web_service", False), infos)
    assert (health.icon, health.label) == (icon, label)


def test_newest_deploy_is_used_even_if_the_api_returns_them_unsorted(send, status_api):
    status_api.services = [svc("a", "api")]
    status_api.deploys = {"a": [dep("live", "2026-09-01T00:00:00Z"), dep("build_failed", "2026-09-20T00:00:00Z")]}
    assert "🟠 api (web) — en línea, pero el último deploy falló" in ask(send)


def test_one_unreadable_service_does_not_break_the_list(send, status_api, monkeypatch):
    status_api.services = [svc("a", "api"), svc("b", "roto")]
    status_api.deploys = {"a": [dep("live")]}
    original = service_status.list_deploys

    def flaky(client, service_id):
        if service_id == "b":
            raise service_status.StatusError("El proveedor no respondió a tiempo.")
        return original(client, service_id)

    monkeypatch.setattr(service_status, "list_deploys", flaky)
    reply = ask(send)
    assert "🟢 api (web) — en línea" in reply and "❓ roto (web) — no pude leer sus deploys" in reply


def test_a_failing_database_endpoint_keeps_the_services_and_says_so(send, status_api):
    status_api.services = [svc("a", "api")]
    status_api.deploys = {"a": [dep("live")]}
    status_api.fail["/postgres"] = httpx.ReadTimeout("slow")
    reply = ask(send)
    assert "🟢 api (web) — en línea" in reply
    assert reply.endswith("⚠️ No pude leer las bases de datos: El proveedor no respondió a tiempo.")


def test_empty_account(send, status_api):
    assert ask(send) == "No encontré servicios en esa cuenta."


# ---- detail -------------------------------------------------------------------------------------

@pytest.fixture
def montevideo(monkeypatch):
    """Deploy times are shown in the machine's time zone: pin it so the expected text is exact."""
    monkeypatch.setenv("TZ", "America/Montevideo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_detail_of_a_service_has_status_region_usage_two_deploys_and_panel(send, status_api, montevideo):
    status_api.services = [svc("a", "trip-trace-api"), svc("b", "finview")]
    status_api.deploys = {"a": [dep("live", "2026-09-20T18:00:00Z", "Bump deps\n\nlong body"),
                                dep("deactivated", "2026-09-01T00:00:00Z", "Old"),
                                dep("deactivated", "2026-08-01T00:00:00Z", "Older")]}
    status_api.metrics[("memory", "a")] = series("bytes", points(90_000_000, 95_801_340, 91_000_000))
    status_api.metrics[("cpu", "a")] = series("cpu", points(0.0008, 0.0016, 0.0014))
    reply = ask(send, "!service trip-trace-api")
    assert reply.splitlines() == [
        "🟢 trip-trace-api (web)",
        "Estado: en línea",
        "Región: oregon",
        "💾 Memoria: 86.8 MB (máx. 1 h: 91.4 MB)",
        "⚙️ CPU: 0.14 % (máx. 1 h: 0.16 %)",
        "Últimos deploys:",
        '• live · hace ' + reply.splitlines()[6].split("hace ")[1].split(" —")[0] + ' — "Bump deps" - 20/09 15:00',
        '• deactivated · hace ' + reply.splitlines()[7].split("hace ")[1].split(" —")[0] + ' — "Old" - 31/08 21:00',
        "Panel: https://dashboard.example.com/web/a",
    ]
    assert "Older" not in reply, "only the last 2 deploys are shown"
    assert "URL" not in reply and "Plan" not in reply and "free" not in reply


def test_deploy_lines_end_with_a_short_local_date_and_time(send, status_api, montevideo):
    status_api.services = [svc("a", "api")]
    status_api.deploys = {"a": [dep("live", "2026-12-31T23:30:00Z", "x"), dep("deactivated", "2026-01-05T03:07:00Z", "y")]}
    lines = [l for l in ask(send, "!service api").splitlines() if l.startswith("•")]
    assert lines[0].endswith(' - 31/12 20:30') and lines[1].endswith(' - 05/01 00:07')


def test_a_deploy_without_a_commit_message_still_gets_its_time(send, status_api, montevideo):
    status_api.services = [svc("a", "api")]
    status_api.deploys = {"a": [{"id": "d", "status": "live", "createdAt": "2026-09-20T18:00:00Z"}]}
    line = next(l for l in ask(send, "!service api").splitlines() if l.startswith("•"))
    assert re.fullmatch(r"• live · hace [\w ]+ - 20/09 15:00", line), line


def test_detail_of_a_database_shows_usage_and_no_deploys(send, status_api):
    status_api.postgres = [db("p", "finview-database", expires_in_days=20)]
    status_api.metrics[("memory", "p")] = series("bytes", points(44_032_000, 51_798_016), instances=())
    status_api.metrics[("cpu", "p")] = series("cpu", points(0.0061, 0.011), instances=())
    lines = ask(send, "!service finview-database").splitlines()
    assert lines[:3] == ["🟢 finview-database (bd)", "Estado: disponible", "Región: oregon"]
    assert lines[3].startswith("Vence: 20") and lines[3].endswith("(vence en 20 d)")
    assert lines[4:6] == ["💾 Memoria: 49.4 MB (máx. 1 h: 49.4 MB)", "⚙️ CPU: 1.1 % (máx. 1 h: 1.1 %)"]
    assert lines[-1] == "Panel: https://dashboard.example.com/d/p"
    assert "Últimos deploys" not in "\n".join(lines) and "Plan" not in "\n".join(lines)
    assert not any("/deploys" in r.url.path for r in status_api.requests), "databases have no deploys to fetch"


def test_detail_accepts_partial_names_case_insensitively(send, status_api):
    status_api.services = [svc("a", "trip-trace-api"), svc("b", "finview")]
    status_api.deploys = {"b": [dep("live")]}
    assert ask(send, "!service FIN").startswith("🟢 finview (web)")


def test_a_shared_prefix_shows_all_matches_instead_of_guessing(send, status_api):
    """`!service finview` must not silently answer with just the backend when the database also matches."""
    status_api.services = [svc("a", "finview-backend"), svc("z", "otro")]
    status_api.deploys = {"a": [dep("live")]}
    status_api.postgres = [db("p", "finview-database")]
    assert ask(send, "!service finview").splitlines() == [
        '☁️ 2 servicios que coinciden con "finview", todo en orden',
        "🟢 finview-backend (web) — en línea",
        "🟢 finview-database (bd) — disponible",
        "Usá el nombre completo para ver el detalle.",
    ]
    assert ask(send, "!service finview-database").startswith("🟢 finview-database (bd)")  # full name = detail


def test_exact_match_wins_over_partial_ones(send, status_api):
    status_api.services = [svc("a", "finview"), svc("b", "finview-backend")]
    status_api.deploys = {"a": [dep("live")]}
    assert ask(send, "!service finview").startswith("🟢 finview (web)\nEstado")


def test_unknown_name_lists_what_exists_including_databases(send, status_api):
    status_api.services = [svc("a", "trip-trace-api")]
    status_api.postgres = [db("p", "finview-database")]
    assert ask(send, "!service nada") == 'No encuentro un servicio que coincida con "nada". Tenés: finview-database, trip-trace-api'


def test_long_commit_messages_are_cut(send, status_api):
    status_api.services = [svc("a", "api")]
    status_api.deploys = {"a": [dep("live", message="x" * 300)]}
    line = next(l for l in ask(send, "!service api").splitlines() if l.startswith("• live"))
    assert len(line) < 120


# ---- safety -------------------------------------------------------------------------------------

def test_only_read_requests_are_ever_made(send, status_api):
    """The command must not be able to change anything at the provider."""
    status_api.services = [svc("a", "api"), svc("b", "web")]
    status_api.postgres = [db("p", "bd")]
    status_api.deploys = {"a": [dep("live")], "b": [dep("live")]}
    ask(send)
    ask(send, "!service api")
    ask(send, "!service bd")
    assert status_api.requests and all(r.method == "GET" for r in status_api.requests)
    assert all(r.url.host == "api.render.com" for r in status_api.requests)
    assert {r.url.path for r in status_api.requests if "deploys" not in r.url.path} == {
        "/v1/services", "/v1/postgres", "/v1/key-value", "/v1/metrics/cpu", "/v1/metrics/memory"}


def test_the_key_is_sent_as_a_bearer_token_and_never_echoed(send, status_api):
    status_api.services = [svc("a", "api")]
    status_api.deploys = {"a": [dep("live")]}
    assert KEY not in ask(send) and KEY not in ask(send, "!service api")
    assert {r.headers["authorization"] for r in status_api.requests} == {f"Bearer {KEY}"}


def test_user_text_cannot_change_what_is_requested(send, status_api):
    status_api.services = [svc("a", "api")]
    ask(send, "!service ../../account?x=1 https://evil.example")
    assert all(r.url.host == "api.render.com" and r.url.path.startswith("/v1/") for r in status_api.requests)


@pytest.mark.parametrize("failure, expected", [
    (401, "⚠️ El proveedor rechazó la API key. ¿Está bien copiada en el .env?"),
    (429, "⚠️ El proveedor limitó las consultas. Probá de nuevo en un minuto."),
    (500, "⚠️ El proveedor respondió 500."),
    (httpx.ReadTimeout("slow"), "⚠️ El proveedor no respondió a tiempo."),
    (httpx.ConnectError("refused"), "⚠️ No pude conectar con el proveedor."),
])
def test_provider_failures_become_clear_messages(send, status_api, failure, expected):
    status_api.fail["*"] = failure
    reply = ask(send)
    assert reply == expected and KEY not in reply


def test_unexpected_payloads_do_not_crash(send, status_api, monkeypatch):
    def make_client(api_key, timeout):
        return httpx.Client(base_url=service_status.BASE_URL, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"not": "a list"})))
    monkeypatch.setattr(service_status, "make_client", make_client)
    assert ask(send) == "⚠️ El proveedor devolvió una respuesta inesperada."


def test_malformed_entries_are_skipped(send, status_api):
    status_api.services = [svc("a", "api"), {"id": "x"}, "garbage"]
    status_api.deploys = {"a": [dep("live")]}
    assert ask(send).splitlines()[0] == "☁️ 1 servicios, todo en orden"


# ---- helpers ------------------------------------------------------------------------------------

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-09-21T11:59:30Z", "hace instantes"), ("2026-09-21T11:30:00Z", "hace 30 min"),
    ("2026-09-21T06:00:00Z", "hace 6 h"), ("2026-09-18T12:00:00Z", "hace 3 d"),
    (None, "?"), ("basura", "?"), ("2026-09-21T11:30:00.123456+00:00", "hace 30 min"),
])
def test_ago(timestamp, expected):
    assert service_status.ago(timestamp, now=NOW) == expected


@pytest.mark.parametrize("timestamp, expected", [
    ("2026-09-21T13:00:00Z", 0), ("2026-09-24T12:00:00Z", 3), ("2026-09-20T12:00:00Z", -1),
    ("2026-10-21T12:00:00Z", 30), (None, None), ("basura", None)])
def test_days_until(timestamp, expected):
    assert service_status.days_until(timestamp, now=NOW) == expected


# ---- CPU and memory in the detail ---------------------------------------------------------------

def one_service(status_api, **kwargs):
    status_api.services = [svc("a", "api", **kwargs)]
    status_api.deploys = {"a": [dep("live")]}


def usage_lines(reply):
    return [l for l in reply.splitlines() if l[:1] in "💾⚙💤⚠"]


def test_a_sleeping_service_has_no_metrics_and_says_so(send, status_api):
    one_service(status_api)  # no series at all, like a free-plan service that spun down
    assert usage_lines(ask(send, "!service api")) == ["💤 Sin métricas en la última hora (probablemente dormido o sin instancias)"]


def test_a_database_without_metrics_does_not_suggest_it_is_asleep(send, status_api):
    status_api.postgres = [db("p", "bd")]
    assert usage_lines(ask(send, "!service bd")) == ["💤 Sin métricas en la última hora"]


def test_instances_are_summed_and_counted(send, status_api):
    one_service(status_api)
    status_api.metrics[("memory", "a")] = series("bytes", points(100 * 1024**2, 100 * 1024**2), instances=("i1", "i2", "i3"))
    status_api.metrics[("cpu", "a")] = series("cpu", points(0.5, 0.5), instances=("i1", "i2"))
    assert usage_lines(ask(send, "!service api")) == [
        "💾 Memoria: 300.0 MB (máx. 1 h: 300.0 MB) · 3 instancias",
        "⚙️ CPU: 100.0 % (máx. 1 h: 100.0 %) · 2 instancias",
    ]


def test_old_data_is_not_presented_as_current(send, status_api):
    one_service(status_api)
    status_api.metrics[("memory", "a")] = series("bytes", points(50 * 1024**2, minutes_ago=42))
    line = usage_lines(ask(send, "!service api"))[0]
    assert line.startswith("💾 Memoria: 50.0 MB (máx. 1 h: 50.0 MB) · último dato hace 4") and "min" in line


def test_fresh_data_has_no_age_note(send, status_api):
    one_service(status_api)
    status_api.metrics[("memory", "a")] = series("bytes", points(50 * 1024**2, minutes_ago=2))
    assert "último dato" not in ask(send, "!service api")


def test_only_memory_or_only_cpu_is_fine(send, status_api):
    one_service(status_api)
    status_api.metrics[("cpu", "a")] = series("cpu", points(0.25))
    assert usage_lines(ask(send, "!service api")) == ["⚙️ CPU: 25.0 % (máx. 1 h: 25.0 %)"]


@pytest.mark.parametrize("unit, value, expected", [
    ("bytes", 1_048_576, "1.0 MB"), ("BYTES", 2048, "2.0 KB"), ("KB", 2048, "2.0 MB"), ("MB", 512, "512.0 MB"),
    ("GiB", 1.5, "1.5 GB"), ("furlongs", 7, "7 furlongs"), (None, 7, "7 ?")])
def test_memory_units(unit, value, expected):
    from app.commands.service import _memory_text
    assert _memory_text(value, unit) == expected


@pytest.mark.parametrize("unit, value, expected", [
    ("cpu", 0.0014, "0.14 %"), ("cpu", 0.5, "50.0 %"), ("cpu", 2, "200.0 %"), ("cores", 0.25, "25.0 %"), ("CPU", 0, "0.00 %"),
    ("%", 12.34, "12.3 %"), ("percent", 0.5, "0.50 %"), ("furlongs", 7, "7 furlongs"), (None, 7, "7 ?")])
def test_cpu_units(unit, value, expected):
    from app.commands.service import _cpu_text
    assert _cpu_text(value, unit) == expected


def test_a_metrics_failure_does_not_break_the_detail(send, status_api):
    one_service(status_api)
    status_api.fail["/metrics/cpu"] = httpx.ReadTimeout("slow")
    lines = ask(send, "!service api").splitlines()
    assert lines[0] == "🟢 api (web)" and lines[1] == "Estado: en línea"
    assert "⚠️ No pude leer las métricas: El proveedor no respondió a tiempo." in lines
    assert any(l.startswith("• live") for l in lines), "the deploys are still shown"


def test_suspended_services_and_static_sites_skip_the_metrics(send, status_api):
    status_api.services = [svc("a", "pausado", suspended="suspended"), svc("b", "sitio", type="static_site"),
                           svc("c", "tarea", type="cron_job")]
    status_api.deploys = {"a": [dep("live")], "b": [dep("live")], "c": [dep("live")]}
    for name in ("pausado", "sitio", "tarea"):
        assert usage_lines(ask(send, f"!service {name}")) == [], name
    assert not any("/metrics/" in r.url.path for r in status_api.requests), "no pointless calls for things without instances"


def test_the_list_does_not_ask_for_metrics(send, status_api):
    one_service(status_api)
    ask(send)
    assert not any("/metrics/" in r.url.path for r in status_api.requests)


def test_metrics_are_requested_for_the_last_hour_of_that_resource_only(send, status_api):
    one_service(status_api)
    ask(send, "!service api")
    metric_calls = [r for r in status_api.requests if "/metrics/" in r.url.path]
    assert {r.url.path for r in metric_calls} == {"/v1/metrics/cpu", "/v1/metrics/memory"}
    for call in metric_calls:
        params = call.url.params
        assert params["resource"] == "a" and params["resolutionSeconds"] == "60"
        start = datetime.fromisoformat(params["startTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(params["endTime"].replace("Z", "+00:00"))
        assert end - start == timedelta(hours=1)


# ---- get_usage: parsing what the provider returns -----------------------------------------------

def _client(payload):
    return httpx.Client(base_url=service_status.BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))


def test_get_usage_sums_instances_per_timestamp_and_reports_latest_and_peak():
    payload = [
        {"labels": [{"field": "instance", "value": "i1"}], "unit": "bytes",
         "values": [{"timestamp": "2026-09-21T10:00:00Z", "value": 10}, {"timestamp": "2026-09-21T10:01:00Z", "value": 30}]},
        {"labels": [{"field": "instance", "value": "i2"}], "unit": "bytes",
         "values": [{"timestamp": "2026-09-21T10:00:00Z", "value": 50}, {"timestamp": "2026-09-21T10:01:00Z", "value": 5}]},
    ]
    usage = service_status.get_usage(_client(payload), "x", "memory")
    assert (usage.latest, usage.peak, usage.unit, usage.instances) == (35.0, 60.0, "bytes", 2)
    assert usage.at == datetime(2026, 9, 21, 10, 1, tzinfo=timezone.utc)


def test_get_usage_skips_malformed_points_and_series():
    payload = ["basura", {"unit": "cpu", "values": [{"timestamp": "no", "value": 1}, {"value": 2}, {"timestamp": "2026-09-21T10:00:00Z", "value": "x"},
                                                       {"timestamp": "2026-09-21T10:00:00Z", "value": 0.5}]}]
    usage = service_status.get_usage(_client(payload), "x", "cpu")
    assert usage.latest == 0.5 and usage.instances == 1
    assert service_status.get_usage(_client([]), "x", "cpu") is None
    assert service_status.get_usage(_client([{"unit": "cpu", "values": []}]), "x", "cpu") is None


def test_get_usage_rejects_a_non_list_answer():
    with pytest.raises(service_status.StatusError, match="respuesta inesperada"):
        service_status.get_usage(_client({"not": "a list"}), "x", "cpu")
