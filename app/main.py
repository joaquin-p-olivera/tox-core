import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .database import init_db
from .routers import messages

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
    logger.info("tox API started (env=%s)", settings.APP_ENV)
    yield


app = FastAPI(title="Tox API", lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(messages.router)
