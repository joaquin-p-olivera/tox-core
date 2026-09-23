import os

# Must be set before the app is imported: Settings reads the environment at import time.
# The database is in-memory so the tests never touch the development database file.
os.environ["API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["HOST_SAMPLE_INTERVAL_SECONDS"] = "0"  # no background sampler thread in tests

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app

API_KEY = "test-key"


@pytest.fixture
def audios_dir(tmp_path):
    """An empty audio folder per test, so the developer's real audios never leak into the tests."""
    path = tmp_path / "audios"
    path.mkdir()
    return path


@pytest.fixture
def risa_audios_dir(tmp_path):
    """Same idea as audios_dir, but for !risa's own folder."""
    path = tmp_path / "risa-audios"
    path.mkdir()
    return path


@pytest.fixture
def settings(audios_dir, risa_audios_dir, tmp_path) -> Settings:
    # SERVICES_FILE points at a per-test file that doesn't exist yet: the developer's real services.json
    # must never be reachable from the tests.
    return Settings(_env_file=None, API_KEY=API_KEY, TRIVIA_TIMEOUT_SECONDS=60,
                    ADMIN_USER_IDS="whatsapp:admin1@lid, telegram:99", AUDIOS_DIR=str(audios_dir),
                    RISA_AUDIOS_DIR=str(risa_audios_dir),
                    SERVICES_FILE=str(tmp_path / "services.json"), SERVICE_COMMAND_TIMEOUT_SECONDS=2)


@pytest.fixture
def db_session():
    # One in-memory database per test; StaticPool keeps a single connection alive.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def client(db_session, settings):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def send(client):
    """Send a message through the real HTTP endpoint and return the reply texts."""

    def _send(text, *, user="u1", name="Alice", chat="g1", platform="whatsapp", is_group=True):
        response = client.post(
            "/api/v1/messages",
            headers={"X-API-Key": API_KEY},
            json={
                "platform": platform,
                "chat_id": chat,
                "user_id": user,
                "user_name": name,
                "text": text,
                "is_group": is_group,
            },
        )
        assert response.status_code == 200, response.text
        return [r["text"] for r in response.json()["replies"]]

    return _send
