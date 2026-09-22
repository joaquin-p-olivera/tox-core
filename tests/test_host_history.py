from datetime import datetime

import pytest

from app.services import host_history as hh
from app.services.host_status import Battery

NOW_TS = datetime(2026, 9, 21, 20, 0, 0).timestamp()
NOW = datetime.fromtimestamp(NOW_TS)
MIN = 60


def series(start_minutes_ago, count, step_minutes=1, **fields):
    """`count` samples, one every `step_minutes`, the first one `start_minutes_ago` minutes ago. Each field may be a
    constant or a function of the sample index i."""
    out = []
    for i in range(count):
        values = {k: (v(i) if callable(v) else v) for k, v in fields.items()}
        out.append(hh.Sample(NOW_TS - (start_minutes_ago - i * step_minutes) * MIN, **values))
    return out


def battery(percent, status="Discharging"):
    return Battery(percent=percent, status=status)


# ---- analyze -----------------------------------------------------------------------------------

def test_no_samples_means_no_history():
    assert hh.analyze([], NOW_TS) == hh.History()
    assert hh.peak_lines(hh.History(), NOW) == ["📈 Historial: todavía sin datos (la API empieza a registrar al arrancar)"]
    assert hh.battery_lines(hh.History(), battery(50), NOW) == []


def test_peaks_are_the_highest_values_and_when_they_happened():
    samples = series(60, 60, cpu=lambda i: 10 if i != 30 else 92.4, memory=lambda i: 50 + i * 0.5, temperature=lambda i: 40 + (i == 45) * 31)
    h = hh.analyze(samples, NOW_TS)
    assert h.count == 60
    assert (h.cpu.value, h.cpu.at) == (92.4, NOW_TS - 30 * MIN)
    assert (h.memory.value, h.memory.at) == (pytest.approx(79.5), NOW_TS - 1 * MIN)
    assert (h.temperature.value, h.temperature.at) == (71, NOW_TS - 15 * MIN)
    assert h.span_seconds == 59 * MIN


def test_only_the_last_24_hours_count_and_future_samples_are_ignored():
    old = [hh.Sample(NOW_TS - 25 * 3600, cpu=99)]
    future = [hh.Sample(NOW_TS + 3600, cpu=98)]  # a clock that jumped
    recent = [hh.Sample(NOW_TS - 60, cpu=5)]
    h = hh.analyze(old + future + recent, NOW_TS)
    assert h.count == 1 and h.cpu.value == 5


def test_samples_are_sorted_before_use():
    shuffled = series(10, 10, battery=lambda i: 90 - i, status="Discharging")[::-1]
    assert hh.analyze(shuffled, NOW_TS).battery_min.value == 81


def test_battery_range_and_time_on_battery_skip_gaps():
    on = series(120, 30, battery=lambda i: 80 - i, status="Discharging")                       # 30 min on battery
    hole = series(60, 5, step_minutes=20, battery=50, status="Discharging")                    # samples 20 min apart: API was down
    h = hh.analyze(on + hole, NOW_TS)
    assert h.battery_max.value == 80 and h.battery_min.value == 50
    assert h.on_battery_seconds == 29 * MIN, "gaps longer than 10 minutes must not count as time on battery"


# ---- trends ------------------------------------------------------------------------------------

def test_discharge_trend_and_autonomy():
    samples = series(60, 61, battery=lambda i: 80 - i * 0.2, status="Discharging")   # loses 12 % in 60 min = 12 %/h
    h = hh.analyze(samples, NOW_TS)
    assert h.trend_percent_per_hour == pytest.approx(-12.0) and h.trend_seconds == 60 * MIN
    lines = hh.battery_lines(h, battery(68), NOW)
    assert lines[0] == "⏳ Autonomía: ≈ 5 h 40 min al ritmo de las últimas 1 h (12 %/h)"   # 68 / 12 h


def test_charge_trend_and_time_to_full():
    samples = series(30, 31, battery=lambda i: 40 + i * 0.5, status="Charging")   # +15 % in 30 min = 30 %/h
    h = hh.analyze(samples, NOW_TS)
    assert h.trend_percent_per_hour == pytest.approx(30.0)
    assert hh.battery_lines(h, battery(55, "Charging"), NOW)[0] == "⏳ Carga completa en ≈ 1 h 30 min al ritmo de las últimas 30 min (30 %/h)"


def test_a_trend_needs_enough_time_and_enough_change():
    assert hh.analyze(series(5, 6, battery=lambda i: 80 - i, status="Discharging"), NOW_TS).trend_percent_per_hour is None       # 5 min
    assert hh.analyze(series(60, 61, battery=lambda i: 80 - i * 0.01, status="Discharging"), NOW_TS).trend_percent_per_hour is None  # 0.6 %


def test_the_trend_only_looks_at_the_current_run():
    charging = series(120, 60, battery=lambda i: 20 + i, status="Charging")
    discharging = series(60, 60, battery=lambda i: 80 - i * 0.5, status="Discharging")
    h = hh.analyze(charging + discharging, NOW_TS)
    assert h.trend_percent_per_hour == pytest.approx(-30.0, abs=1)   # not mixed with the earlier charge


def test_charging_that_does_not_move_is_reported_but_not_as_an_alarm():
    samples = series(300, 301, battery=75, status="Charging")   # 5 hours at 75 %
    h = hh.analyze(samples, NOW_TS)
    assert h.stalled_since == NOW_TS - 300 * MIN and h.trend_percent_per_hour is None
    assert hh.battery_lines(h, battery(75, "Charging"), NOW)[0].startswith("ℹ️ Enchufada, pero sin subir de 75 % desde las 15:00:00")


def test_a_full_battery_is_not_stalled():
    assert hh.analyze(series(300, 301, battery=100, status="Full"), NOW_TS).stalled_since is None


def test_peak_drain_is_the_fastest_window():
    slow = series(120, 60, battery=lambda i: 90 - i * 0.1, status="Discharging")     # 6 %/h
    fast = series(60, 21, battery=lambda i: 84 - i * 1.0, status="Discharging")      # 60 %/h for 20 min, continuing from where slow ends (84.1 %)
    h = hh.analyze(slow + fast, NOW_TS)
    assert h.peak_drain.value == pytest.approx(60.0, abs=1)


def test_peak_drain_ignores_short_windows_and_charging():
    assert hh.analyze(series(5, 6, battery=lambda i: 90 - i * 5, status="Discharging"), NOW_TS).peak_drain is None
    assert hh.analyze(series(30, 31, battery=lambda i: 40 + i, status="Charging"), NOW_TS).peak_drain is None


# ---- the report lines --------------------------------------------------------------------------

def test_battery_summary_line():
    samples = series(120, 121, battery=lambda i: 90 - i * 0.25, status="Discharging")
    lines = hh.battery_lines(hh.analyze(samples, NOW_TS), battery(60), NOW)
    assert lines[1].startswith("🔋 Historial (2 h): mín. 60 % (20:00:00) · máx. 90 % (18:00:00) · 2 h con batería")
    assert "pico de descarga 15 %/h" in lines[1]


def test_no_range_when_the_level_never_changed():
    lines = hh.battery_lines(hh.analyze(series(20, 20, battery=75, status="Charging"), NOW_TS), battery(75, "Charging"), NOW)
    assert lines == []


def test_peak_line_and_the_short_history_note():
    few = hh.analyze(series(3, 3, cpu=40, memory=80, temperature=50), NOW_TS)   # ties: the first (earliest) one is reported
    assert hh.peak_lines(few, NOW) == ["📈 Picos (2 min de datos): CPU 40 % (19:57:00) · memoria 80 % usada (19:57:00) · 50 °C (19:57:00) — historial recién empezando"]
    many = hh.analyze(series(60, 30, step_minutes=2, cpu=lambda i: i), NOW_TS)
    assert hh.peak_lines(many, NOW) == ["📈 Picos (58 min de datos): CPU 29 % (19:58:00)"]


def test_samples_without_any_peak_data():
    assert hh.peak_lines(hh.analyze(series(30, 12, battery=50, status="Full"), NOW_TS), NOW) == ["📈 Historial: recién empieza (12 muestras)"]
