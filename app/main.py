import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .database import SessionLocal, init_db
from .services.alert_monitor import AlertMonitor
from .services.host_sampler import HostSampler
from .routers import alerts, audios, images, messages, stickers

settings = get_settings()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=settings.LOG_LEVEL.upper(),
)
logger = logging.getLogger("tox")


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


app = FastAPI(title="Tox API", lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(messages.router)
app.include_router(audios.router)
app.include_router(stickers.router)
app.include_router(alerts.router)
app.include_router(images.router)
