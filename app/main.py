import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .database import SessionLocal, init_db
from .services import remote_browser
from .services.alert_monitor import AlertMonitor
from .services.host_sampler import HostSampler
from .routers import alerts, audios, blacklist, history, images, jobs, messages, stickers

settings = get_settings()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=settings.LOG_LEVEL.upper(),
)
logger = logging.getLogger("tox")

LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def timestamp_uvicorn_logs() -> None:
    """uvicorn formats its own lines (startup, the access log: `127.0.0.1:53994 - "GET /..." 200 OK`) with no time at
    all, unlike the app's. Gives them the same date and time, whatever way uvicorn was started (a command line, a service)."""
    from uvicorn.logging import AccessFormatter, DefaultFormatter

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
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
app.include_router(jobs.router)
app.include_router(history.router)
