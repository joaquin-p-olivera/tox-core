"""Health of the machine the API runs on, for `!service -L pc`. Read-only: it only reads /proc, /sys and a few fixed
system commands. Nothing typed in a chat ever reaches a command line.

The parsers are pure functions over text so they can be tested with fixed samples; `read_snapshot` does the I/O
through a `Sources` object that tests replace with a fake.
"""

import glob
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import systemd_info

# ---- thresholds for the "needs a look" flag ------------------------------------------------------
DISK_WARN_PERCENT = 85
MEMORY_AVAILABLE_WARN_PERCENT = 10
LOAD_WARN_PER_CORE = 1.5
TEMPERATURE_WARN_C = 85
BATTERY_LOW_PERCENT = 20
BATTERY_HEALTH_WARN_PERCENT = 70
TOP_PROCESSES = 3
MAX_DISKS = 4
REAL_FILESYSTEMS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "ntfs", "ntfs3", "fuseblk", "exfat"}
_SESSION_ID = re.compile(r"^[A-Za-z0-9]{1,16}$")
_SESSION_TYPES = {"x11": "gráfica", "wayland": "gráfica", "mir": "gráfica", "tty": "consola", "unspecified": "sesión"}


# ---- data ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Memory:
    total: int
    available: int
    swap_total: int = 0
    swap_free: int = 0


@dataclass(frozen=True)
class Disk:
    mount: str
    total: int
    used: int
    free: int


@dataclass(frozen=True)
class Session:
    user: str
    kind: str  # Spanish label: gráfica / consola / sesión
    remote: bool
    started_at: datetime | None  # local time; derived from the boot time, see parse_session


@dataclass(frozen=True)
class Battery:
    percent: int
    status: str  # Charging / Discharging / Full / ...
    plugged_in: bool | None = None  # is the charger connected (None = unknown)
    health_percent: int | None = None  # full capacity now vs. when new
    full_wh: float | None = None  # what it holds today
    cycles: int | None = None  # only when the hardware really reports it


@dataclass(frozen=True)
class CpuInfo:
    model: str
    cores: int  # physical
    threads: int  # logical (more than cores when there is hyper-threading)
    per_core_percent: list[float] = field(default_factory=list)
    frequency_mhz: list[float] = field(default_factory=list)  # current, per logical CPU
    max_frequency_mhz: float | None = None
    throttle_events: int = 0  # times the CPU slowed itself down because of heat, since boot


@dataclass(frozen=True)
class Snapshot:
    os_name: str = "Linux"
    kernel: str = ""
    booted_at: datetime | None = None
    cpu_percent: float | None = None
    cores: int = 1
    cpu_info: CpuInfo | None = None
    load: tuple[float, float, float] | None = None
    temperature_c: float | None = None
    memory: Memory | None = None
    disks: list[Disk] = field(default_factory=list)
    battery: Battery | None = None
    sessions: list[Session] = field(default_factory=list)
    top_memory: list[tuple[str, float]] = field(default_factory=list)
    failed_units: list[str] = field(default_factory=list)
    reboot_required: bool = False


# ---- parsers (pure) -----------------------------------------------------------------------------

def parse_os_release(text: str) -> str:
    match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.MULTILINE)
    return match.group(1) if match else "Linux"


def parse_uptime_seconds(text: str) -> float | None:
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def parse_loadavg(text: str) -> tuple[float, float, float] | None:
    try:
        one, five, fifteen = (float(x) for x in text.split()[:3])
        return one, five, fifteen
    except ValueError:
        return None


def parse_cpu_times(text: str) -> tuple[int, int] | None:
    """(busy, total) jiffies from the first "cpu" line of /proc/stat."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            try:
                values = [int(x) for x in line.split()[1:]]
            except ValueError:
                return None
            if len(values) < 5:
                return None
            idle = values[3] + values[4]  # idle + iowait
            return sum(values[:8]) - idle, sum(values[:8])
    return None


def parse_per_cpu_times(text: str) -> list[tuple[int, int]]:
    """(busy, total) per logical CPU from the "cpu0", "cpu1"... lines of /proc/stat, in CPU order."""
    found = []
    for line in text.splitlines():
        match = re.match(r"cpu(\d+) ", line)
        if not match:
            continue
        try:
            values = [int(x) for x in line.split()[1:]]
        except ValueError:
            continue
        if len(values) >= 5:
            found.append((int(match.group(1)), sum(values[:8]) - values[3] - values[4], sum(values[:8])))
    return [(busy, total) for _, busy, total in sorted(found)]


def per_core_percent(before: list[tuple[int, int]], after: list[tuple[int, int]]) -> list[float]:
    return [p for a, b in zip(before, after) if (p := cpu_percent(a, b)) is not None]


def clean_cpu_model(name: str) -> str:
    """"Intel(R) Celeron(R) CPU  N3060  @ 1.60GHz" -> "Intel Celeron N3060"."""
    name = re.sub(r"\((R|TM|tm|r)\)", "", name).split("@")[0]
    return re.sub(r"\s+", " ", re.sub(r"\bCPU\b", "", name)).strip()


def parse_cpuinfo(text: str) -> tuple[str, int, int] | None:
    """(model, physical cores, logical CPUs) from /proc/cpuinfo."""
    blocks = [b for b in text.split("\n\n") if "processor" in b]
    if not blocks:
        return None
    field_of = lambda block, key: next((l.split(":", 1)[1].strip() for l in block.splitlines() if l.startswith(key)), None)  # noqa: E731
    model = clean_cpu_model(field_of(blocks[0], "model name") or "") or "CPU"
    sockets = {field_of(b, "physical id") or "0" for b in blocks}
    try:
        cores = int(field_of(blocks[0], "cpu cores")) * len(sockets)
    except (TypeError, ValueError):
        cores = len(blocks)
    return model, max(1, min(cores, len(blocks))), len(blocks)


def _cpu_index(path: str) -> int:
    match = re.search(r"cpu(\d+)", path)
    return int(match.group(1)) if match else 0


def cpu_percent(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    busy, total = after[0] - before[0], after[1] - before[1]
    return None if total <= 0 or busy < 0 else busy / total * 100


def parse_meminfo(text: str) -> Memory | None:
    kib = {m.group(1): int(m.group(2)) * 1024 for m in re.finditer(r"^(\w+):\s+(\d+)\s*kB", text, re.MULTILINE)}
    if "MemTotal" not in kib or "MemAvailable" not in kib:
        return None
    return Memory(kib["MemTotal"], kib["MemAvailable"], kib.get("SwapTotal", 0), kib.get("SwapFree", 0))


def parse_temperature(millidegrees: list[str]) -> float | None:
    """The hottest plausible sensor, in °C (0 or absurd readings are dropped: some sensors report placeholders)."""
    values = []
    for raw in millidegrees:
        try:
            celsius = int(raw.strip()) / 1000
        except ValueError:
            continue
        if 1 <= celsius <= 150:
            values.append(celsius)
    return max(values) if values else None


def parse_mounts(text: str) -> list[tuple[str, str]]:
    """Real disks only (not tmpfs, snap loops, boot partitions...), each mount point once."""
    seen, mounts = set(), []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] not in REAL_FILESYSTEMS:
            continue
        mount = parts[1].replace("\\040", " ")
        if mount in seen or mount.startswith(("/boot", "/snap", "/var/snap")):
            continue
        seen.add(mount)
        mounts.append((mount, parts[2]))
    return mounts


def parse_session_ids(text: str) -> list[str]:
    ids = [line.split()[0] for line in text.splitlines() if line.split()]
    return [i for i in ids if _SESSION_ID.match(i)]  # they end up on a command line: only plain ids


def parse_session(text: str, booted_at: datetime | None) -> Session | None:
    """`loginctl show-session` output. The wall-clock `Timestamp` is NOT used: on a machine whose hardware clock is in
    local time it is recorded hours off, so the start is placed as boot time + monotonic offset instead."""
    props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if props.get("Class", "user") != "user" or not props.get("Name"):
        return None
    started = None
    if booted_at is not None and props.get("TimestampMonotonic", "").isdigit() and int(props["TimestampMonotonic"]) > 0:
        started = booted_at + timedelta(microseconds=int(props["TimestampMonotonic"]))
    remote = props.get("Remote") == "yes"
    return Session(user=props["Name"], kind="remota" if remote else _SESSION_TYPES.get(props.get("Type", ""), "sesión"),
                   remote=remote, started_at=started)


def parse_memory_by_process(text: str, top: int = TOP_PROCESSES) -> list[tuple[str, float]]:
    """`ps -eo pmem=,comm=` -> the biggest memory users by program NAME, summed (Firefox is dozens of processes).
    Only the name, never the arguments: those can contain secrets."""
    totals: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            totals[parts[1].strip()] = totals.get(parts[1].strip(), 0.0) + float(parts[0])
        except ValueError:
            continue
    return sorted(totals.items(), key=lambda item: -item[1])[:top]


def parse_failed_units(text: str, ignored: set[str] = frozenset()) -> list[str]:
    names = []
    for line in text.splitlines():
        parts = line.split()
        if parts and re.fullmatch(r"[\w@.:\\-]+\.(service|socket|timer|mount|target|path)", parts[0]) and parts[0] not in ignored:
            names.append(parts[0])
    return names


def _number(text: str | None) -> float | None:
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def parse_battery(files: dict[str, str | None], plugged_in: bool | None = None) -> Battery | None:
    """`files` are the contents of /sys/class/power_supply/BAT*/<name>. Units in sysfs are micro-something."""
    percent = _number(files.get("capacity"))
    if percent is None:
        return None
    # Health = what it can hold now / what it held when new. Some batteries report energy (µWh), others charge (µAh).
    full = _number(files.get("energy_full")) or _number(files.get("charge_full"))
    design = _number(files.get("energy_full_design")) or _number(files.get("charge_full_design"))
    health = round(full / design * 100) if full and design else None
    if _number(files.get("energy_full")):
        wh = full / 1e6
    elif full and (volts := _number(files.get("voltage_min_design")) or _number(files.get("voltage_now"))):
        wh = full * volts / 1e12  # µAh × µV
    else:
        wh = None
    cycles = _number(files.get("cycle_count"))
    return Battery(percent=int(percent), status=(files.get("status") or "").strip() or "Unknown", plugged_in=plugged_in,
                   health_percent=health, full_wh=wh, cycles=int(cycles) if cycles and cycles > 0 else None)


# ---- I/O ----------------------------------------------------------------------------------------

class Sources:
    """Everything that touches the machine. Tests pass a fake with the same methods."""

    def read(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as file:
                return file.read()
        except OSError:
            return None

    def glob(self, pattern: str) -> list[str]:
        return sorted(glob.glob(pattern))

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def run(self, argv: list[str], timeout: float = 4.0) -> str | None:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout,
                                  stdin=subprocess.DEVNULL, shell=False, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout

    def disk_usage(self, path: str) -> tuple[int, int, int] | None:
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            return None
        return usage.total, usage.used, usage.free

    def cores(self) -> int:
        return os.cpu_count() or 1

    def kernel(self) -> str:
        return os.uname().release


def read_cpu_info(src: "Sources", per_core: list[float]) -> CpuInfo | None:
    parsed = parse_cpuinfo(src.read("/proc/cpuinfo") or "")
    if parsed is None:
        return None
    model, cores, threads = parsed
    def numbers(pattern: str) -> list[float]:
        found = []
        for path in sorted(src.glob(pattern), key=_cpu_index):
            try:
                found.append(float((src.read(path) or "").strip()))
            except ValueError:
                pass
        return found
    current = [khz / 1000 for khz in numbers("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq")]
    maximum = [khz / 1000 for khz in numbers("/sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq")]
    throttled = int(sum(numbers("/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count")))
    return CpuInfo(model, cores, threads, per_core, current, max(maximum) if maximum else None, throttled)


def read_battery(src: "Sources") -> Battery | None:
    names = ("capacity", "status", "energy_full", "energy_full_design", "charge_full", "charge_full_design",
             "voltage_min_design", "voltage_now", "cycle_count")
    mains = [(src.read(f"{p}/type") or "").strip() == "Mains" and (src.read(f"{p}/online") or "").strip() == "1"
             for p in src.glob("/sys/class/power_supply/*") if (src.read(f"{p}/type") or "").strip() == "Mains"]
    plugged = any(mains) if mains else None
    for path in src.glob("/sys/class/power_supply/BAT*"):
        if battery := parse_battery({name: src.read(f"{path}/{name}") for name in names}, plugged):
            return battery
    return None


def read_snapshot(now: datetime, sources: Sources | None = None, sample_seconds: float = 1.0,
                  ignored_units: set[str] = frozenset()) -> Snapshot:
    """Reads the machine. The slow parts (the CPU sample and the commands) run in parallel."""
    src = sources or Sources()
    uptime = parse_uptime_seconds(src.read("/proc/uptime") or "")
    booted_at = now - timedelta(seconds=uptime) if uptime is not None else None

    def cpu() -> tuple[float | None, list[float]]:
        first_text = src.read("/proc/stat") or ""
        time.sleep(sample_seconds)
        second_text = src.read("/proc/stat") or ""
        first, second = parse_cpu_times(first_text), parse_cpu_times(second_text)
        total = cpu_percent(first, second) if first and second else None
        return total, per_core_percent(parse_per_cpu_times(first_text), parse_per_cpu_times(second_text))

    def sessions() -> list[Session]:
        listing = src.run(["loginctl", "list-sessions", "--no-legend"]) or ""
        found = []
        for session_id in parse_session_ids(listing):
            shown = src.run(["loginctl", "show-session", session_id, "-p", "Name", "-p", "Type", "-p", "Remote",
                             "-p", "Class", "-p", "TimestampMonotonic"])
            if shown and (session := parse_session(shown, booted_at)):
                found.append(session)
        return found

    with ThreadPoolExecutor(max_workers=4) as pool:
        cpu_job = pool.submit(cpu)
        sessions_job = pool.submit(sessions)
        memory_job = pool.submit(src.run, ["ps", "-eo", "pmem=,comm="])
        failed_jobs = [pool.submit(src.run, ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"]),
                       pool.submit(src.run, ["systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager"])]

    disks = []
    for mount, _ in parse_mounts(src.read("/proc/mounts") or "")[:MAX_DISKS]:
        if usage := src.disk_usage(mount):
            disks.append(Disk(mount, *usage))
    battery = read_battery(src)
    failed = []
    for job in failed_jobs:
        failed += parse_failed_units(job.result() or "", ignored_units)

    cpu_total, per_core = cpu_job.result()
    return Snapshot(
        os_name=parse_os_release(src.read("/etc/os-release") or ""), kernel=src.kernel(), booted_at=booted_at,
        cpu_percent=cpu_total, cores=src.cores(), cpu_info=read_cpu_info(src, per_core),
        load=parse_loadavg(src.read("/proc/loadavg") or ""),
        temperature_c=parse_temperature([src.read(p) or "" for p in src.glob("/sys/class/thermal/thermal_zone*/temp")]),
        memory=parse_meminfo(src.read("/proc/meminfo") or ""), disks=disks, battery=battery,
        sessions=sessions_job.result(), top_memory=parse_memory_by_process(memory_job.result() or ""),
        failed_units=failed, reboot_required=src.exists("/var/run/reboot-required"),
    )


# ---- report -------------------------------------------------------------------------------------

def _since(moment: datetime, now: datetime) -> str:
    return f"{systemd_info.fmt_moment(moment, now)} (hace {systemd_info.fmt_duration((now - moment).total_seconds())})"


def _percent(part: int, whole: int) -> int:
    return round(part / whole * 100) if whole else 0


BATTERY_STATUS = {"Charging": "cargando", "Discharging": "descargándose", "Full": "llena", "Not charging": "conectada, sin cargar"}


def _cpu_lines(cpu: CpuInfo) -> list[str]:
    count = f"{cpu.cores} núcleos" + (f" / {cpu.threads} hilos" if cpu.threads > cpu.cores else "")
    parts = [f"{count} ({cpu.model})"]
    if cpu.per_core_percent:
        parts.append("uso " + " / ".join(f"{p:.0f} %" for p in cpu.per_core_percent))
    if cpu.frequency_mhz:
        text = "frecuencia " + " / ".join(f"{mhz / 1000:.1f}" for mhz in cpu.frequency_mhz) + " GHz"
        if cpu.max_frequency_mhz:
            text += f" (máx. {cpu.max_frequency_mhz / 1000:.1f})"
        parts.append(text)
    lines = ["🧩 Núcleos: " + " · ".join(parts)]
    if cpu.throttle_events:
        lines.append(f"ℹ️ La CPU se frenó por temperatura {cpu.throttle_events} veces desde el arranque")
    return lines


def describe(snapshot: Snapshot, now: datetime) -> tuple[int, list[str]]:
    """(number of things that need a look, the report lines)."""
    s, problems, lines = snapshot, 0, []
    lines.append(f"🖥️ {s.os_name}" + (f" · kernel {s.kernel}" if s.kernel else ""))

    if s.booted_at:
        lines.append(f"⏱️ Encendida desde {_since(s.booted_at, now)}")
    for session in s.sessions:
        if session.remote:  # a remote login is worth a line; the ordinary local session is not
            problems += 1
            when = f" iniciada {_since(session.started_at, now)}" if session.started_at else ""
            lines.append(f"👤 ⚠️ Sesión remota de {session.user}{when}")

    cpu = []
    if s.cpu_percent is not None:
        cpu.append(f"{s.cpu_percent:.1f} % ahora")
    if s.load:
        cores = "" if s.cpu_info else f" ({s.cores} núcleos)"  # the cores line says it when known
        cpu.append(f"carga {s.load[0]:.2f} / {s.load[1]:.2f} / {s.load[2]:.2f}{cores}")
        problems += s.load[0] > s.cores * LOAD_WARN_PER_CORE
    if cpu:
        overloaded = s.load and s.load[0] > s.cores * LOAD_WARN_PER_CORE
        lines.append(("🟠 " if overloaded else "⚙️ ") + "CPU: " + " · ".join(cpu) + (" — carga alta" if overloaded else ""))
    if s.cpu_info:
        lines += _cpu_lines(s.cpu_info)
    if s.temperature_c is not None:
        hot = s.temperature_c >= TEMPERATURE_WARN_C
        problems += hot
        lines.append(f"{'🟠' if hot else '🌡️'} Temperatura: {s.temperature_c:.0f} °C" + (" — muy alta" if hot else ""))

    if s.memory:
        m = s.memory
        used = m.total - m.available
        low = _percent(m.available, m.total) < MEMORY_AVAILABLE_WARN_PERCENT
        problems += low
        text = (f"{'🟠' if low else '💾'} Memoria: {systemd_info.fmt_bytes(used)} de {systemd_info.fmt_bytes(m.total)} usados "
                f"({_percent(used, m.total)} %) · disponible {systemd_info.fmt_bytes(m.available)}")
        if m.swap_total:
            text += f" · swap {systemd_info.fmt_bytes(m.swap_total - m.swap_free)} de {systemd_info.fmt_bytes(m.swap_total)}"
        lines.append(text + (" — poca memoria libre" if low else ""))
    for disk in s.disks:
        percent = _percent(disk.used, disk.total)
        full = percent >= DISK_WARN_PERCENT
        problems += full
        lines.append(f"{'🟠' if full else '💽'} Disco {disk.mount}: {systemd_info.fmt_bytes(disk.used)} de "
                     f"{systemd_info.fmt_bytes(disk.total)} usados ({percent} %) · libres {systemd_info.fmt_bytes(disk.free)}"
                     + (" — casi lleno" if full else ""))
    if s.battery:
        b = s.battery
        low = b.percent < BATTERY_LOW_PERCENT and b.status == "Discharging"
        problems += low
        text = f"{'🟠' if low else '🔋'} Batería: {b.percent} % ({BATTERY_STATUS.get(b.status, b.status.lower())})"
        if b.plugged_in is not None:
            text += " · 🔌 enchufada" if b.plugged_in else " · 🔌 sin cargador"
        lines.append(text + (" — baja" if low else ""))
        details = []
        if b.health_percent is not None:
            worn = b.health_percent < BATTERY_HEALTH_WARN_PERCENT
            problems += worn
            details.append(f"salud {b.health_percent} %" + (" — desgastada" if worn else "") + (f" ({b.full_wh:.1f} Wh)" if b.full_wh else ""))
        if b.cycles:
            details.append(f"{b.cycles} ciclos")
        if details:
            lines.append("🔋 " + " · ".join(details))
    if s.top_memory:
        lines.append("🧠 Más memoria: " + " · ".join(f"{name} {percent:.0f} %" for name, percent in s.top_memory))
    if s.failed_units:
        problems += 1
        shown = ", ".join(s.failed_units[:3]) + (f" y {len(s.failed_units) - 3} más" if len(s.failed_units) > 3 else "")
        lines.append(f"🟠 Servicios con fallos: {len(s.failed_units)} ({shown})")
    if s.reboot_required:
        problems += 1
        lines.append("🟠 Hay un reinicio pendiente (por actualizaciones)")
    return int(problems), lines
