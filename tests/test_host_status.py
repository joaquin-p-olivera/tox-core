from datetime import datetime, timedelta

import pytest

from app.services import host_status as hs

NOW = datetime(2026, 9, 21, 20, 0, 0)

# ---- parsers, with samples taken from a real Linux Mint machine ----------------------------------

MEMINFO = "MemTotal:        3909260 kB\nMemFree:          171380 kB\nMemAvailable:     802804 kB\nSwapTotal:       3457016 kB\nSwapFree:        2195016 kB\n"
MOUNTS = "\n".join([
    "/dev/sda2 / ext4 rw,relatime 0 0", "tmpfs /run tmpfs rw 0 0", "/dev/sda1 /boot/efi vfat rw 0 0",
    "/dev/loop3 /snap/core22/1 squashfs ro 0 0", "/dev/sdb1 /media/joaquin/USB\\040DISK exfat rw 0 0",
    "/dev/sda2 /var/lib/docker/x ext4 rw 0 0", "/dev/sda2 / ext4 rw 0 0", "proc /proc proc rw 0 0"])
LOGINCTL_LIST = "c1 1000 joaquin seat0 tty7 active no -\n"
SESSION = "Name=joaquin\nType=x11\nRemote=no\nClass=user\nTimestampMonotonic=25800000\n"


def test_os_release_uptime_loadavg():
    assert hs.parse_os_release('NAME="Linux Mint"\nPRETTY_NAME="Linux Mint 22.3"\nID=linuxmint\n') == "Linux Mint 22.3"
    assert hs.parse_os_release("nada") == "Linux"
    assert hs.parse_uptime_seconds("19923.71 36102.39\n") == pytest.approx(19923.71)
    assert hs.parse_uptime_seconds("") is None and hs.parse_uptime_seconds("x") is None
    assert hs.parse_loadavg("1.19 1.39 1.35 2/1163 24242\n") == (1.19, 1.39, 1.35)
    assert hs.parse_loadavg("basura") is None


def test_cpu_times_and_percent():
    before = hs.parse_cpu_times("cpu  1000 0 500 8000 500 0 0 0 0 0\ncpu0 1 1 1 1 1\n")
    after = hs.parse_cpu_times("cpu  1300 0 600 8100 500 0 0 0 0 0\n")
    assert before == (1500, 10000) and after == (1900, 10500)
    assert hs.cpu_percent(before, after) == pytest.approx(80.0)   # 400 busy of 500 elapsed
    assert hs.cpu_percent(after, after) is None                   # no time elapsed
    assert hs.cpu_percent(after, before) is None                  # counters went backwards
    assert hs.parse_cpu_times("intr 1 2 3") is None and hs.parse_cpu_times("cpu  a b c d e") is None


def test_meminfo():
    m = hs.parse_meminfo(MEMINFO)
    assert (m.total, m.available, m.swap_total, m.swap_free) == (3909260 * 1024, 802804 * 1024, 3457016 * 1024, 2195016 * 1024)
    assert hs.parse_meminfo("MemTotal: 5 kB") is None and hs.parse_meminfo("") is None


def test_temperature_takes_the_hottest_plausible_sensor():
    assert hs.parse_temperature(["42000", "20000", "45000\n", "37000"]) == 45.0
    assert hs.parse_temperature(["0", "-5000", "999999", "x", ""]) is None
    assert hs.parse_temperature([]) is None


def test_mounts_only_real_disks_once_each():
    assert hs.parse_mounts(MOUNTS) == [("/", "ext4"), ("/media/joaquin/USB DISK", "exfat"), ("/var/lib/docker/x", "ext4")]


def test_session_ids_are_plain_ids_only():
    assert hs.parse_session_ids(LOGINCTL_LIST) == ["c1"]
    assert hs.parse_session_ids("c1 1\n2 1\n../../x 1\nabc; rm 1\n") == ["c1", "2"]  # only safe ids end up on a command line
    assert hs.parse_session_ids("") == []


def test_a_session_start_is_placed_from_boot_time_not_from_the_wall_clock():
    """The machine's hardware clock is in local time, so loginctl's own timestamp is hours off: it must not be used."""
    booted = datetime(2026, 9, 21, 14, 31, 53)
    session = hs.parse_session(SESSION + "Timestamp=Mon 2026-09-21 17:32:19 -03\n", booted)
    assert session.started_at == booted + timedelta(seconds=25.8)
    assert (session.user, session.kind, session.remote) == ("joaquin", "gráfica", False)


@pytest.mark.parametrize("text, kind, remote", [
    ("Name=a\nType=tty\nRemote=no\nClass=user\n", "consola", False),
    ("Name=a\nType=tty\nRemote=yes\nClass=user\n", "remota", True),
    ("Name=a\nType=wayland\nRemote=no\nClass=user\n", "gráfica", False),
    ("Name=a\nType=weird\nRemote=no\nClass=user\n", "sesión", False)])
def test_session_kinds(text, kind, remote):
    session = hs.parse_session(text, datetime(2026, 1, 1))
    assert (session.kind, session.remote, session.started_at) == (kind, remote, None)


def test_greeter_and_nameless_sessions_are_ignored():
    assert hs.parse_session("Name=gdm\nClass=greeter\n", None) is None
    assert hs.parse_session("Class=user\n", None) is None


def test_memory_by_process_sums_by_name_and_never_uses_arguments():
    ps = " 19.4 Isolated Web Co\n  5.4 Isolated Web Co\n 10.9 firefox-bin\n  9.3 claude\n  0.1 bash\n  x bad\n\n"
    assert hs.parse_memory_by_process(ps) == [("Isolated Web Co", pytest.approx(24.8)), ("firefox-bin", 10.9), ("claude", 9.3)]


def test_failed_units_with_an_ignore_list():
    text = "casper-md5check.service loaded failed failed Verify Live ISO checksums\nfoo@1.service loaded failed failed Foo\ngarbage line\n"
    assert hs.parse_failed_units(text) == ["casper-md5check.service", "foo@1.service"]
    assert hs.parse_failed_units(text, {"casper-md5check.service"}) == ["foo@1.service"]
    assert hs.parse_failed_units("") == []


def test_battery_from_charge_units_like_the_real_one():
    files = {"capacity": "75", "status": "Charging", "charge_full": "3264000", "charge_full_design": "3264000",
             "voltage_min_design": "11550000", "voltage_now": "12728000", "cycle_count": "0"}
    b = hs.parse_battery(files, plugged_in=True)
    assert (b.percent, b.status, b.plugged_in, b.health_percent, b.cycles) == (75, "Charging", True, 100, None)
    assert b.full_wh == pytest.approx(37.699, abs=0.01)  # 3264000 µAh × 11.55 V


def test_battery_from_energy_units_and_wear():
    b = hs.parse_battery({"capacity": "40", "status": "Discharging", "energy_full": "30000000", "energy_full_design": "40000000",
                          "cycle_count": "212"})
    assert (b.health_percent, b.full_wh, b.cycles, b.plugged_in) == (75, 30.0, 212, None)


def test_battery_missing_fields():
    assert hs.parse_battery({}) is None and hs.parse_battery({"capacity": "x"}) is None
    b = hs.parse_battery({"capacity": "50"})
    assert (b.status, b.health_percent, b.full_wh) == ("Unknown", None, None)


# ---- a fake machine ----------------------------------------------------------------------------

class FakeSources(hs.Sources):
    def __init__(self, files=None, commands=None, disks=None, globs=None, cores=2, exists=()):
        self.files, self.commands, self.disks, self.globs = files or {}, commands or {}, disks or {}, globs or {}
        self._cores, self._exists, self.ran = cores, set(exists), []

    def read(self, path):
        return self.files.get(path)

    def glob(self, pattern):
        return self.globs.get(pattern, [])

    def exists(self, path):
        return path in self._exists

    def run(self, argv, timeout=4.0):
        self.ran.append(tuple(argv))
        return self.commands.get(tuple(argv))

    def disk_usage(self, path):
        return self.disks.get(path)

    def cores(self):
        return self._cores

    def kernel(self):
        return "7.0.0-31-generic"


def healthy_machine(**overrides):
    files = {
        "/proc/uptime": "19923.71 36102.39\n", "/proc/loadavg": "0.50 0.60 0.70 1/100 1\n", "/proc/stat": "cpu  100 0 100 800 0 0 0 0\n",
        "/proc/meminfo": MEMINFO.replace("802804", "2000000"), "/proc/mounts": "/dev/sda2 / ext4 rw 0 0\n",
        "/etc/os-release": 'PRETTY_NAME="Linux Mint 22.3"\n', "/sys/class/thermal/thermal_zone0/temp": "45000\n",
        "/sys/class/power_supply/BAT0/capacity": "80", "/sys/class/power_supply/BAT0/status": "Charging",
        "/sys/class/power_supply/BAT0/charge_full": "3264000", "/sys/class/power_supply/BAT0/charge_full_design": "3264000",
        "/sys/class/power_supply/BAT0/voltage_min_design": "11550000",
        "/sys/class/power_supply/ACAD/type": "Mains\n", "/sys/class/power_supply/ACAD/online": "1\n"}
    commands = {("loginctl", "list-sessions", "--no-legend"): LOGINCTL_LIST,
                ("loginctl", "show-session", "c1", "-p", "Name", "-p", "Type", "-p", "Remote", "-p", "Class", "-p", "TimestampMonotonic"): SESSION,
                ("ps", "-eo", "pmem=,comm="): "10.0 firefox-bin\n5.0 claude\n",
                ("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"): "", ("systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager"): ""}
    kwargs = dict(files=files, commands=commands, disks={"/": (30 * 1024**3, 10 * 1024**3, 20 * 1024**3)},
                  globs={"/sys/class/thermal/thermal_zone*/temp": ["/sys/class/thermal/thermal_zone0/temp"],
                         "/sys/class/power_supply/BAT*": ["/sys/class/power_supply/BAT0"],
                         "/sys/class/power_supply/*": ["/sys/class/power_supply/ACAD", "/sys/class/power_supply/BAT0"]})
    kwargs.update(overrides)
    return FakeSources(**kwargs)


def snapshot(**overrides):
    return hs.read_snapshot(NOW, healthy_machine(**overrides), sample_seconds=0)


# ---- read_snapshot -----------------------------------------------------------------------------

def test_a_healthy_machine_needs_nothing():
    problems, lines = hs.describe(snapshot(), NOW)
    assert problems == 0
    assert lines == [
        "🖥️ Linux Mint 22.3 · kernel 7.0.0-31-generic",
        "⏱️ Encendida desde 14:27:56 (hace 5 h 32 min)",
        "⚙️ CPU: carga 0.50 / 0.60 / 0.70 (2 núcleos)",   # no "ahora": the two /proc/stat readings were identical
        "🌡️ Temperatura: 45 °C",
        "💾 Memoria: 1.8 GB de 3.7 GB usados (49 %) · disponible 1.9 GB · swap 1.2 GB de 3.3 GB",
        "💽 Disco /: 10.0 GB de 30.0 GB usados (33 %) · libres 20.0 GB",
        "🔋 Batería: 80 % (cargando) · 🔌 enchufada",
        "🔋 salud 100 % (37.7 Wh)",
        "🧠 Más memoria: firefox-bin 10 % · claude 5 %",
    ]


def test_cpu_now_is_reported_when_the_counters_move():
    class Moving(FakeSources):
        reads = 0

        def read(self, path):
            if path == "/proc/stat":
                Moving.reads += 1
                return "cpu  100 0 100 800 0 0 0 0\n" if Moving.reads == 1 else "cpu  160 0 140 840 0 0 0 0\n"
            return super().read(path)

    base = healthy_machine()
    machine = Moving(files=base.files, commands=base.commands, disks=base.disks, globs=base.globs)
    s = hs.read_snapshot(NOW, machine, sample_seconds=0)
    # busy went from 200 to 300 (+100) while the total went from 1000 to 1140 (+140): 100/140
    assert s.cpu_percent == pytest.approx(100 / 140 * 100)
    assert "⚙️ CPU: 71.4 % ahora · carga 0.50 / 0.60 / 0.70 (2 núcleos)" in hs.describe(s, NOW)[1]  # no cpuinfo in this fake


def test_the_snapshot_only_runs_fixed_commands():
    sources = healthy_machine()
    hs.read_snapshot(NOW, sources, sample_seconds=0)
    assert set(sources.ran) == {
        ("loginctl", "list-sessions", "--no-legend"),
        ("loginctl", "show-session", "c1", "-p", "Name", "-p", "Type", "-p", "Remote", "-p", "Class", "-p", "TimestampMonotonic"),
        ("ps", "-eo", "pmem=,comm="),
        ("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"),
        ("systemctl", "--user", "--failed", "--no-legend", "--plain", "--no-pager")}


def test_boot_and_session_times_come_from_uptime_not_from_the_wall_clock():
    s = snapshot()
    assert s.booted_at == NOW - timedelta(seconds=19923.71)
    assert s.sessions[0].started_at == s.booted_at + timedelta(seconds=25.8)


def test_missing_data_is_simply_left_out():
    machine = FakeSources(files={"/proc/uptime": "100 1\n"}, cores=1)
    s = hs.read_snapshot(NOW, machine, sample_seconds=0)
    problems, lines = hs.describe(s, NOW)
    assert problems == 0 and s.memory is None and s.battery is None and s.disks == [] and s.temperature_c is None
    assert lines[0] == "🖥️ Linux · kernel 7.0.0-31-generic" and not any(l.startswith("👤") for l in lines)


def test_a_failing_command_does_not_break_the_report():
    machine = healthy_machine(commands={})  # every command "fails" (None)
    s = hs.read_snapshot(NOW, machine, sample_seconds=0)
    assert s.sessions == [] and s.top_memory == [] and s.failed_units == []


# ---- what needs a look --------------------------------------------------------------------------

def flags(**overrides):
    return hs.describe(snapshot(**overrides), NOW)


def test_low_memory_is_flagged():
    problems, lines = flags(files={**healthy_machine().files, "/proc/meminfo": MEMINFO.replace("802804", "200000")})
    assert problems == 1 and any(l.startswith("🟠 Memoria:") and l.endswith("— poca memoria libre") for l in lines)


def test_a_nearly_full_disk_is_flagged():
    problems, lines = flags(disks={"/": (100 * 1024**3, 90 * 1024**3, 10 * 1024**3)})
    assert problems == 1 and any(l.startswith("🟠 Disco /:") and "(90 %)" in l and l.endswith("— casi lleno") for l in lines)


def test_a_hot_machine_is_flagged():
    problems, lines = flags(files={**healthy_machine().files, "/sys/class/thermal/thermal_zone0/temp": "90000"})
    assert problems == 1 and "🟠 Temperatura: 90 °C — muy alta" in lines


def test_a_high_load_is_flagged():
    problems, lines = flags(files={**healthy_machine().files, "/proc/loadavg": "4.00 2.00 1.00 1/1 1\n"})
    assert problems == 1 and any(l.startswith("🟠 CPU:") and l.endswith("— carga alta") for l in lines)


def test_a_low_battery_is_flagged_only_when_discharging():
    base = healthy_machine().files
    low = {**base, "/sys/class/power_supply/BAT0/capacity": "12", "/sys/class/power_supply/BAT0/status": "Discharging"}
    problems, lines = flags(files=low)
    assert problems == 1 and any(l.startswith("🟠 Batería: 12 % (descargándose)") and l.endswith("— baja") for l in lines)
    plugged = {**base, "/sys/class/power_supply/BAT0/capacity": "12"}  # low but charging: fine
    assert flags(files=plugged)[0] == 0


def test_a_worn_battery_is_flagged():
    worn = {**healthy_machine().files, "/sys/class/power_supply/BAT0/charge_full": "1600000"}
    problems, lines = flags(files=worn)
    assert problems == 1 and any(l.startswith("🔋 salud 49 % — desgastada") for l in lines)


def test_failed_units_and_a_pending_reboot_are_flagged():
    commands = dict(healthy_machine().commands)
    commands[("systemctl", "--failed", "--no-legend", "--plain", "--no-pager")] = "a.service l f f A\nb.service l f f B\nc.service l f f C\nd.service l f f D\n"
    problems, lines = flags(commands=commands, exists=["/var/run/reboot-required"])
    assert problems == 2
    assert "🟠 Servicios con fallos: 4 (a.service, b.service, c.service y 1 más)" in lines
    assert "🟠 Hay un reinicio pendiente (por actualizaciones)" in lines


def test_ignored_failed_units_do_not_count():
    commands = dict(healthy_machine().commands)
    commands[("systemctl", "--failed", "--no-legend", "--plain", "--no-pager")] = "casper-md5check.service l f f X\n"
    s = hs.read_snapshot(NOW, healthy_machine(commands=commands), sample_seconds=0, ignored_units={"casper-md5check.service"})
    assert hs.describe(s, NOW)[0] == 0


def test_a_remote_session_is_flagged():
    commands = dict(healthy_machine().commands)
    key = next(k for k in commands if k[:2] == ("loginctl", "show-session"))
    commands[key] = "Name=root\nType=tty\nRemote=yes\nClass=user\nTimestampMonotonic=1000000\n"
    problems, lines = flags(commands=commands)
    assert problems == 1 and any(l.startswith("👤 ⚠️ Sesión remota de root") for l in lines)


def test_a_battery_without_a_charger_says_so():
    files = {**healthy_machine().files, "/sys/class/power_supply/ACAD/online": "0\n", "/sys/class/power_supply/BAT0/status": "Discharging"}
    assert "🔋 Batería: 80 % (descargándose) · 🔌 sin cargador" in flags(files=files)[1]


# ---- the local session is not shown, a remote one is --------------------------------------------

def test_an_ordinary_local_session_gets_no_line():
    assert not any(l.startswith("👤") for l in hs.describe(snapshot(), NOW)[1])
    assert snapshot().sessions[0].user == "joaquin", "it is still read, so a remote one can be noticed"


# ---- CPU cores, from a real /proc/cpuinfo --------------------------------------------------------

CPUINFO = """processor	: 0
model name	: Intel(R) Celeron(R) CPU  N3060  @ 1.60GHz
cpu MHz		: 2480.000
physical id	: 0
siblings	: 2
core id		: 0
cpu cores	: 2

processor	: 1
model name	: Intel(R) Celeron(R) CPU  N3060  @ 1.60GHz
cpu MHz		: 2479.691
physical id	: 0
siblings	: 2
core id		: 2
cpu cores	: 2
"""
STAT_2_CORES = "cpu  200 0 200 1600 0 0 0 0\ncpu0 100 0 100 800 0 0 0 0\ncpu1 100 0 100 800 0 0 0 0\nintr 1\n"
STAT_2_CORES_LATER = "cpu  260 0 240 1700 0 0 0 0\ncpu0 150 0 110 840 0 0 0 0\ncpu1 110 0 130 860 0 0 0 0\nintr 1\n"


def test_cpuinfo_model_cores_and_threads():
    assert hs.parse_cpuinfo(CPUINFO) == ("Intel Celeron N3060", 2, 2)
    assert hs.parse_cpuinfo("") is None and hs.parse_cpuinfo("nada útil") is None


def test_hyper_threading_is_told_apart():
    four_threads = "".join(f"processor\t: {i}\nmodel name\t: Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz\nphysical id\t: 0\ncpu cores\t: 2\n\n" for i in range(4))
    assert hs.parse_cpuinfo(four_threads) == ("Intel Core i5-8250U", 2, 4)
    two_sockets = "".join(f"processor\t: {i}\nmodel name\t: AMD EPYC 7B12\nphysical id\t: {i % 2}\ncpu cores\t: 2\n\n" for i in range(4))
    assert hs.parse_cpuinfo(two_sockets)[1:] == (4, 4)   # 2 sockets x 2 cores


@pytest.mark.parametrize("raw, clean", [
    ("Intel(R) Celeron(R) CPU  N3060  @ 1.60GHz", "Intel Celeron N3060"), ("AMD Ryzen 5 5600X 6-Core Processor", "AMD Ryzen 5 5600X 6-Core Processor"),
    ("Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz", "Intel Core i7-9750H"), ("", "")])
def test_cpu_model_is_tidied(raw, clean):
    assert hs.clean_cpu_model(raw) == clean


def test_per_core_usage():
    before, after = hs.parse_per_cpu_times(STAT_2_CORES), hs.parse_per_cpu_times(STAT_2_CORES_LATER)
    assert before == [(200, 1000), (200, 1000)]
    assert hs.per_core_percent(before, after) == [pytest.approx(60.0), pytest.approx(40.0)]   # cpu0: +60 busy of +100; cpu1: +40 of +100
    assert hs.parse_per_cpu_times("cpu  1 2 3 4 5\n") == [] and hs.parse_per_cpu_times("") == []


def cpu_machine(**extra_files):
    base = healthy_machine()
    files = {**base.files, "/proc/cpuinfo": CPUINFO, "/proc/stat": STAT_2_CORES, **extra_files}
    globs = {**base.globs,
             "/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq": ["/sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"],
             "/sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq": ["/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"],
             "/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count": ["/sys/devices/system/cpu/cpu0/thermal_throttle/core_throttle_count"]}
    files.setdefault("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "1524414")
    files.setdefault("/sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", "2480000")
    files.setdefault("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "2480000")
    files.setdefault("/sys/devices/system/cpu/cpu0/thermal_throttle/core_throttle_count", "0")
    return FakeSources(files=files, commands=base.commands, disks=base.disks, globs=globs)


def test_the_cores_line_has_model_usage_and_frequency():
    s = hs.read_snapshot(NOW, cpu_machine(), sample_seconds=0)
    lines = hs.describe(s, NOW)[1]
    i = next(i for i, l in enumerate(lines) if l.startswith("⚙️ CPU:"))
    assert lines[i] == "⚙️ CPU: carga 0.50 / 0.60 / 0.70", "the core count moved to its own line"
    assert lines[i + 1] == "🧩 Núcleos: 2 núcleos (Intel Celeron N3060) · frecuencia 1.5 / 2.5 GHz (máx. 2.5)"  # sorted by CPU number, not by path


def test_the_cores_line_shows_usage_when_the_counters_move():
    class Moving(FakeSources):
        reads = 0

        def read(self, path):
            if path == "/proc/stat":
                Moving.reads += 1
                return STAT_2_CORES if Moving.reads == 1 else STAT_2_CORES_LATER
            return super().read(path)

    base = cpu_machine()
    s = hs.read_snapshot(NOW, Moving(files=base.files, commands=base.commands, disks=base.disks, globs=base.globs), sample_seconds=0)
    assert s.cpu_info.per_core_percent == [pytest.approx(60.0), pytest.approx(40.0)]
    assert "🧩 Núcleos: 2 núcleos (Intel Celeron N3060) · uso 60 % / 40 % · frecuencia 1.5 / 2.5 GHz (máx. 2.5)" in hs.describe(s, NOW)[1]


def test_threads_are_shown_when_there_is_hyper_threading():
    four = "".join(f"processor\t: {i}\nmodel name\t: Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz\nphysical id\t: 0\ncpu cores\t: 2\n\n" for i in range(4))
    s = hs.read_snapshot(NOW, cpu_machine(**{"/proc/cpuinfo": four}), sample_seconds=0)
    assert any(l.startswith("🧩 Núcleos: 2 núcleos / 4 hilos (Intel Core i5-8250U)") for l in hs.describe(s, NOW)[1])


def test_thermal_throttling_is_reported_only_when_it_happened():
    calm = hs.describe(hs.read_snapshot(NOW, cpu_machine(), sample_seconds=0), NOW)[1]
    assert not any("se frenó" in l for l in calm)
    hot = hs.describe(hs.read_snapshot(NOW, cpu_machine(**{"/sys/devices/system/cpu/cpu0/thermal_throttle/core_throttle_count": "7"}), sample_seconds=0), NOW)
    assert "ℹ️ La CPU se frenó por temperatura 7 veces desde el arranque" in hot[1] and hot[0] == 0, "informational: not counted as a problem"


def test_a_cpu_without_frequency_data_still_gets_its_line():
    machine = cpu_machine()
    machine.globs = {k: v for k, v in machine.globs.items() if "cpufreq" not in k}
    line = next(l for l in hs.describe(hs.read_snapshot(NOW, machine, sample_seconds=0), NOW)[1] if l.startswith("🧩"))
    assert line == "🧩 Núcleos: 2 núcleos (Intel Celeron N3060)"
