import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import HostSample
from app.services import host_sampler as hs
from tests.test_host_status import FakeSources, healthy_machine


class Clock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def factory(db_session):
    """The sampler opens its own sessions; here they all share the test's in-memory database."""
    class Shared:
        def __enter__(self):
            return db_session

        def __exit__(self, *exc):
            return False

    return Shared


@pytest.fixture
def threaded_db(tmp_path):
    """Like production: a database file and a NEW session for every use (a Session must not be shared between threads)."""
    from app import models  # noqa: F401
    from app.database import Base

    engine = create_engine(f"sqlite:///{tmp_path}/samples.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def count():
        with session_factory() as db:
            return len(list(db.scalars(select(HostSample))))

    yield session_factory, count
    engine.dispose()


def rows(db_session):
    return list(db_session.scalars(select(HostSample).order_by(HostSample.taken_at)))


def make(factory, sources=None, clock=None, interval=60, days=7):
    return hs.HostSampler(factory, interval, days, sources or healthy_machine(), clock or Clock())


def test_a_sample_records_the_vitals(factory, db_session):
    row = make(factory).sample_once()
    saved = rows(db_session)
    assert len(saved) == 1 and saved[0].taken_at == 1_000_000.0
    assert saved[0].memory_used_percent == pytest.approx((3909260 * 1024 - 2000000 * 1024) / (3909260 * 1024) * 100)
    assert (saved[0].temperature_c, saved[0].battery_percent, saved[0].battery_status) == (45.0, 80.0, "Charging")
    assert saved[0].cpu_percent is None, "the first sample has no previous reading to compare with"
    assert row.battery_percent == 80


def test_cpu_is_the_average_since_the_previous_sample(factory, db_session):
    machine = healthy_machine()
    sampler = make(factory, machine)
    sampler.sample_once()
    machine.files["/proc/stat"] = "cpu  160 0 140 840 0 0 0 0\n"   # +100 busy of +140 elapsed
    sampler.sample_once()
    assert rows(db_session)[1].cpu_percent == pytest.approx(100 / 140 * 100)


def test_a_machine_without_battery_or_sensors_still_samples(factory, db_session):
    make(factory, FakeSources(files={"/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 500 kB\n"})).sample_once()
    saved = rows(db_session)[0]
    assert (saved.memory_used_percent, saved.battery_percent, saved.temperature_c, saved.battery_status) == (50.0, None, None, None)


def test_old_samples_are_deleted(factory, db_session):
    clock = Clock(10_000_000.0)
    db_session.add_all([HostSample(taken_at=clock.now - 8 * 86400), HostSample(taken_at=clock.now - 6 * 86400)])
    db_session.commit()
    make(factory, clock=clock, days=7).sample_once()   # the first sample also prunes
    assert [r.taken_at for r in rows(db_session)] == [clock.now - 6 * 86400, clock.now]


def test_the_thread_samples_periodically_and_stops(threaded_db):
    session_factory, count = threaded_db
    sampler = hs.HostSampler(session_factory, 1, 7, healthy_machine(), time.time)
    sampler.start()
    deadline = time.time() + 8
    while count() < 2 and time.time() < deadline:
        time.sleep(0.05)
    sampler.stop()
    stopped_at = count()
    assert stopped_at >= 2
    time.sleep(1.5)
    assert count() == stopped_at, "no more samples after stop()"


def test_a_failure_does_not_kill_the_sampler(threaded_db, caplog):
    class Flaky(FakeSources):
        calls = 0

        def read(self, path):
            if path == "/proc/meminfo":
                Flaky.calls += 1
                if Flaky.calls == 1:
                    raise RuntimeError("disk on fire")
            return super().read(path)

    session_factory, count = threaded_db
    base = healthy_machine()
    sampler = hs.HostSampler(session_factory, 1, 7, Flaky(files=base.files, commands=base.commands, disks=base.disks, globs=base.globs), time.time)
    sampler.start()
    deadline = time.time() + 8
    while count() == 0 and time.time() < deadline:
        time.sleep(0.05)
    sampler.stop()
    assert count() >= 1, "it recovered and recorded a later sample"
    assert "Could not record a host sample" in caplog.text


def test_interval_zero_never_starts_a_thread(factory):
    sampler = make(factory, interval=0)
    sampler.start()
    assert sampler._thread is None
    sampler.stop()
