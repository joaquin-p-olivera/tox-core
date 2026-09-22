import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import select

from ..models import HostSample
from ..services import host_history, host_status, service_control, service_status, systemd_info
from .flags import normalize_flag
from .registry import CommandContext, command

logger = logging.getLogger("tox.services")

CATEGORY = "Administración"
COMMIT_PREVIEW = 60
# What `pg_isready` says, in Spanish (the chat is Spanish). Unknown text is shown as it comes.
HEALTH_TRANSLATIONS = {
    "accepting connections": "acepta conexiones",
    "rejecting connections": "rechaza conexiones (arrancando o recuperándose)",
    "no response": "no responde",
    "no attempt": "no se pudo comprobar (parámetros inválidos)",
}
# Seconds between the two CPU counter readings used for "CPU now" (a test shortens it)
CPU_SAMPLE_SECONDS = 1.0
LOCAL_FLAGS = {"-l", "--local"}
FLAG_HELP = {"start": "-up", "stop": "-down"}
# (past participle for the OK message, infinitive for errors and timeouts)
VERBS = {"start": ("levantado", "levantar"), "stop": ("apagado", "apagar"), "status": ("", "consultar el estado")}
# What `systemctl is-active` prints -> (icon, Spanish label)
SYSTEMD_STATES = {
    "active": ("🟢", "activo"), "activating": ("🟡", "iniciando"), "reloading": ("🟡", "recargando"),
    "deactivating": ("🟡", "deteniéndose"), "inactive": ("🔴", "detenido"), "failed": ("🔴", "falló"),
}
TYPE_LABELS = {
    "web_service": "web", "static_site": "sitio", "private_service": "privado",
    "background_worker": "worker", "cron_job": "cron", "postgres": "bd", "key_value": "key-value",
}


def _type(service: service_status.ServiceInfo) -> str:
    return TYPE_LABELS.get(service.type, service.type)


def _lines(services, deploys) -> tuple[list[str], int]:
    """One line per service, and how many of them need attention."""
    health = {s.id: service_status.assess(s, deploys.get(s.id)) for s in services}
    lines = [f"{health[s.id].icon} {s.name} ({_type(s)}) — {health[s.id].label}" for s in services]
    return lines, sum(1 for h in health.values() if not h.ok)


def _summary(services, deploys, notes, header_suffix: str = "") -> str:
    lines, problems = _lines(services, deploys)
    status = "todo en orden" if problems == 0 else f"{problems} para revisar"
    return "\n".join([f"☁️ {len(services)} servicios{header_suffix}, {status}", *lines, *notes])


FRESH_METRIC_SECONDS = 300
DEPLOYS_SHOWN = 2
_MEMORY_UNITS = {"b": 1, "byte": 1, "bytes": 1, "kb": 1024, "kib": 1024, "mb": 1024**2, "mib": 1024**2,
                 "gb": 1024**3, "gib": 1024**3}
_CORE_UNITS = {"cpu", "cpus", "core", "cores", "vcpu"}  # values are fractions of one core
_PERCENT_UNITS = {"%", "percent", "percentage"}


def _memory_text(value: float, unit: str | None) -> str:
    factor = _MEMORY_UNITS.get((unit or "").lower())
    return systemd_info.fmt_bytes(round(value * factor)) if factor else f"{value:g} {unit or '?'}"


def _cpu_text(value: float, unit: str | None) -> str:
    """Percent of ONE core (100 % = a whole core), like `top`."""
    unit = (unit or "").lower()
    percent = value * 100 if unit in _CORE_UNITS else value if unit in _PERCENT_UNITS else None
    if percent is None:
        return f"{value:g} {unit or '?'}"
    return f"{percent:.2f} %" if percent < 1 else f"{percent:.1f} %"


def _usage_line(icon: str, label: str, usage: service_status.Usage | None, text, now: datetime) -> str | None:
    if usage is None:
        return None
    line = f"{icon} {label}: {text(usage.latest, usage.unit)} (máx. 1 h: {text(usage.peak, usage.unit)})"
    age = (now.astimezone(usage.at.tzinfo) - usage.at).total_seconds() if usage.at.tzinfo else 0
    if age > FRESH_METRIC_SECONDS:  # the provider stopped reporting: don't present old data as current
        line += f" · último dato hace {systemd_info.fmt_duration(age)}"
    if usage.instances > 1:
        line += f" · {usage.instances} instancias"
    return line


def _metric_lines(service: service_status.ServiceInfo, metrics: dict, now: datetime) -> list[str]:
    if service.type not in service_status.METRIC_TYPES or service.suspended:
        return []
    if metrics.get("error"):
        return [f"⚠️ No pude leer las métricas: {metrics['error']}"]
    lines = [line for line in (
        _usage_line("💾", "Memoria", metrics.get("memory"), _memory_text, now),
        _usage_line("⚙️", "CPU", metrics.get("cpu"), _cpu_text, now)) if line]
    if not lines:  # nothing reported in the last hour
        hint = "" if service.is_database else " (probablemente dormido o sin instancias)"
        lines.append(f"💤 Sin métricas en la última hora{hint}")
    return lines


def _detail(service: service_status.ServiceInfo, deploys, metrics: dict | None = None) -> str:
    health = service_status.assess(service, deploys)
    lines = [f"{health.icon} {service.name} ({_type(service)})", f"Estado: {health.label}"]
    if service.region:
        lines.append(f"Región: {service.region}")
    if service.expires_at:
        days = service_status.days_until(service.expires_at)
        when = "" if days is None else f" ({'ya venció' if days < 0 else service_status.expires_in(days)})"
        lines.append(f"Vence: {service.expires_at[:10]}{when}")
    lines += _metric_lines(service, metrics or {}, datetime.now().astimezone())
    if deploys:
        lines.append("Últimos deploys:")
        for deploy in deploys[:DEPLOYS_SHOWN]:
            message = (deploy.commit_message or "").strip().splitlines()[0:1]
            text = f" — \"{message[0][:COMMIT_PREVIEW]}\"" if message else ""
            stamp = service_status.fmt_local(deploy.created_at)
            when = f" - {stamp}" if stamp else ""
            lines.append(f"• {deploy.status} · {service_status.ago(deploy.created_at)}{text}{when}")
    if service.dashboard_url:
        lines.append(f"Panel: {service.dashboard_url}")
    return "\n".join(lines)


@command(
    "service",
    description=("(Admin) Estado de tus servicios y bases de datos alojados. "
                 "Con -L, los servicios locales de la PC: -up los levanta, -down los apaga, sin flag muestra el estado"),
    usage="service [nombre]   |   service -L [nombre] [-up | -down]",
    category=CATEGORY,
    admin_only=True,
)
def service_command(ctx: CommandContext) -> str:
    if ctx.args and normalize_flag(ctx.args[0]) in LOCAL_FLAGS:
        return _local(ctx, ctx.args[1:])
    return _hosted(ctx)


def _hosted(ctx: CommandContext) -> str:
    settings = ctx.settings
    if not settings.RENDER_API_KEY:
        return "El comando no está configurado: falta la API key en el .env de la API."

    query = ctx.raw_args.strip().lower()
    try:
        with service_status.make_client(settings.RENDER_API_KEY, settings.RENDER_API_TIMEOUT_SECONDS) as client:
            inventory = service_status.list_all(client)
            services = inventory.services
            if not services:
                return "No encontré servicios en esa cuenta." + "".join(f"\n{n}" for n in inventory.notes)

            if not query:
                return _summary(services, service_status.fetch_deploys_for(client, services), inventory.notes)

            exact = [s for s in services if s.name.lower() == query]
            matches = exact or [s for s in services if query in s.name.lower()]
            if not matches:
                names = ", ".join(s.name for s in services)
                return f"No encuentro un servicio que coincida con \"{query}\". Tenés: {names}"
            if len(matches) > 1:
                # e.g. "finview" -> the backend AND the database: show them all, detail needs the full name
                summary = _summary(matches, service_status.fetch_deploys_for(client, matches), [],
                                   f" que coinciden con \"{query}\"")
                return f"{summary}\nUsá el nombre completo para ver el detalle."
            match = matches[0]
            deploys, metrics = _detail_data(client, match)
            return _detail(match, deploys, metrics)
    except service_status.StatusError as error:
        return f"⚠️ {error}"


def _detail_data(client, service: service_status.ServiceInfo):
    """Deploys (services only) and CPU/memory of one resource, fetched in parallel. Metrics failures don't fail the detail."""
    wants_metrics = service.type in service_status.METRIC_TYPES and not service.suspended
    with ThreadPoolExecutor(max_workers=3) as pool:
        deploys = None if service.is_database else pool.submit(service_status.list_deploys, client, service.id)
        cpu = pool.submit(service_status.get_usage, client, service.id, "cpu") if wants_metrics else None
        memory = pool.submit(service_status.get_usage, client, service.id, "memory") if wants_metrics else None
    metrics: dict = {}
    try:
        metrics = {"cpu": cpu.result() if cpu else None, "memory": memory.result() if memory else None}
    except service_status.StatusError as error:
        metrics = {"error": str(error)}
    return (deploys.result() if deploys else None), metrics


# ---- local services (-L) ------------------------------------------------------------------------

def _state(result: service_control.RunResult) -> tuple[str, str]:
    """(icon, label) for a `status` run. Understands systemd's words; anything else is judged by its exit code."""
    if result.timed_out:
        return "⏳", "no respondió a tiempo"
    if result.error:
        return "❌", result.error
    word = result.output.strip().lower()
    if word in SYSTEMD_STATES:
        return SYSTEMD_STATES[word]
    if result.exit_code == 0:
        return "🟢", "activo" + (f" ({result.output})" if result.output else "")
    return "🔴", "no está activo" + (f" ({result.output})" if result.output else "")


def _local_usage(prefix: str) -> str:
    return f"Uso: {prefix}service -L <nombre> [-up | -down]"


def _local(ctx: CommandContext, args: list[str]) -> str:
    usage = _local_usage(ctx.prefix)
    if not args:
        return usage  # deliberately no list of services: you have to know the name
    args = [normalize_flag(a) for a in args]
    flags = [a for a in args if a.startswith("-")]
    names = [a for a in args if not a.startswith("-")]
    # One name and at most one of -up/-down. Anything else is rejected, never guessed or ignored,
    # so no extra word can "ride along".
    if len(names) != 1 or len(flags) > 1 or (flags and flags[0] not in service_control.FLAG_ACTIONS):
        return usage
    name = names[0]
    action = service_control.FLAG_ACTIONS[flags[0]] if flags else "status"

    if name in service_control.HOST_NAMES:  # built-in and read-only: not part of the allowlist
        return _host(ctx) if not flags else f"🖥️ {name} solo se puede consultar: no se levanta ni se apaga desde acá."

    catalog = service_control.load_catalog(ctx.settings.SERVICES_FILE)
    definition = catalog.services.get(name)
    if definition is None:
        lines = [f"No tengo un servicio local llamado \"{name}\"."]
        if not catalog.file_found:
            lines.append("No hay servicios locales configurados (falta services.json, mirá services.example.json).")
        if catalog.problems:  # a broken entry may be exactly why the service isn't found
            lines.append("⚠️ Problemas en la configuración:")
            lines += [f"- {problem}" for problem in catalog.problems]
        return "\n".join(lines)
    if action not in definition.commands:
        missing = "configurado el estado" if action == "status" else f"configurada la acción {FLAG_HELP[action]}"
        return f"{name} no tiene {missing}."

    timeout = ctx.settings.SERVICE_COMMAND_TIMEOUT_SECONDS
    if action == "status":
        return _local_status(ctx, definition, timeout)
    result = service_control.run_action(definition, action, timeout)
    outcome = ("timeout" if result.timed_out else f"error: {result.error}" if result.error
               else f"exit {result.exit_code}")
    # Audit trail: who ran what on this machine, and how it ended. Starting/stopping is worth a warning.
    log = logger.info if action == "status" else logger.warning
    log("LOCAL SERVICE by %s: %s %s -> %s", ctx.message.user_key, name, action, outcome)

    done, verb = VERBS[action]
    if result.timed_out:
        return (f"⏳ {name}: {verb} no terminó en {timeout} s. "
                f"Puede que igual haya funcionado: probá {ctx.prefix}service -L {name}.")
    if result.error:
        return f"❌ {name}: no pude {verb}: {result.error}."
    detail = f"\n{result.output}" if result.output else ""
    if result.exit_code == 0:
        return f"✅ {name}: {done}{detail}"
    return f"❌ {name}: no se pudo {verb} (código {result.exit_code}){detail}"


def _local_status(ctx: CommandContext, definition: service_control.ServiceDef, timeout: int) -> str:
    """State line plus, when the service defines `info` / `last_usage`, when it started or stopped and what it uses."""
    commands = definition.commands
    with ThreadPoolExecutor(max_workers=3) as pool:
        status = pool.submit(service_control.run_action, definition, "status", timeout)
        info = pool.submit(service_control.run_action, definition, "info", timeout) if "info" in commands else None
        journal = pool.submit(service_control.run_action, definition, "last_usage", timeout) if "last_usage" in commands else None
        health = pool.submit(service_control.run_action, definition, "health", timeout) if "health" in commands else None
    first_read = time.monotonic()

    result = status.result()
    logger.info("LOCAL SERVICE by %s: %s status -> %s", ctx.message.user_key, definition.name,
                "timeout" if result.timed_out else f"error: {result.error}" if result.error else f"exit {result.exit_code}")
    icon, text = _state(result)
    lines = [f"{icon} {definition.name}: {text}"]

    show = systemd_info.parse_show(info.result().output) if info and _ok(info.result()) else None
    if show is None:
        return lines[0]  # no details configured (or they failed): the plain state is still useful
    usage = systemd_info.parse_consumed(journal.result().output) if journal and _ok(journal.result()) else None

    cpu_now = None
    if show.active_state == "active" and show.cpu_nsec is not None:
        # "CPU now" needs two readings of the cumulative counter a little apart
        time.sleep(max(0.0, CPU_SAMPLE_SECONDS - (time.monotonic() - first_read)))
        second = service_control.run_action(definition, "info", timeout)
        if _ok(second):
            later = systemd_info.parse_show(second.output)
            if later.cpu_nsec is not None:
                cpu_now = systemd_info.cpu_percent(show.cpu_nsec, later.cpu_nsec, time.monotonic() - first_read)
    lines += systemd_info.describe(show, usage, datetime.now(), cpu_now)
    if health and show.active_state == "active":  # a stopped service can't answer: the state line already says it
        lines.append(_health_line(health.result()))
    return "\n".join(lines)


def _health_line(result: service_control.RunResult) -> str:
    if result.timed_out:
        return "🩺 ⚠️ no respondió a tiempo"
    if result.error:
        return f"🩺 ⚠️ no pude comprobarlo: {result.error}"
    text = result.output
    for english, spanish in HEALTH_TRANSLATIONS.items():
        text = text.replace(english, spanish)
    return f"🩺 {text or 'sin respuesta'}" if result.exit_code == 0 else f"🩺 ⚠️ {text or 'sin respuesta'}"


def _ok(result: service_control.RunResult) -> bool:
    return not result.timed_out and result.error is None and result.exit_code == 0 and bool(result.output)


def _host(ctx: CommandContext) -> str:
    """`!service -L host` (or `master`): how the machine itself is doing (CPU, memory, disk, battery, uptime, sessions...)
    plus battery trends and peaks from the samples the API records."""
    now = datetime.now()
    snapshot = host_status.read_snapshot(now, ignored_units=set(ctx.settings.host_ignored_units))
    problems, lines = host_status.describe(snapshot, now)

    stamp = now.timestamp()
    rows = ctx.db.scalars(select(HostSample).where(HostSample.taken_at >= stamp - host_history.WINDOW_SECONDS))
    history = host_history.analyze([host_history.Sample(r.taken_at, r.cpu_percent, r.memory_used_percent, r.temperature_c,
                                                        r.battery_percent, r.battery_status) for r in rows], stamp)
    battery_at = max((i for i, line in enumerate(lines) if "Batería:" in line or line.startswith("🔋")), default=None)
    extra = host_history.battery_lines(history, snapshot.battery, now)
    lines[(battery_at + 1 if battery_at is not None else len(lines)):0] = extra  # right under the battery lines
    lines += host_history.peak_lines(history, now)

    logger.info("LOCAL SERVICE by %s: host status -> %d to review", ctx.message.user_key, problems)
    headline = "🟢 host: todo en orden" if problems == 0 else f"🟠 host: {problems} para revisar"
    return "\n".join([headline, *lines])
