"""Measures this machine's internet speed against Cloudflare's public speed-test endpoints (no account, no key,
nothing installed): latency with tiny requests, download by reading a large stream for a few seconds, upload by
posting chunks for a few seconds. Each phase is capped by time, not by size, so a slow line doesn't make it
run for minutes and a fast one doesn't burn through gigabytes.

Only one test runs at a time: it saturates the connection, and two at once would just measure each other.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx

BASE_URL = "https://speed.cloudflare.com"
DOWNLOAD_BYTES = 25_000_000  # the most one request may ask for (larger ones get a 403)
UPLOAD_CHUNK = b"\0" * 2_000_000
LATENCY_PROBES = 4

_lock = threading.Lock()


class SpeedtestError(Exception):
    """Carries a message that is fine to show in the chat."""


class Busy(SpeedtestError):
    pass


@dataclass(frozen=True)
class SpeedResult:
    latency_ms: float
    download_mbps: float
    upload_mbps: float


def _mbps(bytes_count: int, seconds: float) -> float:
    return bytes_count * 8 / 1_000_000 / seconds if seconds > 0 else 0.0


def measure_latency(client: httpx.Client) -> float:
    """Best (lowest) round trip, in ms, of a few empty requests — the first one also pays for the TLS handshake."""
    times = []
    for _ in range(LATENCY_PROBES):
        started = time.perf_counter()
        client.get(f"{BASE_URL}/__down", params={"bytes": 0}).raise_for_status()
        times.append((time.perf_counter() - started) * 1000)
    return min(times[1:] or times)


def measure_download(client: httpx.Client, seconds: float) -> float:
    """Reads streams of DOWNLOAD_BYTES one after another until the time is up (a fast line finishes more than one)."""
    received = 0
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        with client.stream("GET", f"{BASE_URL}/__down", params={"bytes": DOWNLOAD_BYTES}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(65536):
                received += len(chunk)
                if time.perf_counter() - started >= seconds:
                    break
    return _mbps(received, time.perf_counter() - started)


def measure_upload(client: httpx.Client, seconds: float) -> float:
    """Posts chunks one after another until the time is up; each response confirms the chunk really got there."""
    sent = 0
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        client.post(f"{BASE_URL}/__up", content=UPLOAD_CHUNK).raise_for_status()
        sent += len(UPLOAD_CHUNK)
    return _mbps(sent, time.perf_counter() - started)


def run(phase_seconds: float = 6.0, client_factory: Callable[[], httpx.Client] | None = None) -> SpeedResult:
    if not _lock.acquire(blocking=False):
        raise Busy("Ya hay una prueba de velocidad en curso, esperá a que termine.")
    try:
        client = client_factory() if client_factory else httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
        with client:
            try:
                latency = measure_latency(client)
                download = measure_download(client, phase_seconds)
                upload = measure_upload(client, phase_seconds)
            except httpx.TimeoutException:
                raise SpeedtestError("La prueba de velocidad se quedó sin respuesta (timeout).") from None
            except httpx.HTTPError:
                raise SpeedtestError("No pude correr la prueba de velocidad: no hay conexión con el servidor de pruebas.") from None
        return SpeedResult(latency, download, upload)
    finally:
        _lock.release()
