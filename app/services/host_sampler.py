"""Background sampler: every N seconds it records the machine's CPU, memory, temperature and battery in the database.

It only reads /proc and /sys (no commands, nothing from chats). Failures are logged and skipped: a missed sample is fine.
"""

import logging
import threading
import time
from typing import Callable

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import HostSample
from . import host_status

logger = logging.getLogger("tox.host_sampler")
PRUNE_EVERY_SAMPLES = 60


class HostSampler:
    def __init__(self, session_factory: Callable[[], Session], interval_seconds: int, retention_days: int,
                 sources: host_status.Sources | None = None, clock: Callable[[], float] = time.time) -> None:
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._retention_seconds = retention_days * 86400
        self._sources = sources or host_status.Sources()
        self._clock = clock
        self._previous_cpu: tuple[int, int] | None = None
        self._taken = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample_once(self) -> HostSample:
        """Reads the vitals and stores one row."""
        src = self._sources
        cpu_times = host_status.parse_cpu_times(src.read("/proc/stat") or "")
        cpu = host_status.cpu_percent(self._previous_cpu, cpu_times) if self._previous_cpu and cpu_times else None
        self._previous_cpu = cpu_times or self._previous_cpu  # the first sample has nothing to compare with
        memory = host_status.parse_meminfo(src.read("/proc/meminfo") or "")
        temperature = host_status.parse_temperature([src.read(p) or "" for p in src.glob("/sys/class/thermal/thermal_zone*/temp")])
        battery = host_status.read_battery(src)
        row = HostSample(
            taken_at=self._clock(), cpu_percent=cpu,
            memory_used_percent=(memory.total - memory.available) / memory.total * 100 if memory and memory.total else None,
            temperature_c=temperature,
            battery_percent=battery.percent if battery else None, battery_status=battery.status if battery else None,
        )
        with self._session_factory() as db:
            db.add(row)
            self._taken += 1
            if self._taken % PRUNE_EVERY_SAMPLES == 1:
                db.execute(delete(HostSample).where(HostSample.taken_at < row.taken_at - self._retention_seconds))
            db.commit()
        return row

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:  # a missed sample must never kill the sampler
                logger.exception("Could not record a host sample")
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread is None and self._interval > 0:
            self._thread = threading.Thread(target=self._run, name="host-sampler", daemon=True)
            self._thread.start()
            logger.info("Host sampler started (every %ds, keeping %d days)", self._interval, self._retention_seconds // 86400)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
