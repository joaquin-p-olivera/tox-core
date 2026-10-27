import os
from functools import lru_cache
from typing import List, Literal

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
    # Where the log is written besides the console, as one file every LOG_ROTATE_HOURS ("" = console only,
    # which is what the tests use). Old files are never deleted: they pile up until somebody removes them.
    LOG_DIR: str = "logs"
    LOG_ROTATE_HOURS: int = Field(default=24, ge=1, le=168)  # 24 = one file per day, rolled over at midnight
    ADMIN_USER_IDS: str = ""
    # The owner(s): admins everywhere, including a group they do not administer in WhatsApp. Everybody else on
    # ADMIN_USER_IDS is an admin only where the platform says they administer that group. Same shape
    # ("platform:user_id", comma separated), and being here is enough: there is no need to be on the list above too.
    OWNER_USER_IDS: str = ""
    # Folder with the sound clips !m can send (relative paths resolve against the working directory).
    # If it is missing or empty, !m simply always tags someone.
    AUDIOS_DIR: str = "media/m"
    # Chance (0-1) that !m sends a random audio instead of tagging someone
    M_AUDIO_PROBABILITY: float = Field(default=0.5, ge=0, le=1)
    # Folder with the sound clips !risa can send. If it is missing or empty, !risa says so instead.
    RISA_AUDIOS_DIR: str = "media/risa"
    # API key for the admin-only !service status command (read-only use). Empty = disabled.
    RENDER_API_KEY: str = ""
    # Local services the admin-only `!service -L` may start/stop/query: an allowlist in a JSON file
    SERVICES_FILE: str = "services.json"
    # Local directory of who is who (name -> platform ids), for commands that treat certain people specially (!gay)
    USERS_FILE: str = "users.json"
    # What each chat is about and who its people are, for !claude's context (see services/chat_profiles.py). Written by
    # the owner, so it is the only part of the context the prompt does not present as "data, not instructions".
    CHATS_FILE: str = "chats.json"
    # Names for the devices !ipmaster finds, by MAC (the router and phones don't announce one): {"mac": "name"}
    DEVICES_FILE: str = "devices.json"
    # `!claude`: runs the Claude Code CLI (`claude -p`). Admins get FULL access (it can edit files and run commands) in
    # CLAUDE_WORKDIR; everybody else gets a plain chat with NO access to this PC (no tools, no files, no MCP). Limits: a money
    # cap per question, questions per hour (admins together; others in total and per person), a wall-clock timeout, and the
    # length of the answer sent to the chat. CLAUDE_BIN empty = look it up in PATH.
    CLAUDE_WORKDIR: str = "~/dev"
    CLAUDE_BIN: str = ""
    CLAUDE_MAX_BUDGET_USD: float = Field(default=2.5, gt=0, le=100)  # ~$U 100
    CLAUDE_QUESTIONS_PER_HOUR: int = Field(default=200, ge=1, le=1000)  # admins, all together
    CLAUDE_TIMEOUT_SECONDS: int = Field(default=600, ge=30, le=3600)
    # At once: the admins' questions go one by one; the no-access ones and the summaries CLAUDE_PARALLEL at a time. The
    # rest wait in a line of CLAUDE_QUEUE_MAX places (0 = refuse straight away) and are answered in turn.
    # Now and then the answer is sent as a voice note instead of text: the share of answers (0 = never, 1 = always)
    # and the voice to read them with (a key of tts_generator.VOICES). An answer that is too long to speak, or one
    # that tags somebody, is sent as text anyway.
    CLAUDE_AUDIO_CHANCE: float = Field(default=0.25, ge=0, le=1)
    CLAUDE_AUDIO_VOICE: Literal["hombre", "mujer"] = "hombre"
    # How Tox talks: "salado" (insults and swearing, like the friends do), "picante" (sarcastic and chicanero),
    # "tranqui" (playful and joking, but it teases nobody) or "normal" (just friendly, no tone added at all). The
    # spicy ones keep a few hard limits (see TONE_PROMPTS). A chat may be given its own with !config.
    CLAUDE_TONE: Literal["normal", "tranqui", "picante", "salado"] = "salado"
    CLAUDE_PARALLEL: int = Field(default=2, ge=1, le=8)
    CLAUDE_QUEUE_MAX: int = Field(default=5, ge=0, le=50)
    CLAUDE_MAX_ANSWER_CHARS: int = Field(default=800, ge=100, le=3000)
    CLAUDE_PUBLIC_MAX_BUDGET_USD: float = Field(default=0.10, gt=0, le=100)  # an answer costs ~US$0.01-0.03
    CLAUDE_PUBLIC_QUESTIONS_PER_HOUR: int = Field(default=100, ge=1, le=1000)  # everybody else, all together
    CLAUDE_PUBLIC_QUESTIONS_PER_PERSON_PER_HOUR: int = Field(default=100, ge=1, le=1000)
    # Replying to one of the bot's own messages asks Claude without typing the command: the reply is the question
    # (the same limits apply). Never in the personal mode, where the bot's messages are the owner's own.
    CLAUDE_ON_REPLY: bool = True
    # Now and then Tox butts into a conversation nobody invited it to: it reads what is going on in the group and drops
    # one remark of its own, always as text. CLAUDE_INTERJECT_CHANCE is the share of messages that trigger one
    # (0 = never), and it never happens twice in a chat within CLAUDE_INTERJECT_COOLDOWN_MINUTES (answering a question
    # counts as having spoken). Only in groups whose history is on: what it reads is its only context.
    CLAUDE_INTERJECT_CHANCE: float = Field(default=0.02, ge=0, le=1)
    CLAUDE_INTERJECT_COOLDOWN_MINUTES: int = Field(default=30, ge=1, le=1440)
    # How often what the owner wrote about each member (CHATS_FILE's "people") is handed over when NOBODY typed
    # a command -- a remark of its own, or an answer to a reply. Knowing everybody's job and where they live
    # turns every message into the same joke about it, so most of the time it is left out and Tox has to talk
    # about what is being said. The chat's own description always goes, and a typed !claude always gets it all.
    CLAUDE_PROFILE_PEOPLE_CHANCE: float = Field(default=0.25, ge=0, le=1)
    # Context for everybody without access to the PC, in the chats where the owner turned the history on (!h -on): the
    # latest CLAUDE_CONTEXT_MESSAGES messages of the last CLAUDE_CONTEXT_HOURS hours go with the question (0 = none).
    # The admins' full-access mode never gets them: anybody in the group could write instructions there.
    CLAUDE_CONTEXT_MESSAGES: int = Field(default=150, ge=0, le=200)
    CLAUDE_CONTEXT_MAX_CHARS: int = Field(default=20_000, ge=1000, le=100_000)  # ~5000 tokens; the oldest go first
    CLAUDE_CONTEXT_HOURS: int = Field(default=12, ge=1, le=168)
    # A chat may also be told what was said in ANOTHER one (models/chat_context_source.py, by hand in the database):
    # how many of its latest messages go, and how much room they get. Only when the question names that source, so
    # most answers never see them. The same CLAUDE_CONTEXT_HOURS bounds how far back they are read.
    CLAUDE_SOURCE_MESSAGES: int = Field(default=50, ge=0, le=200)
    CLAUDE_SOURCE_MAX_CHARS: int = Field(default=6000, ge=500, le=50_000)
    # Chat history (!h, !resumen): the text of the chats the owner turned it on for, kept in a SQLite file of its
    # own (not tox.db). Once it passes HISTORY_MAX_MB the oldest messages are deleted. `!resumen` hands the latest
    # HISTORY_SUMMARY_MAX_MESSAGES of a chat to Claude (no tools, no access to this PC), with its own money cap.
    HISTORY_DB_PATH: str = "history.sqlite"
    HISTORY_MAX_MB: int = Field(default=1024, ge=1, le=102400)
    HISTORY_SUMMARY_MAX_MESSAGES: int = Field(default=500, ge=10, le=2000)
    HISTORY_SUMMARY_MAX_CHARS: int = Field(default=1500, ge=200, le=3000)
    CLAUDE_SUMMARY_MAX_BUDGET_USD: float = Field(default=0.5, gt=0, le=100)  # ~$U 20; 500 messages cost far less
    # Time zone of the clock times people type (`!recordar 18:30 ...`): an IANA name
    TIMEZONE: str = "America/Montevideo"
    # `!web` (admin only): the apps it may open in a remote browser, by name (see webapps.example.json), and how long the
    # browser stays open without use (it takes ~450 MB of RAM while open)
    WEB_APPS_FILE: str = "webapps.json"
    WEB_IDLE_SECONDS: int = Field(default=180, ge=30, le=1800)
    # Extra wait before every screenshot so the page can finish loading its data (it arrives after the load event)
    WEB_SCREENSHOT_DELAY_SECONDS: float = Field(default=3.0, ge=0, le=15)
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
    # `!futbol` (público): fixtures o tabla de la liga top de Uruguay, Ecuador, Argentina o España,
    # vía la API pública (no oficial, sin key) de ESPN. No requiere credenciales.
    FOOTBALL_TIMEOUT_SECONDS: int = Field(default=6, ge=1, le=10)
    FOOTBALL_CACHE_TTL_SECONDS: int = Field(default=86400, ge=300, le=86400)  # 24 h por defecto, como !github
    # The API records CPU, memory, temperature and battery of this machine every N seconds (0 = off) to show peaks and
    # battery trends in `!service -L host`. Samples older than HOST_HISTORY_DAYS are deleted.
    HOST_SAMPLE_INTERVAL_SECONDS: int = Field(default=60, ge=0, le=3600)
    HOST_HISTORY_DAYS: int = Field(default=7, ge=1, le=90)
    # Per request. Keep the total (list + deploys, roughly 2x this) below the bots' API timeout (10 s).
    RENDER_API_TIMEOUT_SECONDS: int = Field(default=4, ge=1, le=8)
    # How long generated media (!sticker images, !voz audio) stays available for the bot to download
    # before it's dropped from memory. Only needs to outlive the bot's own fetch, right after the command runs.
    MEDIA_CACHE_SECONDS: int = Field(default=120, ge=10, le=600)
    # Pictures for the GTA VI countdown card (services/gta_card.py): one of GTA_ARTWORK_DIR at random on each card,
    # and GTA_COVER_FILE, always, on the one sent the day it comes out. `media/` is git-ignored, so they are the
    # owner's own (Rockstar hands them out as wallpapers). Nothing readable there = the sunset the card draws itself.
    GTA_ARTWORK_DIR: str = "media/gta6"
    GTA_COVER_FILE: str = "media/gta6/cover.jpg"
    # The same countdown, published as an Instagram story (services/instagram.py). Off unless all three are set.
    # The account has to be a professional BUSINESS one: stories cannot be published to a creator account. The token
    # comes from the app dashboard (Instagram > API setup with Instagram login) and lasts 60 days.
    # IMGBB_API_KEY is where the picture is parked for a moment: Instagram downloads it from a public URL, it never
    # receives the bytes. The upload asks for it to be deleted again after INSTAGRAM_IMAGE_TTL_SECONDS.
    INSTAGRAM_ENABLED: bool = False
    INSTAGRAM_USER_ID: str = ""
    INSTAGRAM_ACCESS_TOKEN: str = ""
    IMGBB_API_KEY: str = ""
    INSTAGRAM_TIMEOUT_SECONDS: int = Field(default=30, ge=5, le=120)
    INSTAGRAM_IMAGE_TTL_SECONDS: int = Field(default=600, ge=60, le=86400)
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

    @property
    def owner_user_keys(self) -> frozenset[str]:
        """Who is an admin in every chat, whoever runs it (OWNER_USER_IDS)."""
        return frozenset(item.strip() for item in self.OWNER_USER_IDS.split(",") if item.strip())

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
