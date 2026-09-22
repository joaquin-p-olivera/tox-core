import os
from functools import lru_cache
from typing import List

from pydantic import Field
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
    ADMIN_USER_IDS: str = ""
    # Folder with the sound clips !m can send (relative paths resolve against the working directory).
    # If it is missing or empty, !m simply always tags someone.
    AUDIOS_DIR: str = "media/m"
    # Chance (0-1) that !m sends a random audio instead of tagging someone
    M_AUDIO_PROBABILITY: float = Field(default=0.5, ge=0, le=1)
    # API key for the admin-only !service status command (read-only use). Empty = disabled.
    RENDER_API_KEY: str = ""
    # Local services the admin-only `!service -L` may start/stop/query: an allowlist in a JSON file
    SERVICES_FILE: str = "services.json"
    # Keep this below the bots' API timeout (10 s by default) so the chat gets an answer
    SERVICE_COMMAND_TIMEOUT_SECONDS: int = Field(default=8, ge=1, le=60)
    # Failed systemd units that `!service -L pc` should not count as a problem (comma-separated), e.g. harmless leftovers
    HOST_IGNORED_UNITS: str = ""
    # `!github` (admin only): the repos it may look at, comma-separated "owner/name" or GitHub URLs, and a read-only token.
    # The token is optional for public repos (60 requests/hour without it) and required for private ones.
    GITHUB_REPOS: str = ""
    GITHUB_TOKEN: str = ""
    # When GITHUB_TOKEN is empty, take the token of the GitHub CLI session (`gh auth login`) instead. Handy, but that token
    # usually has broad scopes; a fine-grained read-only GITHUB_TOKEN is the safer choice.
    GITHUB_TOKEN_FROM_GH: bool = False
    GITHUB_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=8)
    # The API records CPU, memory, temperature and battery of this machine every N seconds (0 = off) to show peaks and
    # battery trends in `!service -L host`. Samples older than HOST_HISTORY_DAYS are deleted.
    HOST_SAMPLE_INTERVAL_SECONDS: int = Field(default=60, ge=0, le=3600)
    HOST_HISTORY_DAYS: int = Field(default=7, ge=1, le=90)
    # Per request. Keep the total (list + deploys, roughly 2x this) below the bots' API timeout (10 s).
    RENDER_API_TIMEOUT_SECONDS: int = Field(default=4, ge=1, le=8)
    # How long generated media (!sticker images, !voz audio) stays available for the bot to download
    # before it's dropped from memory. Only needs to outlive the bot's own fetch, right after the command runs.
    MEDIA_CACHE_SECONDS: int = Field(default=120, ge=10, le=600)
    # Wall-clock ceiling for one !voz call: network synthesis + the ffmpeg conversion to Ogg/Opus.
    # Edge TTS's own timeouts aren't fully reliable (observed hangs past them), so this is enforced separately.
    TTS_TIMEOUT_SECONDS: int = Field(default=20, ge=5, le=60)
    # Proactive alerts: a background check on a fixed list of LOCAL services (from SERVICES_FILE, by name,
    # comma-separated). Empty = the feature is off. A service going down/up sends exactly one message per
    # transition (not one per check) to every chat below; nobody has to ask !service for it.
    ALERT_SERVICES: str = ""
    ALERT_CHECK_INTERVAL_SECONDS: int = Field(default=300, ge=30, le=3600)
    ALERT_WHATSAPP_GROUP_JIDS: str = ""
    ALERT_TELEGRAM_CHAT_IDS: str = ""

    @property
    def host_ignored_units(self) -> frozenset[str]:
        return frozenset(item.strip() for item in self.HOST_IGNORED_UNITS.split(",") if item.strip())

    @property
    def admin_user_keys(self) -> frozenset[str]:
        """Admin identities as "platform:user_id" (same shape as IncomingMessage.user_key)."""
        return frozenset(item.strip() for item in self.ADMIN_USER_IDS.split(",") if item.strip())

    def admin_ids_for(self, platform: str) -> list[str]:
        """Admin user ids of one platform only, e.g. for tagging them in a proactive alert."""
        prefix = f"{platform}:"
        return [key[len(prefix):] for key in self.admin_user_keys if key.startswith(prefix)]

    @property
    def command_prefixes_list(self) -> List[str]:
        return [p.strip() for p in self.COMMAND_PREFIXES.split(",") if p.strip()]

    @property
    def alert_services_list(self) -> list[str]:
        return [item.strip() for item in self.ALERT_SERVICES.split(",") if item.strip()]

    @property
    def alert_whatsapp_group_jids(self) -> list[str]:
        return [item.strip() for item in self.ALERT_WHATSAPP_GROUP_JIDS.split(",") if item.strip()]

    @property
    def alert_telegram_chat_ids(self) -> list[str]:
        return [item.strip() for item in self.ALERT_TELEGRAM_CHAT_IDS.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
