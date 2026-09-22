"""Turns `systemctl show` and journal output into the short status report of `!service -L <name>`.

Pure functions (no I/O, `now` is passed in) so every format can be tested with fixed values.
"""

import re
from dataclasses import dataclass
from datetime import datetime

# systemd's way of saying "no value": text for unset properties, max uint64 for unset counters
_UNSET = {"", "[not set]", "18446744073709551615"}
_TIMESTAMP_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})")
_TIMESTAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})")
_CONSUMED_CPU = re.compile(r"Consumed (.+?) CPU time")
_CONSUMED_MEMORY = re.compile(r"([\d.]+)\s*([BKMGT]) memory peak")
_DURATION_PART = re.compile(r"([\d.]+)\s*(us|ms|s|min|h|d)")
_DURATION_UNITS = {"us": 1e-6, "ms": 1e-3, "s": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0}
_BYTE_UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


@dataclass(frozen=True)
class Show:
    """The fields of `systemctl show` we use. None = unknown or not set."""
    active_state: str | None = None
    active_enter: datetime | None = None
    active_exit: datetime | None = None
    inactive_enter: datetime | None = None
    main_pid: int | None = None
    memory_current: int | None = None
    memory_peak: int | None = None
    cpu_nsec: int | None = None
    restarts: int | None = None
    result: str | None = None
    exit_status: int | None = None


@dataclass(frozen=True)
class LastUsage:
    """What systemd wrote to the journal when the service last stopped."""
    cpu_seconds: float | None = None
    memory_peak: int | None = None
    stopped_at: datetime | None = None  # the journal line's own time; only if it was requested with dates


def _int(props: dict[str, str], key: str) -> int | None:
    value = props.get(key, "").strip()
    return int(value) if value not in _UNSET and value.isdigit() else None


def _timestamp(props: dict[str, str], key: str) -> datetime | None:
    """"Mon 2026-09-21 19:14:09 -03" -> naive local datetime. The weekday and zone are ignored on purpose:
    the weekday is localised ("lun") and the zone is the machine's own, the same one `now` uses."""
    match = _TIMESTAMP.search(props.get(key, ""))
    return datetime(*map(int, match.groups())) if match else None


def parse_show(output: str) -> Show:
    props = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    pid = _int(props, "MainPID")
    return Show(
        active_state=props.get("ActiveState") or None,
        active_enter=_timestamp(props, "ActiveEnterTimestamp"),
        active_exit=_timestamp(props, "ActiveExitTimestamp"),
        inactive_enter=_timestamp(props, "InactiveEnterTimestamp"),
        main_pid=pid or None,
        memory_current=_int(props, "MemoryCurrent"),
        memory_peak=_int(props, "MemoryPeak"),
        cpu_nsec=_int(props, "CPUUsageNSec"),
        restarts=_int(props, "NRestarts"),
        result=props.get("Result") or None,
        exit_status=_int(props, "ExecMainStatus"),
    )


def parse_consumed(output: str) -> LastUsage | None:
    """The newest "Consumed 1.794s CPU time, 34.8M memory peak, 0B memory swap peak." line, if any."""
    line = next((l for l in reversed(output.splitlines()) if "Consumed" in l), None)
    if line is None:
        return None
    stopped_at = None
    if match := _TIMESTAMP_ISO.match(line):  # `journalctl -o short-iso`: "2026-09-21T19:31:22-0300 host systemd[1]: ..."
        stopped_at = datetime(*map(int, match.groups()))
    cpu = None
    if match := _CONSUMED_CPU.search(line):
        parts = _DURATION_PART.findall(match.group(1))
        cpu = sum(float(number) * _DURATION_UNITS[unit] for number, unit in parts) if parts else None
    memory = None
    if match := _CONSUMED_MEMORY.search(line):
        memory = round(float(match.group(1)) * _BYTE_UNITS[match.group(2)])
    return LastUsage(cpu_seconds=cpu, memory_peak=memory, stopped_at=stopped_at)


def cpu_percent(nsec_before: int, nsec_after: int, seconds: float) -> float | None:
    """CPU use over an interval, as a percentage of ONE core (like `top`); None if it can't be measured."""
    if seconds <= 0 or nsec_after < nsec_before:
        return None
    return (nsec_after - nsec_before) / 1e9 / seconds * 100


# ---- formatting ---------------------------------------------------------------------------------

def fmt_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 1:
        return "menos de 1 s"
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes} min" + (f" {rest} s" if rest else "")
    if seconds < 86400:
        hours, rest = divmod(seconds, 3600)
        return f"{hours} h" + (f" {rest // 60} min" if rest // 60 else "")
    days, rest = divmod(seconds, 86400)
    return f"{days} d" + (f" {rest // 3600} h" if rest // 3600 else "")


def fmt_cpu_seconds(seconds: float) -> str:
    return f"{seconds:.1f} s" if seconds < 60 else fmt_duration(seconds)


def fmt_moment(moment: datetime, now: datetime) -> str:
    return f"{moment:%H:%M:%S}" if moment.date() == now.date() else f"{moment:%d/%m %H:%M:%S}"


def _ago(moment: datetime, now: datetime) -> str:
    return f"hace {fmt_duration((now - moment).total_seconds())}"


def describe(show: Show, usage: LastUsage | None, now: datetime, cpu_now: float | None = None) -> list[str]:
    """The lines that go under the state line ("🟢 name: activo")."""
    if show.active_state == "active":
        return _describe_running(show, now, cpu_now)
    return _describe_stopped(show, usage, now)


def _describe_running(show: Show, now: datetime, cpu_now: float | None) -> list[str]:
    lines = []
    if show.active_enter:
        lines.append(f"⏱️ Activo desde {fmt_moment(show.active_enter, now)} ({_ago(show.active_enter, now)})")
    if show.memory_current is not None:
        peak = f" (pico {fmt_bytes(show.memory_peak)})" if show.memory_peak is not None else ""
        lines.append(f"💾 Memoria: {fmt_bytes(show.memory_current)}{peak}")
    cpu_parts = []
    if cpu_now is not None:
        cpu_parts.append(f"{cpu_now:.1f} % ahora")
    if show.cpu_nsec is not None:
        total = show.cpu_nsec / 1e9
        if show.active_enter and (now - show.active_enter).total_seconds() > 0:
            cpu_parts.append(f"{total / (now - show.active_enter).total_seconds() * 100:.1f} % promedio")
        cpu_parts.append(f"{fmt_cpu_seconds(total)} en total")
    if cpu_parts:
        lines.append("⚙️ CPU: " + " · ".join(cpu_parts))
    extra = []
    if show.main_pid:
        extra.append(f"PID {show.main_pid}")
    if show.restarts is not None:
        extra.append(f"reinicios automáticos: {show.restarts}")
    if extra:
        lines.append("🔁 " + " · ".join(extra))
    return lines


def _describe_stopped(show: Show, usage: LastUsage | None, now: datetime) -> list[str]:
    lines = []
    # systemd forgets the times of some units once stopped: fall back to when the journal recorded the stop
    stopped = show.inactive_enter or (usage.stopped_at if usage else None)
    if stopped:
        lines.append(f"🕒 Detenido desde {fmt_moment(stopped, now)} ({_ago(stopped, now)})")
    if show.result and show.result != "success":
        code = f", código {show.exit_status}" if show.exit_status else ""
        lines.append(f"⚠️ Terminó con error: {show.result}{code}")

    ran = []
    if show.active_enter and show.active_exit and show.active_exit >= show.active_enter:
        ran.append(f"duró {fmt_duration((show.active_exit - show.active_enter).total_seconds())}")
    cpu = show.cpu_nsec / 1e9 if show.cpu_nsec is not None else (usage.cpu_seconds if usage else None)
    if cpu is not None:
        ran.append(f"CPU {fmt_cpu_seconds(cpu)}")
    if usage and usage.memory_peak is not None:
        ran.append(f"memoria pico {fmt_bytes(usage.memory_peak)}")
    if ran:
        lines.append("📊 Última ejecución: " + " · ".join(ran))
    if not lines:
        lines.append("Sin ejecuciones registradas desde el último arranque de la sesión.")
    return lines
