"""Read-only client for the hosting provider's REST API, used by the admin-only !service command.

Only GET requests are made here, on purpose: this module can look at your services but has no way to
suspend, delete or redeploy them. The base URL is a constant, so nothing typed in a chat can redirect it.

The provider lists web services and databases through different endpoints, so "everything" is
services + Postgres + key-value, fetched in parallel.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("tox.service_status")

BASE_URL = "https://api.render.com/v1"
MAX_ITEMS = 100
DEPLOYS_PER_SERVICE = 5
EXPIRY_WARNING_DAYS = 7
IN_PROGRESS = {"created", "queued", "build_in_progress", "update_in_progress", "pre_deploy_in_progress"}
FAILED = {"build_failed", "update_failed", "pre_deploy_failed"}
DATABASE_TYPES = ("postgres", "key_value")
# Resources that run on an instance and therefore have CPU/memory metrics (static sites and cron jobs don't)
METRIC_TYPES = {"web_service", "private_service", "background_worker", "postgres", "key_value"}
METRICS_WINDOW = timedelta(hours=1)
METRICS_RESOLUTION_SECONDS = 60
DATABASE_BUSY = {
    "creating": "creándose",
    "updating_instance": "actualizándose",
    "config_restart": "reiniciándose por un cambio de configuración",
    "maintenance_in_progress": "en mantenimiento",
    "recovery_in_progress": "en recuperación",
}


class StatusError(Exception):
    """The message is safe to show in the chat (it never contains the API key)."""


@dataclass(frozen=True)
class ServiceInfo:
    id: str
    name: str
    type: str  # web_service, static_site, ... or "postgres" / "key_value" for databases
    suspended: bool
    status: str | None = None  # databases only; services are judged by their deploys
    dashboard_url: str | None = None
    region: str | None = None
    expires_at: str | None = None  # free databases expire

    @property
    def is_database(self) -> bool:
        return self.type in DATABASE_TYPES


@dataclass(frozen=True)
class DeployInfo:
    status: str
    created_at: str | None = None
    finished_at: str | None = None
    commit_message: str | None = None


@dataclass(frozen=True)
class Usage:
    """One metric (CPU or memory) of a resource over the last hour, summed across its instances."""
    latest: float
    peak: float
    at: datetime  # timestamp of the latest data point (UTC)
    unit: str | None  # what the provider says: "cpu" (fractions of a core) and "bytes" today
    instances: int = 1


@dataclass(frozen=True)
class Health:
    icon: str
    label: str
    ok: bool  # False = worth the admin's attention


@dataclass(frozen=True)
class Inventory:
    services: list[ServiceInfo]
    notes: list[str] = field(default_factory=list)  # parts that couldn't be read, shown to the admin


def make_client(api_key: str, timeout: float) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        timeout=timeout,
    )


def _get(client: httpx.Client, path: str, params: dict | None = None):
    try:
        response = client.get(path, params=params)
    except httpx.TimeoutException:
        raise StatusError("El proveedor no respondió a tiempo.") from None
    except httpx.HTTPError:
        raise StatusError("No pude conectar con el proveedor.") from None
    if response.status_code == 401:
        raise StatusError("El proveedor rechazó la API key. ¿Está bien copiada en el .env?")
    if response.status_code == 429:
        raise StatusError("El proveedor limitó las consultas. Probá de nuevo en un minuto.")
    if response.status_code >= 400:
        raise StatusError(f"El proveedor respondió {response.status_code}.")
    try:
        return response.json()
    except ValueError:
        raise StatusError("El proveedor devolvió una respuesta inesperada.") from None


def _list(client: httpx.Client, path: str, limit: int = MAX_ITEMS) -> list:
    data = _get(client, path, {"limit": limit})
    if not isinstance(data, list):
        raise StatusError("El proveedor devolvió una respuesta inesperada.")
    return data


def list_services(client: httpx.Client) -> list[ServiceInfo]:
    services = []
    for item in _list(client, "/services"):
        try:
            raw = item["service"]
            services.append(ServiceInfo(
                id=raw["id"], name=raw["name"], type=raw.get("type") or "?",
                suspended=raw.get("suspended") == "suspended",
                dashboard_url=raw.get("dashboardUrl"),
                region=(raw.get("serviceDetails") or {}).get("region"),
            ))
        except (KeyError, TypeError, AttributeError):
            continue  # skip an entry with an unexpected shape instead of failing the whole list
    return services


def _list_databases(client: httpx.Client, path: str, wrapper: str, type_name: str) -> list[ServiceInfo]:
    databases = []
    for item in _list(client, path):
        try:
            raw = item[wrapper]
            databases.append(ServiceInfo(
                id=raw["id"], name=raw["name"], type=type_name,
                suspended=raw.get("suspended") == "suspended",
                status=raw.get("status") or "unknown",
                dashboard_url=raw.get("dashboardUrl"), region=raw.get("region"),
                expires_at=raw.get("expiresAt"),
            ))
        except (KeyError, TypeError, AttributeError):
            continue
    return databases


def list_all(client: httpx.Client) -> Inventory:
    """Web services (required) plus Postgres and key-value databases (best effort), sorted by name."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        services = pool.submit(list_services, client)
        postgres = pool.submit(_list_databases, client, "/postgres", "postgres", "postgres")
        key_value = pool.submit(_list_databases, client, "/key-value", "keyValue", "key_value")

    found = list(services.result())  # if this fails there is nothing useful to show: let the error out
    notes = []
    for future, label in ((postgres, "las bases de datos"), (key_value, "las bases key-value")):
        try:
            found += future.result()
        except StatusError as error:
            logger.warning("Could not list %s: %s", label, error)
            notes.append(f"⚠️ No pude leer {label}: {error}")
    return Inventory(sorted(found, key=lambda s: s.name.lower()), notes)


def list_deploys(client: httpx.Client, service_id: str) -> list[DeployInfo]:
    deploys = []
    for item in _list(client, f"/services/{service_id}/deploys", DEPLOYS_PER_SERVICE):
        try:
            raw = item["deploy"]
            deploys.append(DeployInfo(
                status=raw["status"], created_at=raw.get("createdAt"), finished_at=raw.get("finishedAt"),
                commit_message=(raw.get("commit") or {}).get("message"),
            ))
        except (KeyError, TypeError, AttributeError):
            continue
    return sorted(deploys, key=lambda d: d.created_at or "", reverse=True)  # newest first


def fetch_deploys_for(client: httpx.Client, services: list[ServiceInfo]) -> dict[str, list[DeployInfo] | None]:
    """Latest deploys of every web service, in parallel. None = couldn't be read (that service only)."""
    def one(service: ServiceInfo):
        try:
            return service.id, list_deploys(client, service.id)
        except StatusError as error:
            logger.warning("Could not read deploys of %s: %s", service.name, error)
            return service.id, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, [s for s in services if not s.is_database]))


def get_usage(client: httpx.Client, resource_id: str, metric: str, now: datetime | None = None) -> Usage | None:
    """Last hour of `metric` ("cpu" or "memory") for a resource. None = no data points (e.g. a sleeping service)."""
    now = now or datetime.now(timezone.utc)
    stamp = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    data = _get(client, f"/metrics/{metric}", {
        "resource": resource_id, "startTime": stamp(now - METRICS_WINDOW), "endTime": stamp(now),
        "resolutionSeconds": METRICS_RESOLUTION_SECONDS,
    })
    if not isinstance(data, list):
        raise StatusError("El proveedor devolvió una respuesta inesperada.")

    totals: dict[datetime, float] = {}  # one total per timestamp: instances add up
    instances: set = set()
    unit = None
    for series in data:
        if not isinstance(series, dict):
            continue
        unit = unit or series.get("unit")
        for label in series.get("labels") or []:
            if isinstance(label, dict) and label.get("field") == "instance":
                instances.add(label.get("value"))
        for point in series.get("values") or []:
            try:
                moment, value = _parse(point["timestamp"]), float(point["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if moment is not None:
                totals[moment] = totals.get(moment, 0.0) + value
    if not totals:
        return None
    last = max(totals)
    return Usage(latest=totals[last], peak=max(totals.values()), at=last, unit=unit, instances=len(instances) or 1)


def fmt_local(timestamp: str | None) -> str | None:
    """"2026-09-20T18:00:00Z" -> "20/09 15:00" in this machine's own time zone (None if unparseable)."""
    moment = _parse(timestamp)
    return f"{moment.astimezone():%d/%m %H:%M}" if moment else None


def assess(service: ServiceInfo, deploys: list[DeployInfo] | None) -> Health:
    """Turns a service (and its latest deploys, or a database's status) into a one-line verdict.

    A failed or cancelled newer deploy doesn't take a service down: the provider keeps serving the last
    live version, so that case is "up, but needs attention" rather than "down".
    """
    if service.is_database:
        return _assess_database(service)
    if service.suspended:
        return Health("⏸️", "suspendido", False)
    if deploys is None:
        return Health("❓", "no pude leer sus deploys", False)
    if not deploys:
        return Health("⚪", "todavía sin deploys", False)

    newest = deploys[0].status
    has_live = any(d.status == "live" for d in deploys)
    if newest == "live":
        return Health("🟢", "en línea", True)
    if newest in IN_PROGRESS:
        return Health("🟡", "desplegando…" + (" (sigue la versión anterior)" if has_live else ""), True)
    if newest in FAILED or newest == "canceled":
        problem = "falló" if newest in FAILED else "fue cancelado"
        if has_live:
            return Health("🟠", f"en línea, pero el último deploy {problem}", False)
        return Health("🔴", f"caído: el último deploy {problem}", False)
    if newest == "deactivated":
        return Health("⚪", "desactivado", False)
    return Health("⚪", newest, False)


def _assess_database(database: ServiceInfo) -> Health:
    status = database.status
    if database.suspended or status == "suspended":
        return Health("⏸️", "suspendida", False)
    if status == "available":
        days = days_until(database.expires_at)
        if days is not None and days < 0:
            return Health("🔴", "vencida", False)
        if days is not None and days <= EXPIRY_WARNING_DAYS:
            return Health("🟠", f"disponible, pero {expires_in(days)}", False)
        return Health("🟢", "disponible", True)
    if status == "maintenance_scheduled":
        return Health("🟡", "disponible, con mantenimiento programado", True)
    if status in DATABASE_BUSY:
        return Health("🟡", DATABASE_BUSY[status], True)
    if status == "unavailable":
        return Health("🔴", "no disponible", False)
    if status == "recovery_failed":
        return Health("🔴", "falló la recuperación", False)
    return Health("⚪", "estado desconocido", False)


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def ago(timestamp: str | None, now: datetime | None = None) -> str:
    """"2026-09-20T18:00:00Z" -> "hace 1 d". Returns "?" when it can't be parsed."""
    moment = _parse(timestamp)
    if moment is None:
        return "?"
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - moment).total_seconds()))
    if seconds < 90:
        return "hace instantes"
    if seconds < 5400:
        return f"hace {round(seconds / 60)} min"
    if seconds < 129600:
        return f"hace {round(seconds / 3600)} h"
    return f"hace {round(seconds / 86400)} d"


def days_until(timestamp: str | None, now: datetime | None = None) -> int | None:
    """Whole days from now until the timestamp (negative if it already passed); None if unknown."""
    moment = _parse(timestamp)
    if moment is None:
        return None
    return (moment - (now or datetime.now(timezone.utc))).days


def expires_in(days: int) -> str:
    return "vence hoy" if days == 0 else f"vence en {days} d"
