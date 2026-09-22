from datetime import datetime

import pytest

from app.services import systemd_info as si

NOW = datetime(2026, 9, 21, 19, 30, 0)

RUNNING = """MainPID=20739
Result=success
NRestarts=0
ExecMainStatus=0
MemoryCurrent=30887936
MemoryPeak=31981568
CPUUsageNSec=1282460000
ActiveState=active
ActiveEnterTimestamp=Mon 2026-09-21 19:14:09 -03
"""

STOPPED = """MainPID=0
Result=success
NRestarts=0
ExecMainStatus=0
MemoryCurrent=[not set]
MemoryPeak=[not set]
CPUUsageNSec=1794021000
ActiveState=inactive
ActiveEnterTimestamp=Mon 2026-09-21 19:09:40 -03
ActiveExitTimestamp=Mon 2026-09-21 19:10:06 -03
InactiveEnterTimestamp=Mon 2026-09-21 19:10:08 -03
"""


# ---- parsing ------------------------------------------------------------------------------------

def test_parse_a_running_service():
    show = si.parse_show(RUNNING)
    assert show.active_state == "active" and show.main_pid == 20739 and show.restarts == 0
    assert show.memory_current == 30887936 and show.memory_peak == 31981568 and show.cpu_nsec == 1282460000
    assert show.active_enter == datetime(2026, 9, 21, 19, 14, 9) and show.result == "success"


def test_parse_a_stopped_service_treats_unset_values_as_unknown():
    show = si.parse_show(STOPPED)
    assert show.active_state == "inactive" and show.main_pid is None
    assert show.memory_current is None and show.memory_peak is None
    assert show.cpu_nsec == 1794021000
    assert show.inactive_enter == datetime(2026, 9, 21, 19, 10, 8)


def test_parse_ignores_localised_weekdays_and_missing_timestamps():
    show = si.parse_show("ActiveEnterTimestamp=lun 2026-09-21 19:14:09 UYT\nActiveExitTimestamp=\nMemoryCurrent=18446744073709551615\n")
    assert show.active_enter == datetime(2026, 9, 21, 19, 14, 9)
    assert show.active_exit is None and show.memory_current is None


def test_parse_garbage_is_harmless():
    assert si.parse_show("") == si.Show()
    assert si.parse_show("no es\nnada=útil=raro\n=\nMainPID=abc") == si.Show()


@pytest.mark.parametrize("line, cpu, memory", [
    ("x.service: Consumed 1.794s CPU time.", 1.794, None),
    ("x.service: Consumed 1.668s CPU time, 30.2M memory peak, 0B memory swap peak.", 1.668, round(30.2 * 1024**2)),
    ("x.service: Consumed 3ms CPU time, 512K memory peak.", 0.003, 512 * 1024),
    ("x.service: Consumed 1min 3.5s CPU time, 1.5G memory peak.", 63.5, round(1.5 * 1024**3)),
    ("x.service: Consumed 2h 3min 5s CPU time, 0B memory peak.", 7385.0, 0),
])
def test_parse_consumed(line, cpu, memory):
    usage = si.parse_consumed(line)
    assert usage.cpu_seconds == pytest.approx(cpu) and usage.memory_peak == memory


def test_parse_consumed_takes_the_newest_line_and_tolerates_nothing():
    text = "x: Consumed 1s CPU time.\nother line\nx: Consumed 9.5s CPU time, 40M memory peak.\n"
    assert si.parse_consumed(text) == si.LastUsage(9.5, 40 * 1024**2)
    assert si.parse_consumed("") is None and si.parse_consumed("nothing to see") is None


# ---- numbers ------------------------------------------------------------------------------------

def test_cpu_percent():
    assert si.cpu_percent(1_000_000_000, 1_500_000_000, 1.0) == pytest.approx(50.0)
    assert si.cpu_percent(0, 2_000_000_000, 1.0) == pytest.approx(200.0)  # more than one core
    assert si.cpu_percent(5, 5, 1.0) == 0
    assert si.cpu_percent(10, 5, 1.0) is None      # the counter went backwards (service restarted)
    assert si.cpu_percent(0, 5, 0) is None


@pytest.mark.parametrize("count, text", [(0, "0 B"), (900, "900 B"), (1024, "1.0 KB"), (30887936, "29.5 MB"),
                                         (int(1.5 * 1024**3), "1.5 GB"), (5 * 1024**4, "5120.0 GB")])
def test_fmt_bytes(count, text):
    assert si.fmt_bytes(count) == text


@pytest.mark.parametrize("seconds, text", [(0, "menos de 1 s"), (0.4, "menos de 1 s"), (26, "26 s"), (60, "1 min"),
                                           (200, "3 min 20 s"), (3600, "1 h"), (7500, "2 h 5 min"),
                                           (86400, "1 d"), (100000, "1 d 3 h")])
def test_fmt_duration(seconds, text):
    assert si.fmt_duration(seconds) == text


def test_fmt_moment_shows_the_date_only_when_it_is_not_today():
    assert si.fmt_moment(datetime(2026, 9, 21, 8, 5, 3), NOW) == "08:05:03"
    assert si.fmt_moment(datetime(2026, 9, 20, 23, 59, 0), NOW) == "20/09 23:59:00"


# ---- the report ---------------------------------------------------------------------------------

def test_report_for_a_running_service():
    assert si.describe(si.parse_show(RUNNING), None, NOW, cpu_now=0.4) == [
        "⏱️ Activo desde 19:14:09 (hace 15 min 51 s)",
        "💾 Memoria: 29.5 MB (pico 30.5 MB)",
        "⚙️ CPU: 0.4 % ahora · 0.1 % promedio · 1.3 s en total",
        "🔁 PID 20739 · reinicios automáticos: 0",
    ]


def test_report_for_a_running_service_without_optional_data():
    show = si.Show(active_state="active", active_enter=datetime(2026, 9, 21, 19, 29, 0))
    assert si.describe(show, None, NOW) == ["⏱️ Activo desde 19:29:00 (hace 1 min)"]


def test_report_for_a_stopped_service_uses_the_journal_for_memory():
    usage = si.LastUsage(cpu_seconds=1.9, memory_peak=34 * 1024**2)
    assert si.describe(si.parse_show(STOPPED), usage, NOW) == [
        "🕒 Detenido desde 19:10:08 (hace 19 min 52 s)",
        "📊 Última ejecución: duró 26 s · CPU 1.8 s · memoria pico 34.0 MB",  # CPU from systemctl wins over the journal
    ]


def test_report_for_a_stopped_service_without_the_journal():
    assert si.describe(si.parse_show(STOPPED), None, NOW)[1] == "📊 Última ejecución: duró 26 s · CPU 1.8 s"


def test_report_when_only_the_journal_knows_the_cpu():
    show = si.Show(active_state="inactive", inactive_enter=datetime(2026, 9, 21, 19, 0, 0))
    assert si.describe(show, si.LastUsage(2.5, None), NOW)[1] == "📊 Última ejecución: CPU 2.5 s"


def test_report_for_a_service_that_failed():
    show = si.Show(active_state="failed", inactive_enter=datetime(2026, 9, 21, 19, 28, 0), result="exit-code", exit_status=1)
    assert si.describe(show, None, NOW) == ["🕒 Detenido desde 19:28:00 (hace 2 min)", "⚠️ Terminó con error: exit-code, código 1"]


def test_report_for_a_service_never_run():
    assert si.describe(si.Show(active_state="inactive"), None, NOW) == [
        "Sin ejecuciones registradas desde el último arranque de la sesión."]


# ---- the journal's own timestamp (units whose times systemd forgets once stopped) ----------------

JOURNAL = "2026-09-21T19:31:22-0300 hp-stream systemd[1]: postgresql@16-main.service: Consumed 1.3s CPU time, 37.2M memory peak, 0B memory swap peak."


def test_parse_consumed_reads_the_journal_timestamp_when_present():
    usage = si.parse_consumed(JOURNAL)
    assert usage.stopped_at == datetime(2026, 9, 21, 19, 31, 22)
    assert usage.cpu_seconds == pytest.approx(1.3) and usage.memory_peak == round(37.2 * 1024**2)
    assert si.parse_consumed("x.service: Consumed 1s CPU time.").stopped_at is None  # `-o cat` has no time


def test_a_stopped_unit_without_times_uses_the_journal_time():
    forgotten = si.Show(active_state="inactive")  # like postgresql@16-main after `systemctl stop`
    ago = si.fmt_duration((NOW - datetime(2026, 9, 21, 19, 31, 22)).total_seconds())
    assert si.describe(forgotten, si.parse_consumed(JOURNAL), NOW) == [
        f"🕒 Detenido desde 19:31:22 (hace {ago})",
        "📊 Última ejecución: CPU 1.3 s · memoria pico 37.2 MB",
    ]


def test_systemctls_own_time_wins_over_the_journal_one():
    show = si.parse_show(STOPPED)
    lines = si.describe(show, si.parse_consumed(JOURNAL), NOW)
    assert lines[0].startswith("🕒 Detenido desde 19:10:08")
