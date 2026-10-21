import logging
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from fastapi import FastAPI

from .config import get_settings
from .database import SessionLocal, init_db
from .services import remote_browser
from .services.alert_monitor import AlertMonitor
from .services.host_sampler import HostSampler
from .routers import alerts, audios, blacklist, censorship, history, images, jobs, messages, stickers

settings = get_settings()

LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def log_file_handler() -> logging.Handler | None:
    """The log file of this run (LOG_DIR), rolled over every LOG_ROTATE_HOURS: `tox.log` is the current one and
    the older ones are named after the time they end at, `tox-2026-10-20.log`, so a day can be opened without
    digging through everything. Nothing is ever deleted (backupCount=0): clearing them out is a manual chore.

    None when LOG_DIR is empty (console only, as in the tests) or when the folder can't be written: the API is
    never worth failing to start over a log file."""
    if not settings.LOG_DIR.strip():
        return None
    daily = settings.LOG_ROTATE_HOURS == 24  # at midnight, so a daily file holds the day it is named after
    try:
        folder = Path(settings.LOG_DIR).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            folder / "tox.log", when="midnight" if daily else "H",
            interval=1 if daily else settings.LOG_ROTATE_HOURS, backupCount=0, encoding="utf-8",
        )
    except OSError as error:
        logging.getLogger("tox").warning("Logging to the console only, %s is not writable: %s", settings.LOG_DIR, error)
        return None
    handler.suffix = "%Y-%m-%d" if daily else "%Y-%m-%d_%H"
    # "tox.log.2026-10-20" -> "tox-2026-10-20.log": every file keeps the extension, so they all open the same way
    handler.namer = lambda name: name.replace(".log.", "-") + ".log"
    return handler


# The file handler is shared with uvicorn's own loggers below, so the access log ends up in the same file.
file_handler = log_file_handler()
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=settings.LOG_LEVEL.upper(),
    handlers=[logging.StreamHandler(), *([file_handler] if file_handler is not None else [])],
)
logger = logging.getLogger("tox")


def timestamp_uvicorn_logs() -> None:
    """uvicorn formats its own lines (startup, the access log: `127.0.0.1:53994 - "GET /..." 200 OK`) with no time at
    all, unlike the app's. Gives them the same date and time, whatever way uvicorn was started (a command line, a service)."""
    from uvicorn.logging import AccessFormatter, DefaultFormatter

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        if file_handler is not None and not uvicorn_logger.propagate and file_handler not in uvicorn_logger.handlers:
            # Those loggers don't propagate to the root one, so the file has to be added to each of them -- except
            # `uvicorn.error`, which does propagate (to `uvicorn`): giving it the file too wrote every line twice.
            uvicorn_logger.addHandler(file_handler)
        for handler in uvicorn_logger.handlers:
            formatter = handler.formatter
            if isinstance(formatter, AccessFormatter):
                handler.setFormatter(AccessFormatter(
                    '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                    datefmt=LOG_DATE_FORMAT, use_colors=formatter.use_colors))
            elif isinstance(formatter, DefaultFormatter):
                handler.setFormatter(DefaultFormatter(
                    "%(asctime)s %(levelprefix)s %(message)s", datefmt=LOG_DATE_FORMAT, use_colors=formatter.use_colors))


timestamp_uvicorn_logs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.API_KEY:
        raise RuntimeError(
            "API_KEY is not set. Create a .env file with a secret, e.g. "
            'python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    init_db()
    sampler = HostSampler(SessionLocal, settings.HOST_SAMPLE_INTERVAL_SECONDS, settings.HOST_HISTORY_DAYS)
    sampler.start()  # a no-op when HOST_SAMPLE_INTERVAL_SECONDS=0
    monitor = AlertMonitor(SessionLocal, settings)
    monitor.start()  # a no-op when ALERT_SERVICES is empty
    logger.info("tox API started (env=%s)", settings.APP_ENV)
    yield
    sampler.stop()
    monitor.stop()
    remote_browser.shutdown()  # closes the browser of !web if one is open


app = FastAPI(title="Tox API", lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(messages.router)
app.include_router(audios.router)
app.include_router(stickers.router)
app.include_router(alerts.router)
app.include_router(images.router)
app.include_router(blacklist.router)
app.include_router(censorship.router)
app.include_router(jobs.router)
app.include_router(history.router)
