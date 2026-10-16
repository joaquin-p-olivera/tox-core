import io
import logging
import logging.config
import re

import pytest
from uvicorn.config import LOGGING_CONFIG

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
