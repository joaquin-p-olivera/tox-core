# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `claude` answers can be twice as long: `CLAUDE_MAX_ANSWER_CHARS` is 800 (was 400), and the voice notes take 500 characters (was 300), `!voz` included. Both limits go into the prompt and are enforced afterwards -- the answer is still cut to `CLAUDE_MAX_ANSWER_CHARS`, and one that does not fit in a voice note is still sent as text. The prompt for a spoken answer now asks for `SPOKEN_SLACK` (20) characters under the voice limit instead of a number of its own, so the two can no longer drift apart.
- What the chat profile says about each member reaches Claude only `CLAUDE_PROFILE_PEOPLE_CHANCE` of the time (0.25) when nobody typed a command -- a remark of its own, or an answer to a reply -- so it stops making the same joke about somebody's job or city every time it opens its mouth. The chat's own description always goes, and a typed `!claude` still gets everything.
- `chats.json` can tell the elementary facts of a person apart from the rest: `"Nombre": {"basics": "34 años, vive afuera", "about": "..."}`. The `basics` go to Claude every time it speaks; the `about` is what the dice above decides. A person written as a string or a list is all `about`, as before.
- `start_dev.sh` only reinstalls the dependencies when `requirements-dev.txt` is newer than the last install (a service that restarts on failure must not reach the network every time), `exec`s uvicorn, so a stop signal reaches it directly, and no longer passes `--reload`: it is what the systemd service runs, where a reloader only adds a process watching files nobody is editing.
- When a `claude` (or `res`) answer arrives the `⏳` reaction is taken off instead of being replaced by a `✅`: a reply with an empty `reaction` now means "revoke the reaction you left there" (`null` still means "leave them alone"). `⚠️` is still used when there is no answer.

### Added

- `!claude -t <pregunta>` (`!c -t ...`, `.b c -t ...` in the personal mode) answers as text for certain, instead of leaving it to `CLAUDE_AUDIO_CHANCE`: the dice is not thrown at all. It is for everybody, not only the admins, it combines with `-n` in any order, and it is read when replying to a message of the bot too, where there is no command to write a flag on. Without it nothing changes, and a question that merely starts with a dash is still asked as typed.
- `!censura @usuario` (admin, groups only): a switch that makes the bot delete every message that person sends in that group, their commands included, until the same command lifts it. It is per chat, and anybody can be censored except the bot and whoever runs the command (a group admin and a listed admin included: nobody leaves the group and the same command undoes it). The API only keeps the list (`censored_users`) and serves it at `GET /api/v1/censored`; the bot reads it and deletes on its own, which is the only way it reaches the messages the API never sees (ordinary chatter of a group with history off). The bot has to be an admin of the group.
- The log is written to a file besides the console: one per `LOG_ROTATE_HOURS` (24 by default, rolled over at midnight) in `LOG_DIR` (`logs/`, git-ignored), as `tox.log` plus `tox-2026-10-20.log` for the days gone by. uvicorn's own lines (startup, the access log) go to the same file. Nothing is deleted automatically: old files are removed by hand. `LOG_DIR=` (empty) keeps it on the console only, which is what the tests do.
- `start_test.sh`, the API while writing code: the same thing as `./start_dev.sh` (dev, `.env`, `requirements-dev.txt`) with uvicorn's reloader on, so every save restarts it.
- `tox-core.service`, a systemd user unit running `./start_dev.sh`, documented in the README (the unit itself lives in `~/.config/systemd/user/`, outside the repo).
- `claude` butts into a conversation now and then (`CLAUDE_INTERJECT_CHANCE`, 0.02 of the messages, `CLAUDE_INTERJECT_COOLDOWN_MINUTES` between remarks): in a group whose history is on it reads what is going on and sends one remark of its own, always as text, always without access to the PC and signed with a `> Tox` line. Claude decides whether it is worth it at all (`paso` sends nothing), and everything about it is silent: no reaction, no busy or limit message. The bot now forwards ordinary chatter of those groups (without downloading its pictures), which is what makes it possible.
- Replying to a message of the bot asks `claude`, with no command typed (`CLAUDE_ON_REPLY`, on by default): the whole reply is the question and the answer continues the thread. A reply to a voice note counts too, and the personal mode is left out (there the bot writes as the owner). The question is not saved to the history twice, since the bot already saves everything that is not a command.
- `claude` answers are sent as a voice note `CLAUDE_AUDIO_CHANCE` (0.25) of the time, in `CLAUDE_AUDIO_VOICE` (`!voz -h`'s voice). Decided before asking, so a spoken answer is asked for short and tagless; it falls back to text when it tags somebody, is too long or the voice service fails.
- Per-chat profile for `claude`: `CHATS_FILE` (`chats.json`, git-ignored, `chats.example.json` tracked) holds each chat's `name`, `about`, `people` and `notes`, read on every call (`app/services/chat_profiles.py`) and handed to both modes as the owner's own description of the chat (the rest of the context stays untrusted data).
- `claude` context: `CLAUDE_CONTEXT_MESSAGES` is 150 by default and `CLAUDE_CONTEXT_MAX_CHARS` (20000) bounds it; `CLAUDE_PUBLIC_MAX_BUDGET_USD` is 0.10. In a chat with the history on, every `!claude` exchange (the question and the answer sent to the chat, admin or not) is saved in it (`claude_context.remember`), so the no-access mode keeps the thread whoever asks. The admins' CLI session stays separate and never reads others' text.
- `claude` no longer answers "Ya hay una consulta en curso": questions wait their turn (`CLAUDE_PARALLEL` no-access questions and summaries at once, the admins' full-access ones one by one, `CLAUDE_QUEUE_MAX` places in line; `Busy` only when it is full). New `CLAUDE_TONE` (`salado` by default; `picante`, `normal`): the no-access prompt insults and swears like the friends do, with a few explicit limits.
- `claude` has the context of the group: the names of the members (`USERS_FILE` first), the message it replies to (new `quoted` field on `POST /api/v1/messages`) and, without PC access and only where the history is on, the latest `CLAUDE_CONTEXT_MESSAGES` messages of the last `CLAUDE_CONTEXT_HOURS` hours (`app/services/claude_context.py`). The no-access prompt now speaks as one more member of the group, and an `@Name` in the answer becomes a real mention. The admins' mode never gets the conversation. `!c <question>` is an alias when there is a question (`!c` alone stays the trivia answer). `chat_history.transcript` is shared with `res`.
- uvicorn's own log lines (startup, the access log) now carry the date and time, like the app's (`timestamp_uvicorn_logs` in `app/main.py`).
- Chat history: `h -on | -off | -status | -delete` (alias `historial`; `owner_only`: from the owner's personal bot or by an admin through the groups bot, refused with 🚫 and unlisted for anyone else) saves the text of a chat to a SQLite file of its own (`HISTORY_DB_PATH`, git-ignored; oldest messages deleted past `HISTORY_MAX_MB`), and `res [-today | -all | 6h | 2d | 150]` (alias `resumen`; today's messages by default) has Claude summarise it (`claude_runner.execute_summary`, no tools, own money cap). New `GET /api/v1/history/chats` and `POST /api/v1/history`, `chat_aliases` on `POST /api/v1/messages`, and the `history_chats` table.
- `personal` field on `POST /api/v1/messages`, set by the WhatsApp bot's personal mode (the owner's own number, private chats): commands there start with `.b ` (`.b sticker`, replies show that prefix), the sender is never treated as an admin, and `recordar` is refused (the groups bot would deliver it to that chat from its own number).
- Initial FastAPI scaffold: settings (`.env` / `.env.prod` selected by `APP_ENV`), SQLite database, API key authentication (`X-API-Key`), `/health`.
- `POST /api/v1/messages`: platform-agnostic entry point the bots forward chat messages to.
- Command framework with decorator-based registry, `!` and `/` prefixes, aliases and an auto-generated `!help`.
- Commands, all in Spanish: `ayuda`, `id`, `chiste`, `dado`, `elegir`, `m`, `uuid`, `base64`.
- Developer tools: `hash`, `url`.
- Jokes are general only (general, dark and spicy); no programming or gaming jokes.
- `m`: asks `y tu mamá donde está?` and mentions a random chat member other than the sender.
- Replies can carry `mentions` (`{@0}` placeholders) and messages can carry the chat's `participants`; users seen in a chat are remembered as a fallback member list.
- `m` also sends a random audio from `media/m` (`AUDIOS_DIR`, `M_AUDIO_PROBABILITY`); skipped when the folder is missing or empty. `GET /api/v1/audios/{name}` serves the clips to the bots; `media/` is git-ignored.
- `m` never repeats the same audio twice in a row in a chat (last audio remembered per chat in `last_audios`).
- `mute`: toggles whether the sender can be tagged in the chat. Muted users are excluded from `m` and any future tagging command (`taggable_members`).
- `service` (admin only): shows whether the hosted services and databases (Postgres, key-value) are up, or one in detail (status, region, last deploys, panel link, database expiry), using the provider's read-only REST API (`RENDER_API_KEY`). Names matching several resources list them all. The detail also shows memory and CPU of the last hour (provider metrics API; 💤 when there are none, i.e. probably asleep), only the last 2 deploys, and each deploy ends with a short local date and time.
- `service -L` (admin only): query (default), start (`-up`) or stop (`-down`) the local services of this PC from an allowlist (`services.json`), without a shell, with a timeout and an audit log entry per action.
- `service -L <name>` shows a detailed status when the service defines `info` / `last_usage`: since when it is up or down, memory, CPU (now, average, total) and the last run's usage. A stopped service now shows 🔴.
- `service -L` with no service name prints a one-line usage instead of listing the services. An unknown name reports configuration problems of `services.json`, if any.
- `service -L host` (or `master`): read-only report of the machine the API runs on: uptime, CPU (load, per-core usage, model, frequency, thermal throttling), temperature, memory, swap, disks, battery (level, plug, health, autonomy or time to full), biggest memory users, failed units, pending reboot and remote sessions. A background sampler (`HOST_SAMPLE_INTERVAL_SECONDS`, `HOST_HISTORY_DAYS`) records vitals in `host_samples` to show peaks and battery trends.
- `github` / `gh` (admin only): open PRs (`-r`, the default) or issues (`-i`) of one repo of `GITHUB_REPOS` (a name or part of it; a repo is required, `!github` alone prints a usage helper). PRs show author, assignees, days open, commit count, draft/open, head → base branch and link, most days open first; issues show number, title and assignee. Read-only GitHub REST calls; private repos through `GITHUB_TOKEN` or the `gh` CLI session (`GITHUB_TOKEN_FROM_GH`).
- Admin-only commands are hidden from `ayuda` for non-admins.
- Admin support: `ADMIN_USER_IDS` setting, `@command(admin_only=True)`, and `!id` shows the caller's role.
- Multiplayer trivia game (`trivia`, `responder`, `ranking`) with 72 questions in 7 categories (general knowledge, Uruguay and games) and a per-chat leaderboard.
- `sticker <texto>`: generates a meme-style sticker (512×512 WEBP, Pillow) with the given text. Fully in-memory, nothing written to disk.
- `voz [-h | -m] <texto>`: says the text out loud with Microsoft Edge's free neural voices (`edge-tts`; `-h` a man's voice, default; `-m` a woman's; both Uruguayan Spanish), converted to Ogg/Opus with `ffmpeg` so it plays as a real voice message on both WhatsApp and Telegram. Reuses `!m`'s `audio` field and `GET /api/v1/audios/{id}` endpoint, so the bots needed no changes.
- `media_cache`: a short-lived, in-memory (RAM only) store shared by `sticker` and `voz` for their generated bytes (`MEDIA_CACHE_SECONDS`); `GET /api/v1/stickers/{id}` and the `audios` endpoint's fallback serve from it. Nothing generated by either command is ever saved to disk.
- Test suite (pytest) covering the router, every command, the trivia rules and API authentication.
- Proactive alerts: a background monitor (`ALERT_SERVICES`, `ALERT_CHECK_INTERVAL_SECONDS`) checks local
  services (the same ones `!service -L` reads) and sends a message with no user command behind it — once
  when a service goes down, once more when it recovers, nothing on every check in between. Queued per
  destination chat (`ALERT_WHATSAPP_GROUP_JIDS`, `ALERT_TELEGRAM_CHAT_IDS`) in `pending_alerts`; each bot
  polls its own with `GET /api/v1/alerts/pending?platform=...` (fetch-and-delete, at-most-once delivery).
- The alert message includes the same extra detail as `!service -L <name>` (since when up/down, memory,
  CPU, restarts) when the service defines `info` in `services.json`.
- `risa`: sends a random laugh audio from `media/risa` (`RISA_AUDIOS_DIR`), reusing `!m`'s audio machinery
  (`audio_library`, `audio_picker`, `GET /api/v1/audios/{name}`) but with its own folder and its own
  per-chat "last sent" history, independent of `!m`'s.
- `uid` (admin only, hidden): prints the id and name of every user of the chat (plus the sender) in the API's log, as
  `platform:user_id — name | other ids` (phone number when WhatsApp shares it; a missing name is completed from the users
  the API has seen), ready to copy into lists like `ADMIN_USER_IDS`. It sends no message: the bot only reacts to
  the command with 👍. Needed a `hidden` flag on `@command` (not listed by `!ayuda`; non-admins get no reply at all) and a
  `reaction` field on replies, which both bots now understand.
- A non-admin using an admin-only command (`service`, `github`, `uid`) no longer gets the "solo para administradores" text:
  the bot reacts to the command with 🚫 instead (`Reply.reaction`). Telegram only accepts a fixed set of reaction emojis, so
  there a refused reaction is just logged.
- Admin-only commands in a WhatsApp group now require being on `ADMIN_USER_IDS` **and** an administrator of that group: the
  bot reports `sender_is_group_admin` (refused when the roster can't be read) and `CommandContext.is_admin` checks it. Private
  chats and platforms that don't report it still use the list only. `!id` explains when you're listed but not a group admin.
- `web` / `w` (admin only, "Administración"): a remote browser driven from the chat. Opens an app of `webapps.json` (`WEB_APPS_FILE`,
  git-ignored) in an invisible Chromium on this PC (Playwright), numbers every visible button/link/field on a screenshot and lets you
  tap/type/scroll by number (`app/services/remote_browser.py`). Opening an app with credentials in the file logs in by itself (`login` repeats
  it). Same-origin navigation only, no downloads or pop-ups, the credentials are typed from the file (never through the chat or the log), one session at a time that closes itself after `WEB_IDLE_SECONDS`, and a pause before each screenshot (`WEB_SCREENSHOT_DELAY_SECONDS`, 3 s) for the
  page to finish loading its data. Tested
  against a real Chromium.
- `fred` (admin only, "Administración"): the URLs of the Fred frontend (`:5200`) and admin module (`:5201`) on this PC's LAN address,
  in one message with a blank line between the title and each app, each marked by a real TCP connection: 🟢 answers on the LAN address, 🟡 up but
  only reachable from this PC (listening on localhost, `(solo desde esta PC)`), 🔴 down. The four Fred user
  services (`fred-frontend`, `fred-admin-module`, `fred-backend`, `fred-db`) were added to `services.json` (start/stop/status, `fred-db`
  with `pg_isready`) and to `ALERT_SERVICES`.
- `ipmaster` / `ipm` (admin only, "Administración"): this PC's IP on the local network and the other devices connected to it, with MAC and
  manufacturer (IEEE list) and the router marked. One ping per host of the PC's own /24, then the kernel's neighbour table is
  read (`app/services/network_scan.py`): no extra tools, no root, ~4 s. Devices get a name when they announce one over mDNS
  (`avahi-browse`, in parallel with the pings) or when listed by MAC in `devices.json` (`DEVICES_FILE`, git-ignored; yours wins).
- `users.json` (`USERS_FILE`, git-ignored; `users.example.json` is tracked): a local directory of name -> platform ids
  (several per person on purpose: WhatsApp uses LID in some places and phone number in others), built from `!uid`'s
  output and read on every call (`app/services/user_directory.py`). Lets a command treat certain people specially.
- `gay`: "Leandro 1899" always gets 1899 % (matched through the directory, not the display name). Tagging the bot itself
  answers `Yo no soy gay como vos <emoji picaro>` instead; for that the bots flag a tag of themselves (`is_bot` on a
  `mentioned` user).
- `gay @usuario`: replies with a random 0-100 % "gay" rating for the tagged user (and tags them back). Needed a new
  optional `mentioned` field on `POST /api/v1/messages` (the users the sender tagged), which the bots now fill in.
- `futbol` / `fut <país>`: this week's fixtures for Uruguay/Ecuador/Argentina/España's top league, via
  ESPN's public API (no key needed). API-Football was tried first but its free plan doesn't cover the
  current season, only 2022-2024, so it was dropped.
- `tabla <país>`: the current standings table, sent as an image (`app/services/table_image.py`, Pillow)
  via the same `media_cache` + `GET /api/v1/images/{id}` mechanism as `!sticker`/`!voz`; falls back to
  plain text if rendering fails. A league split into real zones (Argentina's this season) shows each
  zone as its own ranked table. Was originally `futbol -t`, split into its own command. A league's
  active stage isn't hardcoded either: it's read from ESPN (confirmed real for Uruguay: Apertura,
  Intermedio, Clausura and playoffs each have a different id), falling back to the regular season if
  the current stage doesn't have a table populated yet.
- `!futbol`/`!tabla` results are cached like `!github`'s (`cachetools.TTLCache`), now
  `FOOTBALL_CACHE_TTL_SECONDS` = 24 h by default (raised from 30 min: no quota to protect here, just
  being kind to an undocumented API). The rendered table image itself isn't cached separately —
  building it from the already-cached data takes well under a second even for the largest table.
- `qr <texto o link>`: a QR code PNG (`qrcode`), sent as an image.
- `foto` (alias `imagen`): reply to a sticker and get it back as a PNG picture (transparent areas white, animated stickers: first frame). The WhatsApp bot now also forwards a quoted sticker in the `image` field.
- `sticker` also accepts a picture (sent with `!sticker` as caption or replied to): scaled to a 512×512 transparent WEBP. New optional `image` (base64) field on `POST /api/v1/messages`, filled by the WhatsApp bot.
- `recordar <HH:MM> <qué>`: reminders delivered in the same chat, tagging who asked, through the existing pending-alerts queue (`reminders` table; due ones are queued on each bot poll). `TIMEZONE` setting.
- `wol [número o nombre]` (admin only): Wake-on-LAN for the devices listed in `devices.json`; with no argument it lists them.
- `speedtest` (admin only): latency, download and upload against Cloudflare's public speed-test endpoints.
- Moderation (admin only, groups only): `ban`, `permaban` (blacklist: removed from every group the bot is in and again if they return), `unpermaban` (lists / removes from the blacklist), `ruleta-ban` (random non-admin member, confirmed by an admin with `si` / `no`). New reply fields `remove` and `sweep_blacklist`, `is_group_admin` on participants, `GET /api/v1/blacklist`. Command names may contain hyphens.
- `cumple <dd/mm/aaaa | dd/mm>`: registers a birthday (the year is optional); admins can set someone else's by tagging them (overwrites).
- `fred` now sends the state of the apps in one message and then one message per app with only its URL; `fred-studio` (port 5203, local-only by design) was added.
- `claude [-n] <pregunta>`: open to everyone. Admins: runs the Claude Code CLI in `CLAUDE_WORKDIR` with full access; everybody else: a plain chat with no access to the PC (no tools, no MCP, empty temporary folder, stateless; `CLAUDE_PUBLIC_*` limits, e-mail addresses masked). The admin mode runs the CLI in `CLAUDE_WORKDIR` with full access, one question at a time, with a per-question budget, a per-hour limit, a timeout, an answer cut to 400 characters and secrets scrubbed. Reacts with ⏳ to the command at once and sends the real answer through a new job mechanism (`job` on a reply, `GET /api/v1/jobs/{id}`), swapping the reaction for ✅ (⚠️ on an error).

### Fixed

- `voz` had been timing out for days: `edge-tts` 7.2.8 hangs for ever against Microsoft's service (its own timeouts never fire, only the command's wall-clock guard does). Pinned to 7.3.1, which answers in ~2 s.
