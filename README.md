# Tox API

Brain of the Tox chat bots. The bots ([tox-whatsapp-bot](../tox-whatsapp-bot),
[tox-telegram-bot](../tox-telegram-bot)) are thin front-ends: they forward every
command-looking message here and send back the replies. **All command logic, game state
and content live in this API**, so a command written once works on every platform.

## Stack

- FastAPI
- SQLAlchemy 2.0 + SQLite (local file, created on first start)
- Pydantic Settings

## Local development

```bash
cp .env.default .env   # set API_KEY (see the comment inside for a one-liner)
./start_dev.sh         # creates .venv, installs deps, runs uvicorn with --reload on 127.0.0.1:8000
```

**System dependency:** `!voz` needs `ffmpeg` on `PATH` (not a pip package — install it separately, e.g. `apt install
ffmpeg` or a static build). Without it, `!voz` fails with a clear error; every other command works fine.

Interactive docs: <http://127.0.0.1:8000/docs>. Run the tests with:

```bash
.venv/bin/python -m pytest
```

### Environments

| Script            | `APP_ENV` | Env file     |
| ----------------- | --------- | ------------ |
| `./start_dev.sh`  | `dev`     | `.env`       |
| `./start_prod.sh` | `prod`    | `.env.prod`  |

`.env` and `.env.prod` are git-ignored; only `.env.default` (the template) is tracked.
Real environment variables always win over the files.

The API binds to `127.0.0.1` by default (bots run on the same machine). Set `HOST=0.0.0.0` only if you
put something in front of it.

## API

Every endpoint except `/health` requires the `X-API-Key` header (the `API_KEY` setting).

### `POST /api/v1/messages`

```json
{
  "platform": "whatsapp",
  "chat_id": "120363012345678901@g.us",
  "user_id": "198765432100@lid",
  "user_name": "Alice",
  "text": "!roll 2d6",
  "is_group": true
}
```

Response — one entry per message the bot must send, empty when the text isn't a command:

```json
{ "replies": [{ "text": "🎲 3 + 5 = 8 (2d6)", "mentions": [], "audio": null }] }
```

`participants` is optional: the full member list when the platform can provide it (WhatsApp), each with an optional `aliases` list of the same person's other IDs. Its `user_id`s must
use the same form as the sender's. Without it, the API falls back to the users it has already seen using the bot in
that chat.

A reply can mention users. Its `text` then contains `{@0}`, `{@1}`... placeholders that the bot replaces with a real
mention of `mentions[0]`, `mentions[1]`... in its platform's own way (WhatsApp `@number`, Telegram `text_mention`):

```json
{ "text": "y tu mamá donde está? {@0}", "mentions": [{ "user_id": "59899222222@s.whatsapp.net", "user_name": "Bob" }] }
```

Commands start with `!` or `/` (configurable via `COMMAND_PREFIXES`). Unknown commands get no reply
on purpose, so the bot stays quiet when other bots share the prefix. A Telegram-style `/ping@botname`
suffix is accepted.

## Commands

Everything the chat sees is in Spanish (command names included). Code, docs and logs are in English.

| Command                                | Aliases                    | Description                                               |
| -------------------------------------- | -------------------------- | --------------------------------------------------------- |
| `ayuda [comando]`                      | `help`, `comandos`         | Lists commands or explains one                            |
| `id`                                   |                            | Shows the platform/chat/user IDs and your role            |
| `mute`                                 |                            | Toggle: stop (or resume) being tagged by the bot in this chat |
| `github <repo> [-r \| -i]`             | `gh`                       | **Admin only.** Open PRs (`-r`, default) or issues (`-i`) of one of your configured GitHub repos |
| `service [nombre]`                     |                            | **Admin only.** Whether your hosted services and databases are up; with a name, the detail of one |
| `service -L [nombre] [-up \| -down]`    |                            | **Admin only.** Query (default), start (`-up`) or stop (`-down`) the *local* services of this PC (see below) |
| `chiste`                               |                            | Random joke (general, dark and spicy; no tech jokes)      |
| `dado [NdM \| M]`                      | `dados`                    | Dice, default `1d6` (e.g. `2d6`, `d20`, `20`)             |
| `elegir a \| b \| c`                   |                            | Picks one option (separate with `\|` or `,`)              |
| `m`                                    |                            | Randomly: `y tu mamá donde está? @someone` (never the sender) or a random audio from `media/m` |
| `risa`                                 |                            | Sends a random laugh audio from `media/risa`               |
| `futbol <país>`                        | `fut`                      | This week's fixtures for Uruguay/Ecuador/Argentina/España's top league |
| `tabla <país>`                         |                            | The current standings table, as an image                  |
| `trivia [categoría]`                   | `preguntas`                | Starts a multiplayer trivia question                      |
| `responder <A-D>`                      | `a`, `b`, `c`, `d`         | Answers the open question (`!b` is a shortcut)            |
| `ranking`                              | `top`, `puntajes`          | Per-chat trivia leaderboard                               |
| `uuid`                                 |                            | Random UUID v4                                            |
| `base64 codificar\|decodificar <texto>` | `b64`                      | Base64 helper                                             |
| `url codificar\|decodificar <texto>`   |                            | URL-encode / decode                                       |
| `hash <md5\|sha1\|sha256\|sha512> <texto>` |                        | Hash of a text                                            |
| `sticker <texto>`                      |                            | Generates a meme-style sticker with the given text        |
| `voz [-h \| -m] <texto>`               |                            | Says the text out loud: `-h` a man's voice (default), `-m` a woman's       |

### `!m`: text or audio

Each time, `!m` rolls the dice (`M_AUDIO_PROBABILITY`, default `0.5`): either it sends a random sound clip, or it tags a random
member with `y tu mamá donde está? @someone`. If nobody else is known in the chat but there are audios, it sends an audio.

The same audio is never sent twice in a row in a chat: the last one is remembered per chat (in the database) and left out of the next draw
(with a single audio there's nothing else to pick, so it repeats). Tagging someone doesn't change that history.

**Sound clips** live in `media/m/` (`AUDIOS_DIR`). Drop `.m4a`, `.mp3`, `.ogg`, `.opus` or `.aac` files there (max 16 MB each);
new files are picked up without restarting. The whole `media/` folder is git-ignored, so the clips stay on your machine.
If `media/m` doesn't exist or has no usable files, the audio branch is skipped and `!m` always tags someone.

The bots download the clips from `GET /api/v1/audios/{name}` (needs the API key; only files inside `media/m` can be requested).
A reply that carries an audio has `"audio": "risa.m4a"` and empty `text`.

WhatsApp shows `.ogg`/`.opus` (Opus) as a voice note and every other format as a plain audio file. Telegram plays all of them as a voice message.

To get voice notes from other formats, convert them with `ffmpeg` (keep the originals outside `media/m`, otherwise both
versions would be picked):

```bash
mkdir -p media/originals
ffmpeg -i media/originals/1.m4a -vn -map_metadata -1 -c:a libopus -b:a 48k -ar 48000 -ac 1 -application voip media/m/1.ogg
```

### `!risa`

Same mechanism as `!m`'s audio branch (`audio_library` + `audio_picker`), but its own folder (`media/risa`,
`RISA_AUDIOS_DIR`) and its own "last sent" history per chat, so it never interferes with `!m`'s. If the folder
is missing or empty, it says so instead of failing silently.

### `!sticker`, `!voz` and `!tabla`: generated media

All three generate content on the fly and hand it to the bots as ordinary bytes over HTTP — nothing is ever written
to disk. `app/services/media_cache.py` is a short-lived, in-memory (RAM only) store: a command renders the bytes once,
keeps them there for `MEDIA_CACHE_SECONDS` (default 120 s — only needs to outlive the bot's own download, right after
the reply), and they're gone. If the API restarts before a bot fetches one, it's simply lost; the chat can just ask again.
`!tabla`'s table image (`GET /api/v1/images/{id}`) reuses this same cache, alongside stickers and TTS audio.

**`!sticker <texto>`** renders a classic meme-style sticker (`app/services/sticker_generator.py`, via Pillow): a
512×512 WEBP, white background, bold black text, centered and auto-wrapped to fit (up to 200 characters). The reply
carries `"sticker": "<id>"`, fetched from `GET /api/v1/stickers/{id}`.

**`!voz [-h | -m] <texto>`** (up to 300 characters) says the text out loud using Microsoft Edge's free, unofficial
neural voices (`app/services/tts_generator.py`) — the same engine behind Edge's "Read aloud" feature, no API key.
`-h` (default) is a man's voice, `-m` a woman's; both are Uruguayan Spanish (`es-UY-MateoNeural` /
`es-UY-ValentinaNeural`). Only a *leading* flag is recognised, so a sentence that happens to start with a hyphenated
word other than `-h`/`-m` is rejected with the usage line rather than mis-read, while one later in the sentence
(`el resultado fue -3`) is spoken literally. The MP3 that Edge returns is converted to Ogg/Opus with `ffmpeg`
(a required external dependency, invoked as a subprocess piping bytes in and out — no temp files), matching `!m`'s
audio: a WhatsApp voice note, and the only format Telegram's `sendVoice` accepts as an actual voice message. The
reply reuses the same `"audio": "<id>"` field and `GET /api/v1/audios/{id}` endpoint as `!m` (which first tries
`AUDIOS_DIR`, then falls back to the media cache), so the bots needed no changes at all for this command.
Synthesis and conversion each run under a hard wall-clock timeout (`TTS_TIMEOUT_SECONDS`, default 20 s) enforced
independently of Edge TTS's own timeouts, which were observed to not always fire on a broken connection.

`!voz` sends the text to Microsoft's servers over the network each time it's used (an unofficial client of the same
backend Edge's browser uses, not an affiliated/official API) — that's the trade-off for not running a heavy model on
this machine. `!sticker` is fully local (Pillow only, no network).

### `!mute`

`!mute` adds the sender to a per-chat list of people who must not be tagged; sending it again removes them, so the same
command mutes and un-mutes. A muted user still uses the bot normally (they can even run `!m`); they just never get tagged.
Every command that tags someone must pick from `chat_members.taggable_members()` / `pick_random_other()`, which already
exclude the sender and the muted users, so future commands respect it for free.

On WhatsApp one person can appear as an LID or as a phone number, so each participant may carry `aliases` (their other IDs) and a mute
matches any of them.

### Who counts as a member for `!m`

- **WhatsApp:** the bot sends the group's full member list (minus the bot itself), so anyone in the group can be picked,
  even if they never wrote to the bot.
- **Telegram:** bots can't list members, so only people who have already sent a command to the bot in that chat can be picked.
- The sender is never picked. In a private chat, or if nobody else is known, the bot says so instead.

### Trivia game

One open question per chat; everybody in the chat competes.

- `!trivia` posts a question with options A–D (72 questions). Optional category: `geografia`, `ciencia`, `historia`,
  `cultura`, `deportes`, `uruguay`, `juegos` (accents and case don't matter).
- Each player gets **one attempt** per question (`!a`…`!d`). The first correct answer wins
  10 points plus up to 5 speed bonus points.
- Unanswered questions expire after `TRIVIA_TIMEOUT_SECONDS` (60). Expiry is lazy: the answer is revealed
  on the next `!trivia`/`!a`, because the API never pushes messages on its own.
- Answer positions are shuffled per round; recently asked questions aren't repeated.
- Claiming a round is an atomic `UPDATE`, so two simultaneous correct answers can't both win.

## Admins

`ADMIN_USER_IDS` in `.env` lists the admins as comma-separated `platform:user_id` (the IDs are platform-specific, so
the platform prefix matters):

```
ADMIN_USER_IDS=whatsapp:111111111111111@lid,telegram:123456789
```

Send `!id` in a chat: it shows your user ID and `Rol: admin` or `Rol: usuario`. Mark a command as admin-only with
`@command(..., admin_only=True)`; everybody else gets "Este comando es solo para administradores." No admin-only
commands exist yet. Note that WhatsApp may identify the same person by LID in groups and by phone number elsewhere;
list the form you see in `!id` for the chats where you need it.

## Service status (admin only)

`!service` lists your hosted services **and databases** (Postgres and key-value) and whether they are up; `!service finview-backend`
shows one in detail: status, region, **memory and CPU of the last hour**, the last 2 deploys (each ends with a short local `- dd/mm HH:MM`) and the panel link; databases also show when they expire.
If the text matches several (e.g. `finview` matches the backend and its database), it lists all the matches; use the full name for the detail. Only admins can run it, and it is hidden from `!ayuda` for everyone else.

```
☁️ 3 servicios, 1 para revisar
🟡 finview (web) — desplegando… (sigue la versión anterior)
🟢 trip-trace-api (web) — en línea
⏸️ viejo (web) — suspendido
```

**Setup:** create an API key in your hosting provider's dashboard and put it in the API's `.env` (never in a chat):
`RENDER_API_KEY=...`. Restart the API to load it. Until then the command says it isn't configured.

How the state is decided (services: the `suspended` flag and the latest 5 deploys; databases: their status):

| Icon | Meaning |
| --- | --- |
| 🟢 | Latest deploy is `live` |
| 🟡 | A deploy is in progress (the previous version keeps serving) |
| 🟠 | Up, but the latest deploy failed or was cancelled (the provider keeps serving the last live version) |
| 🔴 | No live version: the latest deploy failed or was cancelled |
| ⏸️ | Suspended |
| ⚪ | Deactivated, or no deploys yet |
| ❓ | That service's deploys couldn't be read |

Databases use the provider's own status (available, creating, in maintenance, unavailable…). A free database close to its expiry date
(within 7 days) shows 🟠 with the days left, and 🔴 once it has expired.

```
🟢 trip-trace-api (web)
Estado: en línea
Región: oregon
💾 Memoria: 91.4 MB (máx. 1 h: 91.4 MB)
⚙️ CPU: 0.14 % (máx. 1 h: 0.16 %)
Últimos deploys:
• live · hace 3 d — "Merge pull request #39 …" - 18/09 11:50
• deactivated · hace 11 d — "Merge pull request #23 …" - 10/09 12:57
Panel: https://dashboard.…
```

Memory and CPU come from the provider's metrics API (`/metrics/memory`, `/metrics/cpu`) for the last hour at 1-minute resolution: the value shown is the
latest data point and `máx. 1 h` the highest one in that window. Several instances are added up (`· 3 instancias`). CPU is a percentage of **one**
core (100 % = a whole core). If the last data point is older than 5 minutes it says how old it is. Static sites, cron jobs and suspended services have no
metrics and skip them.

A service that is `live` but has **no metrics at all in the last hour** shows `💤 Sin métricas en la última hora (probablemente dormido o sin
instancias)`: on the free plan that is what a service that spun down from inactivity looks like, so this is how you can tell it is asleep.
Deploy times are converted to the API machine's time zone.

**Limits worth knowing**
- It reads the provider's own state, not your app. A free-plan web service that is *asleep* from inactivity still shows 🟢 (the deploy is live); the detail view flags it with 💤 when it has no
  metrics in the last hour. The list does not query metrics, so it can't tell.
- The code only performs `GET` requests to the provider's API (a test enforces it), so it cannot suspend, delete or redeploy anything.
- As far as I know these API keys are not limited to read-only. Treat it like a password: keep it only in `.env` / `.env.prod`
  (git-ignored), and revoke it from the dashboard if it ever leaks.

## Local services (`service -L`, admin only)

The flag `-L` (or `-l`, `--local`) switches `!service` from the read-only hosted status to **this PC's own services**.
With no other flag it just shows the status; `-up` starts the service and `-down` stops it. `-L` on its own only prints the usage: services are
deliberately not listed, you have to know the name (e.g. `pg`, `trip-trace-telegram-bot`):

```
!service -L                                    → just shows how to use it (it never lists the services)
!service -L trip-trace-telegram-bot            → 🟢 trip-trace-telegram-bot: activo      (no flag = status)
!service -L trip-trace-telegram-bot -down      → ✅ trip-trace-telegram-bot: apagado
!service -L trip-trace-telegram-bot -up        → ✅ trip-trace-telegram-bot: levantado
```

Exactly one service name and at most one of `-up` / `-down` (`--up` / `--down` also work, in any order and case). Anything else, including
the old words (`iniciar`, `detener`, `estado`, `start`, `stop`...), is rejected with the usage line and runs nothing. Phone keyboards that turn
`--`/`-` into a long dash are handled.

**Detailed status.** With a service that defines the optional `info` and `last_usage` lookups (below), the status also says when it started or
stopped and what it uses:

```
🟢 trip-trace-telegram-bot: activo
⏱️ Activo desde 19:14:09 (hace 15 min 51 s)
💾 Memoria: 29.5 MB (pico 30.5 MB)
⚙️ CPU: 0.4 % ahora · 0.1 % promedio · 1.3 s en total
🔁 PID 20739 · reinicios automáticos: 0

🔴 trip-trace-telegram-bot: detenido
🕒 Detenido desde 19:10:08 (hace 19 min 52 s)
📊 Última ejecución: duró 26 s · CPU 1.8 s · memoria pico 34.8 MB
```

`CPU % ahora` is measured by reading the CPU counter twice, one second apart, so the status of a running service takes about a second;
100 % is one full core. Times are the PC's local time (a date is added when it isn't today). A stopped service shows a red 🔴.
What each figure comes from: `systemctl show` (state, times, memory and CPU while running, CPU of the last run) and the journal, where
systemd writes `Consumed 1.8s CPU time, 34.8M memory peak` when a service stops (the memory of the last run and, for units systemd forgets once stopped, when it stopped: systemd forgets them once
stopped, and that line is sometimes written without the memory part, in which case it is simply not shown).

The flag `-L` exists so the risky mode is explicit: without it the command can only *read* the hosted state.

**Which services** are defined in `services.json` (git-ignored; copy `services.example.json`). Each entry says what to run for each action:

```json
{
  "trip-trace-telegram-bot": {
    "description": "Bot de Telegram de TripTrace (local)",
    "start":  ["systemctl", "--user", "start",  "trip-trace-telegram-bot.service"],
    "stop":   ["systemctl", "--user", "stop",   "trip-trace-telegram-bot.service"],
    "status": ["systemctl", "--user", "is-active", "trip-trace-telegram-bot.service"],
    "info": ["systemctl", "--user", "show", "trip-trace-telegram-bot.service", "-p",
             "ActiveState,ActiveEnterTimestamp,ActiveExitTimestamp,InactiveEnterTimestamp,MainPID,MemoryCurrent,MemoryPeak,CPUUsageNSec,NRestarts,Result,ExecMainStatus"],
    "last_usage": ["journalctl", "--user", "-u", "trip-trace-telegram-bot.service", "-g", "Consumed", "-n", "1", "-o", "short-iso", "--no-pager"]
  }
}
```

`info` and `last_usage` are optional, read-only lookups used only to build the detailed status (they are validated like the other commands, and
nothing typed in a chat can run them on its own). Without them the status is just the state line. See `services.example.json`.

It is read on every call, so edits apply without restarting the API. For `status`, `systemctl is-active` words (`active`, `inactive`, `failed`…) are shown
in Spanish; any other command is judged by its exit code (0 = up).

**Safety, by design** (`app/services/service_control.py`):
- Only entries in `services.json` can run. The chat picks a *name* and one of three fixed actions, never a program or an argument.
- Commands run as an argv list, `shell=False`: `; rm -rf`, `$(...)`, `&&`... are never interpreted. Extra words are rejected, not ignored.
- Admins only (`ADMIN_USER_IDS`); the command is hidden from `!ayuda` for everybody else and nothing runs for a non-admin.
- Each command has a timeout (`SERVICE_COMMAND_TIMEOUT_SECONDS`, default 8 s, below the bots' 10 s) and is killed when it expires.
- Every start/stop is logged with who ran it (`LOCAL SERVICE by whatsapp:…: name stop -> exit 0`).
- Whoever can call this API as an admin can start/stop what is listed in `services.json`: keep the API on `127.0.0.1` and the `API_KEY` secret.

Note that replies are posted in the chat, so in a group everybody sees them.

### The machine itself: `service -L host` (or `master`)

`!service -L host` (or `!service -L master`, same thing, any case) is a built-in, read-only report of the PC the API runs on. It can't be started or
stopped, admins only, and the two names are reserved (a `services.json` entry can't take them).

```
🟢 host: todo en orden                      (or 🟠 host: N para revisar)
🖥️ Linux Mint 22.3 · kernel 7.0.0-31-generic
⏱️ Encendida desde 14:31:53 (hace 5 h 27 min)
⚙️ CPU: 40.4 % ahora · carga 1.44 / 1.22 / 1.08
🧩 Núcleos: 2 núcleos (Intel Celeron N3060) · uso 45 % / 36 % · frecuencia 1.5 / 2.5 GHz (máx. 2.5)
🌡️ Temperatura: 48 °C
💾 Memoria: 3.0 GB de 3.7 GB usados (82 %) · disponible 705.6 MB · swap 1.2 GB de 3.3 GB
💽 Disco /: 13.9 GB de 28.0 GB usados (50 %) · libres 12.7 GB
🔋 Batería: 75 % (cargando) · 🔌 enchufada
🔋 salud 100 % (37.7 Wh)
⏳ Autonomía: ≈ 5 h 40 min al ritmo de las últimas 1 h (12 %/h)         ← discharging; while charging: time to full
🔋 Historial (6 h): mín. 41 % (14:10) · máx. 100 % (03:20) · 2 h con batería · pico de descarga 22 %/h (15:05)
🧠 Más memoria: Isolated Web Co 29 % · firefox-bin 11 % · claude 9 %
🟠 Servicios con fallos: 1 (casper-md5check.service)
📈 Picos (6 h de datos): CPU 92 % (15:20) · memoria 88 % usada (16:05) · 71 °C (15:22)
```

It reads `/proc`, `/sys` and a few fixed commands (`loginctl`, `ps`, `systemctl --failed`); nothing typed in a chat reaches a command line, and program
names are shown without their arguments (those can hold secrets). What needs a look (🟠 and the count in the headline): less than 10 % memory available,
a disk over 85 %, temperature over 85 °C, load over 1.5 per core, a discharging battery under 20 % or worn below 70 %, failed systemd units, a pending
reboot, or a **remote login session** (the ordinary local session is not shown). Failed units you know are harmless can be ignored with
`HOST_IGNORED_UNITS` (comma-separated, e.g. `casper-md5check.service`).

**Peaks and battery trends** come from samples the API records itself: while it runs, a background thread stores CPU, memory, temperature and battery
every `HOST_SAMPLE_INTERVAL_SECONDS` (60; `0` turns it off) in the table `host_samples`, and keeps `HOST_HISTORY_DAYS` (7) days. Reports look at the last 24 h.
Nothing is recorded while the API is off, and holes over 10 minutes are not counted as "time on battery". The autonomy / time to full is a linear estimate
from the current run (needs 10+ minutes and a 2 % change). "Plugged in but not rising" is shown as information (some notebooks cap the charge).
CPU peaks are 1-minute averages. `Núcleos` shows the model, physical cores (and threads when there is hyper-threading), usage per core, current vs. max
frequency, and how many times the CPU slowed down from heat since boot (only if it happened).

Two things this machine taught me: its hardware clock is in **local time** (typical of dual boot with Windows), so `last`, `who` and even `loginctl`
record login times hours off; the report derives boot time from `/proc/uptime` instead. And UPower's battery history is too irregular (skewed times, missing
data, an invented power draw) to compute peaks from, which is why the API keeps its own samples.

**Installing a program as a user service** (what `trip-trace-telegram-bot` uses): put a unit in `~/.config/systemd/user/`, then
`systemctl --user daemon-reload && systemctl --user enable --now <name>.service`, and `loginctl enable-linger $USER` so it also starts
at boot without logging in. Logs: `journalctl --user -u <name> -f`.

## Football: `futbol` / `fut` and `tabla`

```
!futbol uruguay  → this week's fixtures for Uruguay's top league
!fut espana      → "fut" is the same command as "futbol"; "espana"/"spain" both work
!tabla argentina → the current standings table, sent as an image
```

Covers exactly 4 countries: Uruguay, Ecuador, Argentina and España (any spelling: accents/case don't matter,
and "spain" is accepted too). Anything else prints the 4 valid names, no request made.

```
⚽ Fixtures — Uruguay
• Peñarol 1-2 Nacional (finalizado)
• Danubio vs Defensor Sporting — sáb 26/09 18:00
```

Fixtures are chronological, covering a rolling window around today (fixture rounds don't line up with
calendar weeks, so this isn't a strict Mon-Sun cut).

`!tabla` sends the standings as an **image** (`app/services/table_image.py`, Pillow — no new dependency),
not text: a title, a header row and one row per team, each real zone/group (confirmed for Argentina's
"Zona A"/"Zona B" this season) as its own labelled block. If rendering fails for any reason, it falls
back to a plain-text table instead of breaking the command. The image itself isn't cached separately —
the standings data already is, and drawing it from that data takes well under a second even for the
largest table (Argentina's 30 rows), so another cache layer wouldn't be worth the complexity.

A league can also split its year into several stages (confirmed real for Uruguay: Apertura → Intermedio
→ Clausura → playoffs, each with its own table). `!tabla` asks ESPN which stage is current instead of
assuming one, and falls back to the regular-season stage if the current one doesn't have a table yet
(happens right when a new stage starts).

**No API key needed.** It uses ESPN's own public JSON API (the same one espn.com's site calls), which is
undocumented but needs no credentials and does have the current season. **API-Football was tried first**
(see `app/services/football_client.py`'s docstring) but its free plan turned out to only allow seasons
2022-2024 — useless for "this week"'s matches — so it was dropped entirely.

**Caching.** Same idea as `!github`'s (`app/services/github_client.py`): a `cachetools.TTLCache`, keyed
by (country, fixtures/table), only successful results cached. `FOOTBALL_CACHE_TTL_SECONDS` (default 24 h)
is much longer than GitHub's hardcoded hour, since there's no per-call cost to worry about here (ESPN's
endpoint publishes no quota) — it's purely about not hammering an undocumented API and keeping the
command fast; lower it in `.env` for fresher scores during a matchday. It's in-memory only, like every
other cache in this app: an API restart clears it.

Being unofficial, ESPN's API could change or disappear without notice — if `!futbol` starts failing, that
module is the one to look at first.

## GitHub PRs and issues (`github` or `gh`, admin only)

```
!github <repo>            → open PRs of that repo (same as -r)
!github <repo> -r         → open PRs                     (-r = the list of PRs)
!github <repo> -i         → open issues
!gh <repo> -i             → !gh is the same command as !github
```

`<repo>` is the repo name or **part of it** (`owner/name` also works); a word that matches several of your repos lists all the matches. A repo is
**required**: `!github` alone (or with only a flag) just prints a short usage helper, because listing every project at once is too much. The helper
never contains repo names. Also accepted: `-pr`/`-prs`/`--pr` for PRs and `-issue`/`-issues`/`--issue` for issues. At most one repo and one kind of flag;
anything else prints the helper and fetches nothing.

```
🔀 8 PRs abiertos en trip-trace-android-app

📦 trip-trace-android-app (8)
• #26 Add Room + trip-tracking foreground service
  🟢 open · 12 d · 6 commits            ← 📝 draft when it's a draft; "hoy" / "N d" since it was opened
  👤 joaquin-p-olivera · 🎯 sin asignar   ← author · assignee(s)
  🌿 feature/room-local-db → develop      ← the PR's branch → the branch it will merge into
  🔗 https://github.com/TechVibe-Dev/trip-trace-android-app/pull/26
```

**Order.** PRs go from the most to the fewest days open, and when several repos match, the repos are ordered by their oldest PR too. A PR with no
date goes last. Issues are not ranked (GitHub's own order). Issues show only `#number title` and the assignee (`— 🎯 bob`) when there is one.
Up to 10 PRs per repo (15 issues) are shown with `… y N más`; a `+` after a count means GitHub returned a full page of 30, so there may be more.
A repo that fails to load never counts as "nothing open": it gets its own ⚠️ line and the others are still shown.

**Setup.** `GITHUB_REPOS` in `.env`: comma-separated `owner/name` or GitHub URLs (`https://github.com/owner/name`). What a chat types only
*selects* among these; it can never build a URL. Public repos work as they are, but anonymous GitHub allows only 60 requests/hour (one full listing uses
about 12-20), so give it credentials. **Private repos need them**, by either of two ways:

1. **`GITHUB_TOKEN` (recommended, least privilege).** In GitHub: *Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate*. Under *Repository access* pick *Only select repositories* and choose the private ones; under *Permissions* set **Pull requests: Read-only**
   and **Issues: Read-only** (Metadata is added automatically). Paste it in `.env` as `GITHUB_TOKEN=...`, never in a chat. Fine-grained tokens on
   *organization* repos may need the organization's approval.
2. **`GITHUB_TOKEN_FROM_GH=true`.** Log in once with the GitHub CLI (`gh auth login`, in a terminal: it uses your browser) and the API borrows that
   session's token (`gh auth token`, cached 5 minutes) when `GITHUB_TOKEN` is empty. Convenient, but the CLI's token normally has the broad `repo`
   scope (read *and* write on your private repos). The code only ever sends `GET` requests to `api.github.com` (a test enforces it), but the token
   is that powerful, so prefer option 1 if you can.

The token is never shown in any reply. The API needs a restart to read a changed `.env`.

## Proactive alerts

The API can message a chat on its own, with no command behind it. Today the only source is a background health
check on local services (the same ones `!service -L` looks at):

```
[ALERTA] 🔴 trip-trace-telegram-bot: caído, revisar (falló).
```

```
[ALERTA] 🟢 trip-trace-telegram-bot: se recuperó (activo).
```

Every `ALERT_CHECK_INTERVAL_SECONDS`, the API checks the services listed in `ALERT_SERVICES` (comma-separated
names from `services.json`) exactly like `!service -L <name>` would, and remembers the last state of each. A
message is only sent on a **change**: going down sends one alert, staying down sends nothing more, and
recovering sends exactly one more. A service already down the very first time the API starts stays silent
(nothing was ever told to be down), but from then on every change is reported. When the service defines
`info` (and optionally `last_usage`) in `services.json`, the alert also includes the same extra lines
`!service -L <name>` shows (since when up/down, memory, CPU, restarts) — fetched only for the one alert
being sent, not on every check.

Alerts are queued in the database, one row per destination chat, and each bot picks its own up by polling
`GET /api/v1/alerts/pending?platform=...` (fetch-and-delete, so nothing is ever sent twice). Bots are still
thin forwarders: the alert logic (what to check, when it counts as a change) lives entirely in the API, the
same as every command.

**Setup.** In `.env`:
- `ALERT_SERVICES`: comma-separated names from `services.json`. Empty (default) = the whole feature is off.
- `ALERT_CHECK_INTERVAL_SECONDS` (default 300, i.e. 5 min).
- `ALERT_WHATSAPP_GROUP_JIDS` / `ALERT_TELEGRAM_CHAT_IDS`: comma-separated destinations, same ids `!whoami`
  or `ALLOWED_GROUP_JIDS`/`ALLOWED_CHAT_IDS` use. Both are independent: set either, both, or neither per
  platform. Each bot also has its own `ALERTS_POLL_INTERVAL_SECONDS` / `ALERTS_POLL_INTERVAL_MS` (default
  ~30 s) controlling how quickly it notices a queued alert — unrelated to how often the API itself checks.

## Adding a command

Create a handler in `app/commands/` and decorate it — it appears in `!help` automatically:

```python
from .registry import CommandContext, command

@command("hola", description="Saluda", category="Diversión")
def hello(ctx: CommandContext) -> str:
    return f"¡Hola, {ctx.message.display_name}!"
```

Return a `str`, a `Reply` (needed to mention users), a list of them (one chat message each), or `None`. New modules must be imported in
`app/commands/__init__.py`. Content (jokes, trivia questions) lives in `app/data/*.json`.

## Notes

- Tables are created with `create_all` on startup; there are no migrations yet. Add Alembic before the
  first schema change on a database you care about.
- Everything the chat sees is in Spanish. Reply texts live in the command modules and `app/data/*.json`.
- `user_id` is opaque and namespaced by platform: the same person on WhatsApp and Telegram is two players.
  On WhatsApp, groups may address members by LID or by phone number; the bot forwards whichever the
  message carries.

## Branching model

- `main`: released code only.
- `develop`: integration branch; `feature/{name}`, `doc/{name}`, ... branch off it.
- `release/{version}`: cut from `develop`, merged into `main` via PR.

See [CHANGELOG.md](./CHANGELOG.md) for release history.
