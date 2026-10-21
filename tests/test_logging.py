import io
import logging
import logging.config
import re

import pytest
from uvicorn.config import LOGGING_CONFIG

from app import main
from app.config import Settings
from app.main import timestamp_uvicorn_logs

NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def uvicorn_logging():
    """uvicorn's default logging setup, writing to memory; put back as it was afterwards."""
    before = {name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate) for name in NAMES}
    logging.config.dictConfig({**LOGGING_CONFIG, "disable_existing_loggers": False})
    stream = io.StringIO()
    for name in NAMES:
        for handler in logging.getLogger(name).handlers:
            handler.stream = stream
    yield stream
    for name, (handlers, propagate) in before.items():
        logging.getLogger(name).handlers[:] = handlers
        logging.getLogger(name).propagate = propagate


def test_uvicorns_own_lines_get_a_date_and_time(uvicorn_logging):
    timestamp_uvicorn_logs()
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:53994", "GET", "/api/v1/alerts/pending?platform=whatsapp", "1.1", 200)
    logging.getLogger("uvicorn.error").info("Application startup complete.")

    access, startup = uvicorn_logging.getvalue().splitlines()
    stamp = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "
    assert re.match(stamp + r'INFO:\s+127\.0\.0\.1:53994 - "GET /api/v1/alerts/pending\?platform=whatsapp HTTP/1\.1" 200', access)
    assert re.match(stamp + r"INFO:\s+Application startup complete\.", startup)


def test_uvicorns_own_lines_are_written_to_the_file_once(uvicorn_logging, monkeypatch):
    file = io.StringIO()
    handler = logging.StreamHandler(file)
    handler.setFormatter(logging.Formatter("%(message)s"))
    monkeypatch.setattr(main, "file_handler", handler)
    timestamp_uvicorn_logs()

    logging.getLogger("uvicorn.error").info("Application startup complete.")
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/health", "1.1", 200)

    # `uvicorn.error` hands its records to `uvicorn`, which already writes to the file: adding it there too made
    # every startup line appear twice in logs/tox.log
    assert file.getvalue().count("Application startup complete.") == 1
    assert file.getvalue().count("/health") == 1


# --- The log file of a run (LOG_DIR / LOG_ROTATE_HOURS) ---

@pytest.fixture
def logging_settings(monkeypatch):
    """Builds the handler as if the API had started with these settings, without touching the real log folder."""
    def build(**values) -> logging.Handler | None:
        monkeypatch.setattr(main, "settings", Settings(_env_file=None, **values))
        return main.log_file_handler()
    return build


def test_without_a_folder_the_log_only_goes_to_the_console(logging_settings):
    assert logging_settings(LOG_DIR="") is None
    assert logging_settings(LOG_DIR="   ") is None


def test_the_log_is_written_to_a_file_in_the_folder(logging_settings, tmp_path):
    handler = logging_settings(LOG_DIR=str(tmp_path / "logs"))
    assert handler is not None
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.emit(logging.LogRecord("tox", logging.INFO, __file__, 1, "algo pasó", None, None))
    handler.close()

    files = list((tmp_path / "logs").iterdir())
    assert [file.name for file in files] == ["tox.log"]
    assert "algo pasó" in files[0].read_text(encoding="utf-8")


def test_a_day_of_logs_is_one_file_rolled_over_at_midnight(logging_settings, tmp_path):
    handler = logging_settings(LOG_DIR=str(tmp_path), LOG_ROTATE_HOURS=24)
    assert handler.when == "MIDNIGHT" and handler.suffix == "%Y-%m-%d"
    # The rotated file keeps the extension: tox.log.2026-10-20 -> tox-2026-10-20.log
    assert handler.namer(str(tmp_path / "tox.log.2026-10-20")) == str(tmp_path / "tox-2026-10-20.log")
    handler.close()


def test_fewer_hours_mean_a_file_every_that_many_hours(logging_settings, tmp_path):
    handler = logging_settings(LOG_DIR=str(tmp_path), LOG_ROTATE_HOURS=6)
    assert handler.when == "H" and handler.interval == 6 * 3600 and handler.suffix == "%Y-%m-%d_%H"
    handler.close()


def test_a_folder_that_cannot_be_created_is_not_worth_failing_to_start(logging_settings, tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")
    assert logging_settings(LOG_DIR=str(blocked / "logs")) is None
