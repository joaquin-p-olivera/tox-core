import os
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file() -> str:
    # APP_ENV must come from the real process environment: it decides which
    # file is loaded, so it can't live inside that file.
    return ".env.prod" if os.getenv("APP_ENV", "dev").lower() == "prod" else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(), env_file_encoding="utf-8", extra="ignore"
    )

    APP_ENV: str = "dev"
    API_KEY: str = ""
    DATABASE_URL: str = "sqlite:///./tox.db"
    TRIVIA_TIMEOUT_SECONDS: int = 60
    COMMAND_PREFIXES: str = "!,/"
    LOG_LEVEL: str = "INFO"

    @property
    def command_prefixes_list(self) -> List[str]:
        return [p.strip() for p in self.COMMAND_PREFIXES.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
