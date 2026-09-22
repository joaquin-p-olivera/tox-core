"""Peaks and battery trends computed from the samples the API records (see host_sampler.py).

Everything here is pure (samples in, numbers out) so it can be tested with hand-made series.
"""

from dataclasses import dataclass, field
from datetime import datetime

from . import systemd_info
from .host_status import Battery

WINDOW_SECONDS = 24 * 3600
MAX_GAP_SECONDS = 600  # a longer hole means the API was down: don't count it as "on battery" or as a trend
MIN_TREND_SECONDS = 600
MIN_TREND_PERCENT = 2.0
STALLED_CHARGE_SECONDS = 3600
DRAIN_WINDOW_SECONDS = 600


@dataclass(frozen=True)
class Sample:
    t: float
    cpu: float | None = None
    memory: float | None = None
    temperature: float | None = None
    battery: float | None = None
    status: str | None = None


@dataclass(frozen=True)
class Peak:
    value: float
    at: float


@dataclass(frozen=True)
class History:
    count: int = 0
    span_seconds: float = 0.0
    cpu: Peak | None = None
    memory: Peak | None = None
    temperature: Peak | None = None
    battery_min: Peak | None = None
    battery_max: Peak | None = None
    on_battery_seconds: float = 0.0
    peak_drain: Peak | None = None  # value = % per hour
    trend_percent_per_hour: float | None = None  # negative while discharging, positive while charging
    trend_seconds: float = 0.0
    stalled_since: float | None = None  # plugged in and "charging", but the level has not moved
    notes: list[str] = field(default_factory=list)


def _peak(samples: list[Sample], attribute: str, pick=max) -> Peak | None:
    points = [(getattr(s, attribute), s.t) for s in samples if getattr(s, attribute) is not None]
    if not points:
        return None
    value, at = pick(points, key=lambda point: point[0])
    return Peak(value, at)


def _contiguous_tail(samples: list[Sample]) -> list[Sample]:
    """The trailing run of samples with the same battery status and no hole bigger than MAX_GAP_SECONDS."""
    with_battery = [s for s in samples if s.battery is not None and s.status]
    if not with_battery:
        return []
    tail = [with_battery[-1]]
    for sample in reversed(with_battery[:-1]):
        if sample.status != tail[0].status or tail[0].t - sample.t > MAX_GAP_SECONDS:
            break
        tail.insert(0, sample)
    return tail


def _peak_drain(samples: list[Sample]) -> Peak | None:
    """Fastest battery loss, in % per hour, over windows of at least DRAIN_WINDOW_SECONDS spent discharging."""
    best: Peak | None = None
    run: list[Sample] = []
    runs = []
    for sample in [s for s in samples if s.battery is not None]:
        if sample.status == "Discharging" and (not run or sample.t - run[-1].t <= MAX_GAP_SECONDS):
            run.append(sample)
        else:
            if run:
                runs.append(run)
            run = [sample] if sample.status == "Discharging" else []
    if run:
        runs.append(run)
    for chunk in runs:
        j = 0
        for i in range(len(chunk)):
            while j < len(chunk) and chunk[j].t - chunk[i].t < DRAIN_WINDOW_SECONDS:
                j += 1
            if j >= len(chunk):
                break
            lost = chunk[i].battery - chunk[j].battery
            if lost >= 1:
                rate = lost / ((chunk[j].t - chunk[i].t) / 3600)
                if best is None or rate > best.value:
                    best = Peak(rate, chunk[j].t)
    return best


def analyze(samples: list[Sample], now: float) -> History:
    """Peaks over the last 24 h and the current battery trend. Samples from the future (a clock that jumped) are ignored."""
    window = sorted((s for s in samples if now - WINDOW_SECONDS <= s.t <= now + 60), key=lambda s: s.t)
    if not window:
        return History()

    on_battery = sum(b.t - a.t for a, b in zip(window, window[1:])
                     if a.status == "Discharging" and 0 < b.t - a.t <= MAX_GAP_SECONDS)
    tail = _contiguous_tail(window)
    trend, trend_seconds, stalled = None, 0.0, None
    if len(tail) >= 2 and tail[-1].t - tail[0].t >= MIN_TREND_SECONDS:
        seconds = tail[-1].t - tail[0].t
        change = tail[-1].battery - tail[0].battery
        if abs(change) >= MIN_TREND_PERCENT and (change < 0) == (tail[0].status == "Discharging"):
            trend, trend_seconds = change / (seconds / 3600), seconds
        if tail[0].status in ("Charging", "Full") and abs(change) < 1 and seconds >= STALLED_CHARGE_SECONDS \
                and tail[-1].battery < 100:
            stalled = tail[0].t
    return History(
        count=len(window), span_seconds=window[-1].t - window[0].t,
        cpu=_peak(window, "cpu"), memory=_peak(window, "memory"), temperature=_peak(window, "temperature"),
        battery_min=_peak(window, "battery", min), battery_max=_peak(window, "battery"),
        on_battery_seconds=on_battery, peak_drain=_peak_drain(window),
        trend_percent_per_hour=trend, trend_seconds=trend_seconds, stalled_since=stalled,
    )


def _at(timestamp: float, now: datetime) -> str:
    return systemd_info.fmt_moment(datetime.fromtimestamp(timestamp), now)


def battery_lines(history: History, battery: Battery | None, now: datetime) -> list[str]:
    """Battery trend (autonomy or time to full) and its range over the recorded history. Empty without a battery or history."""
    if battery is None or history.count == 0:
        return []
    lines = []
    if history.trend_percent_per_hour is not None and history.trend_percent_per_hour < 0:
        rate = -history.trend_percent_per_hour
        lines.append(f"⏳ Autonomía: ≈ {systemd_info.fmt_duration(battery.percent / rate * 3600)} al ritmo de las últimas "
                     f"{systemd_info.fmt_duration(history.trend_seconds)} ({rate:.0f} %/h)")
    elif history.trend_percent_per_hour is not None and history.trend_percent_per_hour > 0:
        rate = history.trend_percent_per_hour
        lines.append(f"⏳ Carga completa en ≈ {systemd_info.fmt_duration(max(0.0, 100 - battery.percent) / rate * 3600)} "
                     f"al ritmo de las últimas {systemd_info.fmt_duration(history.trend_seconds)} ({rate:.0f} %/h)")
    elif history.stalled_since is not None:
        lines.append(f"ℹ️ Enchufada, pero sin subir de {battery.percent:.0f} % desde las {_at(history.stalled_since, now)} "
                     f"(¿límite de carga de la notebook?)")
    parts = []
    if history.battery_min and history.battery_max and history.battery_min.value != history.battery_max.value:
        parts.append(f"mín. {history.battery_min.value:.0f} % ({_at(history.battery_min.at, now)}) · "
                     f"máx. {history.battery_max.value:.0f} % ({_at(history.battery_max.at, now)})")
    if history.on_battery_seconds >= 60:
        parts.append(f"{systemd_info.fmt_duration(history.on_battery_seconds)} con batería")
    if history.peak_drain:
        parts.append(f"pico de descarga {history.peak_drain.value:.0f} %/h ({_at(history.peak_drain.at, now)})")
    if parts:
        lines.append(f"🔋 Historial ({systemd_info.fmt_duration(history.span_seconds)}): " + " · ".join(parts))
    return lines


def peak_lines(history: History, now: datetime) -> list[str]:
    """CPU / memory / temperature peaks over the recorded history (up to 24 h), or a note when there is none."""
    if history.count == 0:
        return ["📈 Historial: todavía sin datos (la API empieza a registrar al arrancar)"]
    peaks = []
    if history.cpu:
        peaks.append(f"CPU {history.cpu.value:.0f} % ({_at(history.cpu.at, now)})")
    if history.memory:
        peaks.append(f"memoria {history.memory.value:.0f} % usada ({_at(history.memory.at, now)})")
    if history.temperature:
        peaks.append(f"{history.temperature.value:.0f} °C ({_at(history.temperature.at, now)})")
    span = systemd_info.fmt_duration(history.span_seconds)
    if not peaks:
        return [f"📈 Historial: recién empieza ({history.count} muestras)"]
    note = " — historial recién empezando" if history.count < 10 else ""
    return [f"📈 Picos ({span} de datos): " + " · ".join(peaks) + note]
