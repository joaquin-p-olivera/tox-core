"""Background monitor: every ALERT_CHECK_INTERVAL_SECONDS it checks the services listed in ALERT_SERVICES
(local services from SERVICES_FILE, matched by name) and queues a PendingAlert the moment one changes state,
never on every check: down -> one alert; later, recovered -> one more; still down/still fine in between -> nothing.

Only local services are supported today (that's all ALERT_SERVICES is meant to hold for now), but the state
machine below doesn't care where a service comes from, so a "render:<name>" kind can be added later without
changing this part.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AlertState, PendingAlert
from . import service_control, systemd_info

logger = logging.getLogger("tox.alert_monitor")


def check_local(name: str, catalog: service_control.Catalog, timeout: int) -> tuple[bool, str]:
    """(ok, label) for one local service, judged the same way `!service -L <name>` reads it."""
    definition = catalog.services.get(name)
    if definition is None:
        return False, "no está configurado en services.json"
    if "status" not in definition.commands:
        return False, "no tiene una acción de estado configurada"
    result = service_control.run_action(definition, "status", timeout)
    if result.timed_out:
        return False, "no respondió a tiempo"
    if result.error:
        return False, result.error
    word = result.output.strip().lower()
    if result.exit_code == 0 and word in ("active", ""):
        return True, "activo"
    return False, word or f"código de salida {result.exit_code}"


def _ran_ok(result: service_control.RunResult) -> bool:
    return not result.timed_out and result.error is None and result.exit_code == 0 and bool(result.output)


def detail_lines(definition: service_control.ServiceDef, timeout: int) -> list[str]:
    """Extra lines (since when up/down, memory, CPU...) for a service that defines `info` (and `last_usage`),
    the same ones `!service -L <name>` shows. Only run right before sending an alert: no point paying for
    the extra subprocess calls on every check when nothing changed."""
    commands = definition.commands
    if "info" not in commands:
        return []
    info = service_control.run_action(definition, "info", timeout)
    if not _ran_ok(info):
        return []
    show = systemd_info.parse_show(info.output)
    usage = None
    if "last_usage" in commands:
        journal = service_control.run_action(definition, "last_usage", timeout)
        if _ran_ok(journal):
            usage = systemd_info.parse_consumed(journal.output)
    return systemd_info.describe(show, usage, datetime.now())


class AlertMonitor:
    def __init__(self, session_factory: Callable[[], Session], settings: Settings,
                 clock: Callable[[], float] = time.time) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _destinations(self) -> list[tuple[str, str]]:
        settings = self._settings
        return ([("whatsapp", jid) for jid in settings.alert_whatsapp_group_jids] +
                [("telegram", chat_id) for chat_id in settings.alert_telegram_chat_ids])

    def check_once(self) -> None:
        services = self._settings.alert_services_list
        if not services:
            return
        catalog = service_control.load_catalog(self._settings.SERVICES_FILE)
        timeout = self._settings.SERVICE_COMMAND_TIMEOUT_SECONDS
        destinations = self._destinations()
        with self._session_factory() as db:
            for name in services:
                key = f"local:{name}"
                ok, label = check_local(name, catalog, timeout)
                previous = db.get(AlertState, key)
                # Nothing known yet: assume it was fine, so a service that's already healthy on the very
                # first check doesn't trigger a "recovered" message out of nowhere (one already down does).
                was_ok = previous.ok if previous else True
                if previous is None:
                    db.add(AlertState(key=key, ok=ok, label=label))
                else:
                    previous.ok = ok
                    previous.label = label
                if ok == was_ok:
                    continue
                text = (f"[ALERTA] 🟢 {name}: se recuperó ({label})." if ok
                        else f"[ALERTA] 🔴 {name}: caído, revisar ({label}).")
                definition = catalog.services.get(name)
                if definition is not None:
                    lines = detail_lines(definition, timeout)
                    if lines:
                        text += "\n" + "\n".join(lines)
                for platform, chat_id in destinations:
                    admins = self._settings.admin_ids_for(platform)
                    # Tag every admin of that platform, same {@0}, {@1}... convention as a normal Reply.
                    tags = "\n" + " ".join(f"{{@{i}}}" for i in range(len(admins))) if admins else ""
                    db.add(PendingAlert(platform=platform, chat_id=chat_id, text=text + tags, mentions=",".join(admins)))
                logger.warning("ALERT %s -> %s (%s)", name, "recovered" if ok else "down", label)
            db.commit()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception:  # a missed check must never kill the monitor
                logger.exception("Could not run the alert check")
            self._stop.wait(self._settings.ALERT_CHECK_INTERVAL_SECONDS)

    def start(self) -> None:
        if self._thread is None and self._settings.alert_services_list:
            self._thread = threading.Thread(target=self._run, name="alert-monitor", daemon=True)
            self._thread.start()
            logger.info("Alert monitor started (every %ds, watching: %s)",
                        self._settings.ALERT_CHECK_INTERVAL_SECONDS, ", ".join(self._settings.alert_services_list))

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
