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
./start_test.sh        # creates .venv, installs deps, runs uvicorn with --reload on 127.0.0.1:8000
```

**System dependency:** `!voz` needs `ffmpeg` on `PATH` (not a pip package — install it separately, e.g. `apt install
ffmpeg` or a static build). Without it, `!voz` fails with a clear error; every other command works fine.

Interactive docs: <http://127.0.0.1:8000/docs>. Run the tests with:

```bash
.venv/bin/python -m pytest
```

### Environments

| Script            | `APP_ENV` | Env file     | Reloads on save |
| ----------------- | --------- | ------------ | --------------- |
| `./start_test.sh` | `dev`     | `.env`       | yes             |
| `./start_dev.sh`  | `dev`     | `.env`       | no              |
| `./start_prod.sh` | `prod`    | `.env.prod`  | no              |

`./start_test.sh` is the one to use while writing code; `./start_dev.sh` is the same thing without the
reloader, which is what the service runs. uvicorn only watches `*.py`, so a change in `.env` needs a
restart either way.

`.env` and `.env.prod` are git-ignored; only `.env.default` (the template) is tracked.
Real environment variables always win over the files.

### Logs

Everything (the app's lines and uvicorn's access log) goes to the console and to a file in `LOG_DIR`
(`logs/`, git-ignored): `logs/tox.log` is the current one and a new one starts every `LOG_ROTATE_HOURS`
(24 by default, rolled over at midnight), leaving the day behind as `logs/tox-2026-10-20.log`.

Nothing is ever deleted automatically, so clear out the old files by hand once in a while. Set `LOG_DIR=`
(empty) to log to the console only.

### Running it as a service

`systemd` user units (so they need no `sudo` and start at boot thanks to `loginctl enable-linger`) live
outside this repo, in `~/.config/systemd/user/`: `tox-core.service` runs `./start_dev.sh` from this folder.
The bot has its own two, `tox-bot.service` and `tox-bot-personal.service`.

```bash
systemctl --user daemon-reload
systemctl --user enable --now tox-core.service   # starts it now and at every boot
systemctl --user status tox-core.service
journalctl --user -u tox-core.service -f         # uvicorn's own output (the app's log is in logs/)
```

Only start one instance of each: the API would fail to bind the port, and two bots on the same WhatsApp
session kick each other out (`stream_error_replaced` in the log).

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

**Personal mode.** A message with `"personal": true` comes from the owner's own number (the WhatsApp bot's personal
mode, see its README) in a private chat with someone else. There the commands start with `.b ` (`.b sticker hola`;
`!` and `/` are not commands, and the replies and help show the `.b` form), the sender is never treated as an admin
(the reply is visible to the other person), and `recordar` is refused: the reminder would be delivered by the groups
bot, from its own number, into that chat.

## Commands

Everything the chat sees is in Spanish (command names included). Code, docs and logs are in English.

| Command                                | Aliases                    | Description                                               |
| -------------------------------------- | -------------------------- | --------------------------------------------------------- |
| `ayuda [comando]`                      | `help`, `comandos`         | Lists commands or explains one                            |
| `memory`                               |                            | What Tox remembers about you in this chat (`olvidame` forgets it all) |
| `novedades`                            | `cambios`                  | What was last added to Tox (`-probar`, admins: what is about to be announced) |
| `id`                                   |                            | Shows the platform/chat/user IDs and your role            |
| `mute`                                 |                            | Toggle: stop (or resume) being tagged by the bot in this chat |
| `github <repo> [-r \| -i]`             | `gh`                       | **Admin only.** Open PRs (`-r`, default) or issues (`-i`) of one of your configured GitHub repos |
| `service [nombre]`                     |                            | **Admin only.** Whether your hosted services and databases are up; with a name, the detail of one |
| `service -L [nombre] [-up \| -down]`    |                            | **Admin only.** Query (default), start (`-up`) or stop (`-down`) the *local* services of this PC (see below) |
| `ipmaster`                             | `ipm`                      | **Admin only.** This PC's IP on the local network and the other devices connected to it (see below) |
| `fred`                                 |                            | **Admin only.** State and URLs of the Fred frontend (`:5200`), admin module (`:5201`), studio (`:5203`), one URL per message (see below) |
| `chiste`                               |                            | Random joke (general, dark and spicy; no tech jokes)      |
| `dado [NdM \| M]`                      | `dados`                    | Dice, default `1d6` (e.g. `2d6`, `d20`, `20`)             |
| `elegir a \| b \| c`                   |                            | Picks one option (separate with `\|` or `,`)              |
| `m`                                    |                            | Randomly: `y tu mamá donde está? @someone` (never the sender) or a random audio from `media/m` |
| `gay @usuario`                         |                            | Says how "gay" the tagged user is: a random 0-100 % (fixed for some people, see `users.json`); tagging the bot gets a cheeky answer |
| `risa`                                 |                            | Sends a random laugh audio from `media/risa`               |
| `futbol <país>`                        | `fut`                      | This week's fixtures for Uruguay/Ecuador/Argentina/España's top league |
| `tabla <país>`                         |                            | The current standings table, as an image                  |
| `vi`                                   | `gta6`                     | A card with how long is left until Grand Theft Auto VI, over a random picture of the game (see below); the same one the countdown sends on its own |
| `trivia [categoría]`                   | `preguntas`                | Starts a multiplayer trivia question                      |
| `responder <A-D>`                      | `a`, `b`, `c`, `d`         | Answers the open question (a plain `b`, no prefix, works) |
| `banderas`                             | `bandera`                  | Flags round of the trivia: which country is this flag? |
| `ranking`                              | `top`, `puntajes`          | Per-chat trivia leaderboard                               |
| `uuid`                                 |                            | Random UUID v4                                            |
| `base64 codificar\|decodificar <texto>` | `b64`                      | Base64 helper                                             |
| `url codificar\|decodificar <texto>`   |                            | URL-encode / decode                                       |
| `hash <md5\|sha1\|sha256\|sha512> <texto>` |                        | Hash of a text                                            |
| `sticker <texto>`                      |                            | Generates a meme-style sticker with the given text, or from a picture (sent with `!sticker` as caption, or replied to) |
| `foto`                                 | `imagen`                   | Turns a sticker into a photo (reply to the sticker with `!foto`) |
| `yt <link>`                            |                            | Sends the video of a YouTube link (up to 8 minutes; also works replying to a message with the link) |
| `ytm <link>`                           |                            | The same, as audio only                                   |
| `qr <texto o link>`                    |                            | A QR code image of the text or link                       |
| `recordar <HH:MM> <qué>`               |                            | Reminds you (tagging you, in the same chat) at that time: today if still ahead, else tomorrow (see below) |
| `claude [-n] [-t] <pregunta>`          |                            | Asks Claude: admins get full access in `~/dev`, everybody else a chat with no access to the PC; a ⏳ reaction first, then the answer (see below). Replying to a message of the bot asks it too, with no command |
| `config [clave valor \| -off \| -on]`    | `conf`                     | **Admin only.** How much Tox talks **in this chat**: lists it, changes one setting here only (`-reset` goes back to the general value) and `-off` / `-on` stop or resume it butting in (see below) |
| `spy [alias [palabras]]`               | `espiar`                   | **Admin only.** Which other chats this one may be told about, and with what words (`-alias` names this chat, `-off` takes one out, `-list` shows the names); never the other way round (see below) |
| `ban @usuario`                         |                            | **Admin only.** Removes the tagged member from the group (see below) |
| `permaban @usuario`                    |                            | **Admin only.** Bans and blacklists: removed from every group the bot is in, and again if they come back |
| `unpermaban [número o nombre]`         | `despermaban`              | **Admin only.** Lists the blacklist, or takes someone off it |
| `ruleta-ban`                           | `ruletaban`                | **Admin only.** Picks a random non-admin member to ban; an admin confirms with `!si` or cancels with `!no` |
| `censura @usuario`                     |                            | **Admin only.** Deletes everything the tagged member sends in this group; the same command again lifts it (see below) |
| `cumple <dd/mm/aaaa \| dd/mm>`         |                            | Registers your birthday (the year is optional); admins can tag someone to set theirs (overwrites) |
| `wol [número o nombre]`                |                            | **Admin only.** Wakes a device with Wake-on-LAN; with no argument, lists the ones in `devices.json` |
| `speedtest`                            |                            | **Admin only.** Internet speed of this PC: download, upload and latency (~15 s) |
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

`!sticker` also turns a **picture** into a sticker: send the image with `!sticker` as its caption, or reply to an image
with `!sticker`. The picture is scaled to fit a 512×512 transparent square (never cropped or stretched). For that, the
WhatsApp bot downloads the picture of any command message that carries or quotes one (up to 5 MB) and forwards it
base64-encoded in the message's optional `image` field; only `!sticker` and `!foto` read it.

**`!foto`** (alias `!imagen`) goes the other way: reply to a **sticker** with `!foto` and it comes back as a PNG
picture (`sticker_generator.sticker_to_image`), at the sticker's own size, with the transparent areas turned white.
Animated stickers keep only their first frame. A sticker can't carry a caption, so replying is the only way; the bot
downloads a quoted sticker the same way as a quoted picture. The result goes out through the `"image"` field.

**`!qr <texto o link>`** replies with a QR code PNG (`app/services/qr_code.py`, `qrcode` + Pillow; up to 500
characters), through the same `"image"` field and `GET /api/v1/images/{id}` as `!tabla`.

**`!voz [-h | -m] <texto>`** (up to 500 characters) says the text out loud using Microsoft Edge's free, unofficial
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

### Release notes (`app/data/news.json`, `!novedades`)

When a feature ships, the chats are told about it on their own. A note is written by hand, in Spanish, for the
group to read: the CHANGELOG is in English, technical and long, and nobody in a chat wants to read that one commit
at a time. The entry ships in the same commit as the feature it announces, so what is said and what was built
cannot drift apart.

```json
{"id": "2026-10-27-youtube", "title": "YouTube",
 "versions": {"whatsapp-bot": "1.0.0", "api-core": "1.0.0"},
 "text": "🎬 *!yt <link>* manda el video..."}
```

- Nothing is scheduled: each poll the bots make for pending alerts queues whatever the file gained
  (`news.queue_due`), so a deploy and a restart are all it takes. It goes to the same chats the alerts and the
  countdown go to (`ALERT_WHATSAPP_GROUP_JIDS`, `ALERT_TELEGRAM_CHAT_IDS`).
- What is remembered is the id of the newest entry already queued (`news_states`), not when it went out: a restart
  announces what is new and never the same thing twice. **The first run on a machine announces nothing** — with no
  row there is no telling an entry that was missed from the whole history of the file — so it writes down where the
  file stands and what is added after that is the first thing announced. (A marker that is not in the file any
  more means "announce from the start", which is how a machine that has announced nothing is told to catch up.)
- At most `MAX_AT_ONCE` (3) go out together, so several deploys without a poll in between do not become a wall of
  text, and an entry missing its id or its text is skipped and logged instead of being sent half-written.
- `!novedades` reads the latest one on demand; `!novedades -probar` (admins) shows exactly what is about to be
  announced and to how many chats, before a group reads it.
- The versions are written in the entry, not read off the apps: the API cannot see the bot's `package.json` (it is
  another repo), and a number copied into a setting drifts from the release it was meant to name. Only the app that
  changed needs a line.

### YouTube (`!yt`, `!ytm`)

`!yt <link>` sends the video of a YouTube link into the chat and `!ytm <link>` sends only the audio. The link can
also be in the message being replied to, which is how it usually arrives: somebody pastes it, somebody else asks
for it. Both answer with a ⏳ and send the file when it is there (the job system `!claude` uses).

- **Nothing is kept on this machine.** yt-dlp writes into a temp folder inside `YT_WORK_DIR` (`/dev/shm`, which is
  RAM, so the disk is never touched), the bytes are read out of it and the folder is deleted before the reply is
  built; from there they live in `media_cache` only until the bot has downloaded them. A video has to be written
  somewhere first because YouTube no longer serves a single stream with picture and sound in it: yt-dlp downloads
  the two and ffmpeg puts them in one mp4, which it cannot do into a pipe.
- **Two limits, both checked before anything is downloaded.** `YT_MAX_DURATION_SECONDS` (480, eight minutes), and
  `YT_MAX_BYTES` (16 MB, which is what WhatsApp takes) over the formats on offer. A video inside the duration can
  still be far too big — eight minutes at 360p is about 24 MB — so the quality is stepped down (720p, 480p, 360p,
  240p) until picture plus sound fit, and what does not fit even at 240p is refused with a message. Only H.264
  video and AAC audio are picked: they go into an mp4 as they are, where a VP9 or AV1 stream would have to be
  re-encoded, which this machine cannot afford.
- One download at a time: these go out over a home connection, and three people pasting links at once must not
  take it. Lives, playlists, private, age-restricted and members-only videos are refused, each with its own
  message.
- `yt-dlp` is in `requirements.txt` rather than the system's one on purpose: YouTube breaks old versions every few
  months, and that way dependabot keeps it current. ffmpeg has to be on the service's `PATH` (or `FFMPEG_PATH`).

### Trivia game

One open question per chat; everybody in the chat competes.

- `!trivia` posts a question with options A–D (144 written questions). Optional category: `geografia`, `ciencia`,
  `historia`, `cultura`, `deportes`, `uruguay`, `juegos`, `banderas` (accents and case don't matter).
- **`!banderas`** asks which country a flag belongs to. These questions are not written down: they are made out of
  `flags.json` (a country, its continent), and the flag is not stored either — a flag emoji *is* its ISO code
  written with the two regional indicator letters, so there is no picture to ship or lose. The three wrong answers
  come from the same continent whenever there are enough of them, which is what makes it hard; they are drawn with
  the round id as the seed, so a round always shows the same question with nothing but that id stored.
- Each player gets **one attempt** per question. A plain `a`, `b`, `c` or `d` with **no prefix** answers while a
  question is open — typed on its own or as a reply to the bot's question — and `!a`…`!d` still work. Outside a
  round a letter is just a letter, and the personal mode never reads one as an answer.
- The first correct answer wins 10 points plus up to 5 speed bonus points.
- Unanswered questions expire after `TRIVIA_TIMEOUT_SECONDS` (30). The reveal is pushed to the chat on the bots'
  next poll for pending alerts (`trivia_service.queue_due`, like reminders), so nobody has to type for it.
- **The round that ends starts the next one**, whether somebody won or the time ran out — as long as at least one
  person answered. A question nobody answered ends the streak, so a chat that moved on is left alone.
- **A streak keeps the category it was asked for** (`trivia_chains`): `!trivia ciencia` or `!banderas` is asking
  the *chat* to play that, so the rounds that follow on their own stay in it until the streak ends. A plain
  `!trivia` asks for anything again.
- Answer positions are shuffled per round; recently asked questions aren't repeated.
- Claiming a round is an atomic `UPDATE`, so two simultaneous correct answers can't both win.

## Admins

`ADMIN_USER_IDS` in `.env` lists the admins as comma-separated `platform:user_id` (the IDs are platform-specific, so
the platform prefix matters):

```
ADMIN_USER_IDS=whatsapp:111111111111111@lid,telegram:123456789
```

`OWNER_USER_IDS` is the exception to everything below: whoever is listed there is an admin **in every chat**, including a group
somebody else runs and where WhatsApp does not make them an administrator. The bot is theirs, so a group they did not create is
still their bot answering. Being there is enough; there is no need to be on `ADMIN_USER_IDS` too, and the personal mode is still
excluded (there the bot writes as the owner into somebody else's chat, so an admin answer is not theirs to see).

**For everybody else, being on that list isn't enough in a group:** on WhatsApp the bot also tells the API whether you administer *that* group
(`sender_is_group_admin`), and admin commands (`!service`, `!github`, `!uid`) need both. Someone on the list who isn't an admin
of the group where they write is treated as a normal user there. If the group's member list can't be read, nobody is
considered an admin of it for that message (never guessed). Private chats only look at the list, and so does a platform that
doesn't report it (Telegram, today). Nothing about group admins is stored anywhere: the bot reads the group's roster from
WhatsApp for every command sent in a group and the API uses it only for that one request, so a change of group admins applies
immediately.

Send `!id` in a chat: it shows your user ID and `Rol: admin` or `Rol: usuario` (saying so when you're on the list but not an
admin of that group). Mark a command as admin-only with
`@command(..., admin_only=True)`; everybody else gets no message at all: the bot just reacts to their command with 🚫. No admin-only
commands exist yet. Note that WhatsApp may identify the same person by LID in groups and by phone number elsewhere;
list the form you see in `!id` for the chats where you need it.

**`!uid`** (admin only, and hidden: not in `!ayuda`, not even for admins; anyone else just gets a 🚫 reaction) helps you build that list. Send it in a
group and the API prints one line per user in its log, as `platform:user_id — name | other ids` (the list the platform
gives the bot, plus you; on Telegram only the users the bot has already seen), ready to copy. WhatsApp often sends no name
for a group member, so a missing one is completed with the name the API learned when that person used a command; the
other ids include their phone number (`tel +598...`) when WhatsApp shares it. The chat gets no message: the bot just
reacts to your command with 👍. Look for the `UID` lines in the API's console output.

**`users.json`** (`USERS_FILE`, git-ignored — copy `users.example.json`) is the local directory you build from that output:
`"Name": ["platform:user_id", ...]`. Commands use it to give certain people special behaviour without relying on the display
name (anyone can change theirs). List every id of the same person (WhatsApp shows one person as a LID in some places and as
a phone number in others). It's read on every call, so edits apply without restarting. Today only `!gay` uses it: "Leandro 1899"
always gets 1899 % (`FIXED_GAY_PERCENT` in `app/commands/fun.py`).

**`chats.json`** (`CHATS_FILE`, git-ignored — copy `chats.example.json`) is the same idea for the chats themselves: one entry per
chat, keyed by `"platform:chat_id"` (the bare id works too), with `name`, `about` (what the chat is for), `people`
(`{"Name": "what to know about them"}`, or a list of several things about one person) and `notes`. Only `!claude` uses it, and it is read on every call,
so edits apply without restarting.

A person can also be written as `{"basics": "...", "about": "..."}`, which tells the elementary facts apart from the
rest:

```json
"people": {
  "Nombre": { "basics": "34 años, vive en Estados Unidos", "about": "Ingeniero, hincha de Peñarol, organiza las juntadas" }
}
```

`basics` goes to Claude **every time** it speaks (an age, a city: things it would be odd not to know about somebody it
talks to every day); `about` only `CLAUDE_PROFILE_PEOPLE_CHANCE` of the time when nobody typed a command, so it stops
making the same joke about somebody's life in every message. Written as a plain string or a list, everything counts as
`about`.

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

## The GTA VI countdown: `vi`

`!vi` answers with a **card**: a picture of the game over a bar with the days, hours, minutes and seconds left until
**19 November 2026, 00:00 Uruguay time**.

The picture is one of `GTA_ARTWORK_DIR` (`media/gta6`) at random, read from disk every time, so adding or removing one
needs no restart. `media/` is not tracked: the pictures are Rockstar's, handed out as wallpapers on their own site, so
they stay on this machine. With nothing readable there the card draws its own Vice City sunset instead, which is also
what happens if a file turns out not to be an image.

The card is **square (900x900) on purpose**: WhatsApp on iOS crops a preview taller than about 1:1, and an earlier
720x898 card lost its numbers off the bottom of the bubble (Android showed it whole). The artwork is therefore cropped
to a landscape rectangle, a little above its centre (`VERTICAL_BIAS`), which is where faces and skylines sit.

The text message is still there underneath (`🌴 Falta mm:dd:HH:MM:SS para Grand Theft Auto VI 🦩`) and is what the bot
sends if it cannot fetch the card: the message is never lost to a drawing. The months are calendar months, so they
shrink the way a person counts them rather than in 30-day blocks (until the release is less than a month away, which
it is, `mm` simply reads `00`).

Besides the command, the API sends it **on its own to the same chats the alerts go to**
(`ALERT_WHATSAPP_GROUP_JIDS` / `ALERT_TELEGRAM_CHAT_IDS`):

- **once a day at 00:00** (`TIMEZONE`), until 14 November;
- **every hour** from 15 November on, when waiting by the day stops being fun;
- and on 19 November, one last `🌴 Bienvenidos a VICE CITY. 🌴` over the official cover art, after which nothing is
  ever sent again and `!vi` answers that same card instead of a countdown. That day's picture is never a random one:
  it is always `GTA_COVER_FILE`, since the cover is the news.

`!vi` answers in the chat it was typed in, so it works in the **personal mode** too (`.b vi`), where the bot polls
for nothing and no alert ever lands. Anyone can use it.

Like reminders, nothing runs in the background: each bot poll of `GET /api/v1/alerts/pending` first queues whatever
is due. What the `countdown_states` table remembers is the **scheduled moment already sent**, not when it was sent,
which is what makes it both catch up and never repeat itself: a poll after a restart still sends the slot nobody
sent, ten polls inside the same hour send one message, and a whole day the API spent down is one missed message
instead of a burst of them. The first poll ever sends nothing — it only writes down where the schedule stands, so a
restart never fires a message by itself.

### The same countdown as an Instagram story

Set `INSTAGRAM_ENABLED` with the three things around it and every one of those moments is also published as a story
(`app/services/instagram.py`): the same picture as the card, laid out 1080x1920 over its own artwork blurred and
darkened.

The account has to be a professional **business** one — a creator account cannot publish stories — reached with a
token from the app dashboard (*Instagram > API setup with Instagram login*), which lasts **60 days**. Instagram never
receives the bytes: its publishing API downloads the picture from a public URL, so the card is parked on imgbb
(`IMGBB_API_KEY`) and the upload itself asks for it to be deleted again after `INSTAGRAM_IMAGE_TTL_SECONDS`. Then a
container is created (`media_type=STORIES`) and published — but not before its `status_code` reads `FINISHED`, since
publishing a container Instagram is still fetching is refused with *"Media ID is not available"*.

It all happens in a thread of its own, inside the bots' poll, and **nothing it does can fail the countdown**: a
broken token, a refused upload or a timeout is logged and the chats get their message all the same. With any of the
four settings missing, it is simply never attempted.

## Reminders: `recordar`

```
!recordar 18:30 sacar la basura        (the time may also come last: !recordar sacar la basura 18:30)
⏰ Listo, te lo recuerdo hoy a las 18:30: sacar la basura
```

Anyone can use it. The time is the wall clock of `TIMEZONE` (default `America/Montevideo`); if it already passed today, it
is for tomorrow (the reply says which). When it comes due, the bot writes in the **same chat**, tagging who asked:
`⏰ Recordatorio para @Ana: sacar la basura`. Up to 10 pending per person, 200 characters each.

Reminders live in the `reminders` table and survive restarts. Nothing runs in the background for them: each bot poll of
`GET /api/v1/alerts/pending` first moves the due ones into the alert queue, so a reminder goes out on the first poll after
its time — **the delay is at most the bot's `ALERTS_POLL_INTERVAL`** (set it to 30–60 s if a few minutes of lag is too
much; it is a call to localhost). One that came due while the API was down is delivered on the first poll after it starts
(with `(era para las HH:MM)` when it is more than 5 minutes late).

## Birthdays: `cumple`

`!cumple 15/03/1995` registers (or overwrites) the sender's birthday. An admin can tag someone: `!cumple @Ana 15/03/1995`;
anyone else tagging gets told only admins can. An admin who tags nobody sets their own. The date is `dd/mm/aaaa`, or just `dd/mm` when the year isn't wanted; it must be real and, with a year, already past. One date per person across all groups (table `birthdays`); `!cumple` alone shows yours.

## Claude: `claude`

`!claude <pregunta>` is open to everyone, but **what Claude can do depends on who asks**:

| Who | Mode |
|---|---|
| Admins (listed in `ADMIN_USER_IDS` *and* admin of the group) | Runs the Claude Code CLI (`claude -p`) in `CLAUDE_WORKDIR` (default `~/dev`) with **full access** (`--permission-mode bypassPermissions`: it can read and edit files and run commands), so it can answer about the projects and services or do what it is asked. Keeps a conversation per chat; `-n` starts a new one. |
| Everybody else | A plain chat with **no access to this PC**: every built-in tool disabled (`--tools ""`: no files, no shell, no web), no MCP servers, no skills, no user/project settings, run from an empty temporary folder, stateless (`--no-session-persistence`). Limited to `CLAUDE_PUBLIC_QUESTIONS_PER_PERSON_PER_HOUR` (100) per person, `CLAUDE_PUBLIC_QUESTIONS_PER_HOUR` (100) in total and `CLAUDE_PUBLIC_MAX_BUDGET_USD` (0.05, about $U 2; an answer costs ~US$0.01) per question. |

The full-access mode is acceptable only because of who can reach it. The only thing the no-access mode can still see is what the CLI
puts in the model's context (the account's e-mail), so its prompt forbids revealing it and the API masks any e-mail address in the answer.
The admins' conversation is never visible to the others (separate modes, the public one keeps nothing), and the two groups have separate
hourly pools so a busy group can't use up the admins' questions.

- **Context: Claude is one more member of the group.** Along with the question it receives (see `app/services/claude_context.py`):
  the **names of the members** (the curated `USERS_FILE` name first, then the roster's, then the name each person writes under; two with the same name get
  the last digits of their id), the **message the command replies to** (`quoted` on `POST /api/v1/messages`, with who wrote it), and, only for the no-access
  mode and only in chats where the owner turned the history on (`!h -on`), the **latest `CLAUDE_CONTEXT_MESSAGES` messages of the last `CLAUDE_CONTEXT_HOURS`
  hours** (150 / 12, never more than `CLAUDE_CONTEXT_MAX_CHARS` characters, the oldest go first; `0` messages = none). With the history off nothing saved is read.
  Everything is handed over as data, and the prompts say so. Claude can address and tag people: `@Name` in the answer becomes a real mention (only members
  in the chat now, never a muted one, at most 5).
- **One memory per chat, whoever asks.** While the history of a chat is on, every `!claude` exchange (what was typed and the answer that went to the chat,
  by an admin or not) is saved in it as a line of the conversation (`Tox` is the answer's author), so the no-access mode sees what was asked and answered
  before as part of those messages. It is a shared *conversation*, not a shared CLI session: the admins' full-access session (`claude -p --resume`, one per
  chat) is deliberately separate. Sharing it would hand a non-admin the tool output and files of the admins' turns, and would plant a non-admin's text in
  the context of an agent that can run commands. So the admins' mode reads nothing written by others except the message it replies to, and what the
  no-access mode reads is only what the chat has already seen.
- **`!c <question>`** is the short way of asking (`!c` alone is still the trivia answer C).
- **Replying to Tox needs no command** (`CLAUDE_ON_REPLY`, on by default; per chat with `!config`): a reply to one of the bot's own messages is a question
  for Claude, and the whole reply is the question. It keeps the conversation going the way it does with anybody else in the chat
  (replying to a voice note counts too, even though there is no text to quote). The same limits and modes apply; it never works
  in the personal mode, where the bot writes as the owner and a reply to it is their own chat. The WhatsApp bot only forwards the
  message when WhatsApp says the quoted one is the bot's: in a private chat it sometimes doesn't, and then the command is needed.
- **When it speaks on its own** (`services/discretion.py`): besides the dice and the cooldown, a table of
  objections, each one a small function over a snapshot of the chat, walked *before* Claude is called — so a remark
  that should not happen costs nothing, and no prompt can talk any of them out of it. It keeps quiet outside
  `CLAUDE_INTERJECT_START_HOUR`–`CLAUDE_INTERJECT_END_HOUR` (7 to 2, read in `TIMEZONE`, running past midnight
  because that is when a chat is most alive); once `CLAUDE_INTERJECT_PATIENCE` (2) of its remarks went unanswered
  in a row, until somebody talks to it again (a command, a reply to it, or its name); once it has written more than
  `CLAUDE_INTERJECT_SHARE_LIMIT` (2) times what the average person there has in the chat's recent window, which
  also keeps it out of a chat that has been asleep; while the group is talking about something heavy (a short word
  list — a death, an illness, an accident — blunt on purpose and wrong in the cheap direction: mistaking a joke for
  bad news costs one remark nobody would have missed); and while a trivia round is open, where the answers *are*
  the conversation. The objection that fired is what gets logged. Where it stands with a chat is remembered in this
  process only, like the cooldown it sits beside.
- **What it remembers about each person** (`services/person_memory.py`, `person_notes`, `!memory`): the small
  things that come up while talking -- what somebody does, what they are into, an inside joke -- kept without
  anybody writing them down. **No extra question is asked for it**: the answer Claude is already giving may end
  with `[memoria] Nombre: dato` lines, which are taken off before the chat sees a word of it and stored. The
  stripping is deliberately greedy (any line starting with the marker goes, parsed or not), because the one
  failure that matters is a marker reaching the chat.
  A note belongs to **one chat** — the chat is part of the key, so nothing crosses between groups and nothing said
  in the personal mode is kept; only the **no-access mode** learns, since the admins' session is doing work on a
  machine, not chatting; a name has to be **somebody who is there** or the note is dropped, so Claude cannot invent
  a person to remember things about; and **each person owns theirs** (`!memory`, `!memory forget`, no admin
  needed), which is the only reason a bot is allowed to keep notes about people at all. A chat turns it off with
  `!config memory off`; `CLAUDE_MEMORY_MAX_NOTES` (20) and `CLAUDE_MEMORY_MAX_CHARS` (120) bound it, and the notes
  that never came up again are the first to go. They are read back as what they are: data that came out of a
  conversation, in the untrusted block, never in the owner's own description of the chat.
- **`-no`: that reply was not for Tox.** Starting a reply to one of its messages with `-no` (or `--no`) answers nothing: in a group
  people reply to what the bot said to talk to *each other*, and until now every one of those got an answer nobody asked for. Nothing
  is sent, nothing is reacted to and **Claude is never called**, so it costs nothing and uses no question of the hour. It is read only
  where there is no command to put a flag on: a typed `!claude -no sé qué hacer` is the question it looks like. It goes with `-t`
  in any order. Tagging somebody else in the reply does not stop the answer on its own -- a question for Tox often tags people too.
- **It butts in now and then** (`CLAUDE_INTERJECT_CHANCE`, 0.02 of the messages; per chat with `!config`, see below): in a group whose history is on, Tox reads the
  conversation and drops **one remark nobody asked for**, always as text (never a voice note) and always in the no-access mode,
  whoever happened to write the message that got it talking — nobody asked for anything, so it must never run commands on this PC.
  It ends with a `> Tox` line, so a remark is never taken for an answer to somebody. Whether it is worth saying anything at all is
  Claude's own call: it is told to answer `paso` (and then nothing is sent, and nobody is told) when the recent messages are mostly
  its own, questions put to it or commands instead of a real conversation. It also stays quiet if it said something in that chat
  within `CLAUDE_INTERJECT_COOLDOWN_MINUTES` (30; answering a question counts as having spoken), if the hourly limits are full
  (a remark is charged to the bot itself, `tox-bot`, not to the person who was talking) or if the line of questions is full.
  Everything about it is silent: no reaction, no "I'm busy", no limit message. `0` turns it off, and then nothing is even rolled.
  This is what the WhatsApp bot forwards ordinary chatter for, and only from the groups whose history is on: a message that
  carries a picture is forwarded without downloading it.
- **What the chat is.** `CHATS_FILE` (`chats.json`, see above) gives the chat a name, what it is about, who its people are and any notes, and all of it goes
  to **both** modes. It is the one part of the context nobody but the owner can write, so the prompt presents it as the owner's own description of the chat
  instead of as untrusted data; everything else (members, transcript, quoted message) stays data.
- **Who everybody is, only now and then.** When nobody typed a command (a remark of its own, or an answer to a reply), what the profile says *about* each
  member (`about`) is handed over only `CLAUDE_PROFILE_PEOPLE_CHANCE` of the time (0.25). With everybody's job, city and team always at hand, every message
  ends up being the same joke about them; the rest of the time it has to talk about what is being said. Their names and the elementary facts (`basics`:
  an age, a city) always go, and so does the chat's own description. A typed `!claude` gets all of it: somebody asked, so the answer may well need it.
- **Sometimes a voice note.** `CLAUDE_AUDIO_CHANCE` (0.25) of the answers are sent as a voice note instead of text, read with
  `CLAUDE_AUDIO_VOICE` (`hombre`: the same voice as `!voz -h`). The dice is rolled **before** asking, so an answer that will be
  heard is asked for under 480 characters (20 under what the voice service takes), with no emoji, markdown or tags. It falls back to text when the answer tags somebody
  (the tag would be lost), is longer than the voice service takes, or the synthesis fails: the answer is never lost. Summaries
  (`res`) are always text.
- **`-t`: text, no dice.** `!claude -t <pregunta>` (or `!c -t ...`) answers as text for certain, without rolling the dice above.
  Anybody can use it, admins included, and it goes with `-n` in any order (`!c -n -t ...`). It is read when replying to a
  message of the bot as well, where there is no command to put a flag on, and in the personal mode (`.b c -t <pregunta>`).
  A question that merely starts with a dash is asked as typed: only `-t`, `--texto`, `-n` and `--nuevo` are flags.
- **Tone.** `CLAUDE_TONE` sets how the no-access mode talks: `salado` (the default: insults, swearing and heavy teasing, as the friends do among themselves),
  `picante` (sarcastic and chicanero, milder), `tranqui` (playful and joking, but it teases nobody: the jokes are about the situation or about itself, never about
  somebody's defects, failures or looks, and there is no swearing, black humour or sexual joking) or `normal` (just friendly, nothing added). **It is per chat**
  (`!config tono tranqui`, see below), which is what a group where not everybody is in on the joke wants. The two spicy prompts keep a few hard limits, written into them (`TONE_PROMPTS`): no
  insults for race, origin, religion, gender, orientation, disability or illness, nothing sexually explicit, no threats or pushing anybody to hurt themselves,
  no serious accusations or private data; and the jokes stop when somebody is really down. It applies to the admins' full-access mode too (only the manner changes: the answers about the PC stay correct, and the secrecy rules stay); the summaries are unchanged.
- **A reaction, then the answer.** The reply is a `⏳` reaction to the command (no placeholder message) with a `job` id; the bot polls `GET /api/v1/jobs/{id}` (every 2 s, up to 15 min)
  and sends the answer, quoting the command, when it is ready; the `⏳` is then taken off (an empty `reaction`, which is how the bot revokes one) or turned into `⚠️` if there is no answer. A question can take minutes; the chat request doesn't wait for it.
  Jobs live in memory: if the API restarts meanwhile, the bot says the query was lost.
- **Limits.** Few at a time, never "busy": the admins' questions go one by one, the no-access ones and the summaries `CLAUDE_PARALLEL` (2) at a time, and the rest wait their turn in a line of `CLAUDE_QUEUE_MAX` (5) places (only a full line is refused); questions per hour, counted in `claude_queries`: `CLAUDE_QUESTIONS_PER_HOUR` (200, admins together)
  and `CLAUDE_PUBLIC_QUESTIONS_PER_HOUR` (100, everybody else together); `CLAUDE_MAX_BUDGET_USD` (2.5, about $U 100) per question, passed as `--max-budget-usd`; `CLAUDE_TIMEOUT_SECONDS` (600),
  after which the whole process group is killed. A cold question costs roughly US$0.30 in tokens (the CLI loads its context first).
- **Short answers.** The system prompt asks for a summary of at most `CLAUDE_MAX_ANSWER_CHARS` (800) characters, and the API cuts
  the text to that length anyway (the model can ignore the prompt).
- **No secrets.** The system prompt forbids printing `.env` contents or sensitive data (only the *structure* of a `.env` is allowed), and
  the API scrubs the answer before sending it: the values of every secret-looking variable (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASS*`...) of
  the `.env*` files one level under the work directory, `webapps.json` passwords, provider tokens and WhatsApp ids become `***`. The
  prompt is the main control; this is a safety net, not a guarantee (the model could still rephrase a value).
- **Conversation (admins).** Each chat keeps its CLI session (in memory) and the next question resumes it; `!claude -n <pregunta>` starts a new one.
  The question is passed through stdin, and it is not stored or logged (only its length).

Note that the CLI runs in the same folder as an interactive Claude Code session would, so it also reads that folder's `CLAUDE.md`
and memory.

### One chat reading another: `chat_context_sources`

A chat can be told what was said in **another** chat (`app/services/chat_sources.py`), for the case where one group is
the continuation of another and the question only makes sense with both. `!spy` (admin only, alias `espiar`) is where it
is set up, always **from the chat that is going to read**:

```
!spy                          qué chats puede leer este, y cómo se llama
!spy -alias festejen          le pone nombre a ESTE chat (una palabra), para poder nombrarlo desde otro
!spy -list                    los nombres que hay
!spy festejen risas,asado     este chat pasa a leer "festejen" cuando la pregunta diga alguna de esas palabras
!spy -off festejen            deja de leerlo
```

Un alias no da permiso por sí mismo: solo evita escribir un id de WhatsApp. El permiso es la fila de
`chat_context_sources`, y se escribe desde el chat que lee.

- **A row is one direction.** `chat_key` is the chat that reads, `source_chat_key` the one that is read. A row saying A
  reads B does not let B read A: that needs its own row, and not writing it is how the other chat learns nothing.
- **It is read only when the question asks for it.** Each row carries `triggers` (comma separated, the `label` when
  empty), matched against the question without accents or case. A question that names none of them is answered as if
  the source did not exist, and **nothing of that chat reaches the prompt at all** -- which is the real control: an
  instruction not to bring something up is a control, not a guarantee, while text that was never sent cannot be
  repeated by accident. A remark Tox makes on its own (`interject`) has no question, so it never brings another chat up.
- **`label` is what the prompt calls it** ("el otro grupo"), so the name of a group is not something the other one has
  to learn. The block tells Claude to answer with it but not to copy messages verbatim, not to say who said what unless
  asked, and not to raise the subject in other answers. The people there are not in this chat and are never taggable.
- **A question that asks for a source is answered without access to this PC**, even when an admin asks it: the
  full-access mode never receives anything written by other people (that is why it gets no conversation at all), and a
  question about what was said somewhere is a question about a conversation, not about this machine. Without this an
  admin could never see a source in their own group, since being an admin is exactly what puts them in the other mode.
- What is read is only what the source chat's own history already saved (`!h -on` there), the latest
  `CLAUDE_SOURCE_MESSAGES` (50) of the last `CLAUDE_CONTEXT_HOURS`, up to `CLAUDE_SOURCE_MAX_CHARS` (6000) of their own,
  so the chat's own conversation loses no room. `CLAUDE_SOURCE_MESSAGES=0` turns the whole thing off.

```sql
INSERT INTO chat_context_sources (chat_key, source_chat_key, label, triggers, added_by, added_at)
VALUES ('whatsapp:<el que lee>', 'whatsapp:<el que es leído>', 'el otro grupo', 'el otro grupo,los pibes',
        'manual', datetime('now'));
```

### Per chat: `config` (admin only)

Everything above is global, read from the `.env` when the API starts. A group that writes all day and a quiet one don't want the
same numbers, so **`!config` gives one chat its own value** for how talkative Tox is there (`app/services/chat_config.py`,
`chat_settings`). What is typed in a chat changes that chat and no other.

| Typed | Setting | What it is |
|---|---|---|
| `tono` | `CLAUDE_TONE` | How it talks: salado, picante, tranqui or normal |
| `charla` | `CLAUDE_INTERJECT_CHANCE` | How often it butts in with nobody talking to it |
| `espera` | `CLAUDE_INTERJECT_COOLDOWN_MINUTES` | How long it stays quiet after speaking |
| `perfiles` | `CLAUDE_PROFILE_PEOPLE_CHANCE` | How often it is told who everybody is |
| `respuestas` | `CLAUDE_ON_REPLY` | Whether replying to it is a question |
| `audio` | `CLAUDE_AUDIO_CHANCE` | How often an answer is a voice note |
| `contexto` | `CLAUDE_CONTEXT_MESSAGES` | How many messages of the conversation it reads |
| `horas` | `CLAUDE_CONTEXT_HOURS` | How far back they are read from |

- `!config` lists them all with what each is worth here and whether that is **this chat's own value or the general one**;
  `!config charla` shows just one. `!config charla 15%` sets it, `!config charla -reset` drops the row and the `.env` value
  applies again. A chance is read and written as a **percentage** (`15%`, or `nunca`), a yes/no as `sí` / `no`.
- `!config -off` is the switch the owner asked for: `charla` to `0` **in this chat**, so Tox stops butting in there while it
  still answers whoever talks to it. `!config -on` undoes it (a `-reset` of that one key, not a `0.02` written by hand).
- **The list of settings it can touch is a whitelist** (`chat_config.OVERRIDABLE`), and only those eight. An admin typing into a
  group reaches this code, so a key, a path, a money cap or `ADMIN_USER_IDS` must never be reachable from a message — the service
  refuses anything outside the list even if a command asked it to.
- Every value is validated by **the field's own rules in `config.py`** (its type and its `ge`/`le`), so a chat cannot be given a
  number the `.env` itself would have been refused for, and the error message quotes the real limits instead of a hand-written
  copy. A stored value that stops being valid (a range narrowed in a new version) is ignored and the general value applies again,
  rather than taking the chat down.
- `admin_only`, and in a WhatsApp group `is_admin` also asks for the sender to administer **that** group: nobody configures a
  group they aren't an admin of. Rows survive a restart, so none of this needs the `.env` touched again — **the `.env` stays the
  default for every chat that was never configured, and there is deliberately no way to change that default from a chat.**

## Moderation: `ban`, `permaban`, `unpermaban`, `ruleta-ban`, `censura` (admin only)

The API decides, the WhatsApp bot executes. A reply can carry `remove` (user ids to take out of the chat where the command ran)
and `sweep_blacklist`; the bot needs to be **an admin of the group** to remove anyone (if it isn't, it says so instead of
announcing a ban that didn't happen).

- `!ban @usuario` (up to 5 people): removes them from this group.
- `!permaban @usuario`: the same, and adds them to the blacklist (`blacklisted_users`, with every id the roster knows them
  under: LID and phone number). The bot then removes them from **every group it is in** (it also does that when it connects,
  for anyone who joined while it was off) and, whenever someone is added to a group (`group` event, action `add`), it checks the
  blacklist again and removes them (announcing it in allowed groups). The bot reads the list from `GET /api/v1/blacklist`.
- `!unpermaban`: lists the blacklist; `!unpermaban 2` or `!unpermaban ana` takes them off (they can join again).
- `!ruleta-ban`: draws one regular member (never a group admin, a listed admin, the sender or the bot), tags them and waits for
  `!si` (ban) or `!no` (save them) from an admin. One draw per chat; it expires after 2 minutes.

Safety: nobody can ban the bot, themselves, a group admin or anyone in `ADMIN_USER_IDS`; a person whose group-admin status the
bot could not read is refused (fails closed). It all works only in groups.

### `censura`: deleting everything somebody sends

`!censura @usuario` is a switch, not an action: the same command again lifts it. While it is on, **every** message that person
sends in that group is deleted for everyone as it arrives -- their commands included, which are not run. It is per chat: the
same person writing in another group is untouched. It never works from the personal mode (it is `admin_only`, and that is
never granted there).

Who can be censored is a lower bar than who can be banned: **anybody except the bot itself and whoever runs the command**, a
group admin and a listed admin of `ADMIN_USER_IDS` included. Nobody leaves the group and the same command undoes it, so there
is nothing to fail closed about either -- unlike `!ban`, a member whose group-admin status the bot could not read is censored
all the same.

Unlike `!ban`, the API does not decide message by message. It only keeps the list (`censored_users`, with every id the roster
knows the person under) and serves it at `GET /api/v1/censored?platform=whatsapp`:

```json
[{"chat_id": "120363000000000000@g.us", "user_ids": ["1234@lid", "59899123456@s.whatsapp.net"]}]
```

The bot reads it (at most every 15 s, and again right after any command, so the switch takes effect at once) and deletes on
its own. It has to: a censored person's ordinary chatter only reaches the API when that chat has history on, and censorship
cannot depend on that. **The bot must be an admin of the group** -- WhatsApp only lets an admin delete somebody else's
message. When it can't, it says so in the group once and logs it with every message.

## Wake-on-LAN and speed test: `wol`, `speedtest` (admin only)

`!wol` with no argument lists the devices of `devices.json` (the same file `!ipmaster` takes names from, MAC → name):

```
!wol
💡 Dispositivos que puedo despertar (Wake-on-LAN):

1. Mac de Ana — 00:11:22:33:44:55
2. Desktop — 5a:11:22:33:44:66
```

`!wol 2` (or part of the name: `!wol desktop`) broadcasts a magic packet (UDP, port 9, to the subnet's broadcast address and
255.255.255.255). Only devices listed in the file can be targeted, never a MAC typed in the chat. There is no reply from the
device, so "sent" is all it can confirm: it only turns on if it supports Wake-on-LAN and has it enabled (BIOS/NIC; phones and
most TVs don't).

`!speedtest` measures this PC's connection against Cloudflare's public speed-test endpoints (no key, nothing installed):
latency, then download and upload for 6 s each, so it takes ~15 s and moves roughly 20–80 MB depending on the line. One test at
a time; a second request while one runs is told so.

## Local network: `ipmaster` / `ipm` (admin only)

```
!ipmaster
🌐 Red local 192.168.1.0/24 (wlo1)
🖥️ Esta PC: 192.168.1.21

📡 Otros dispositivos (5):
• 192.168.1.1 — 00:11:22:33:44:55 · Acme Networks · router
• 192.168.1.6 — Celular de Ana · 5a:11:22:33:44:66 · MAC privada (celular o PC con privacidad)
• 192.168.1.13 — PCDEANA · ea:11:22:33:44:88 · MAC privada (celular o PC con privacidad)
• 192.168.1.16 — Android / Smart TV(1234) · bc:11:22:33:44:99
```

It takes no arguments. This PC's address comes from `ip addr` (private addresses only; container/VM bridges and loopback are skipped). To
find the others, every host of that subnet gets **one ping** so the kernel learns who answers, and its neighbour table (`ip neigh`) is
read back: no extra program, no root, about 4 s. The manufacturer comes from the IEEE list the system already ships
(`/usr/share/ieee-data/oui.txt`, package `ieee-data`); a phone using a private (randomised) MAC has no manufacturer to look up, so it's
labelled as such. The router is the default gateway.

**Names.** A device only has one if it announces itself over mDNS/Bonjour (read with `avahi-browse`, in parallel with the pings, so it adds
no time; smart TVs, PCs, printers and Apple devices usually do, most phones and the router don't) or if you list its MAC in `devices.json`
(`DEVICES_FILE`, git-ignored; copy `devices.example.json`): `{"5a:11:22:33:44:66": "Celular de Ana"}`. Your name wins over the announced
one, and a phone's private MAC stays the same on the same Wi-Fi, so it keeps its name. The router's DNS and NetBIOS were tried and give
nothing. What a device announces can be any text, so it's cleaned (printable characters only, 40 max).

Only devices that answer show up: one in deep sleep or behind a firewall can be missing. The subnet is always the one of this PC's own interface (never something typed in a chat), a subnet wider
than a /24 is cut down to the /24 around this PC, and the programs run as fixed argv lists with no shell and a timeout.

### `fred`

`!fred` answers with the state of the Fred apps and the URLs to open them, built from this PC's own LAN address and the fixed ports
(`APPS` in `app/commands/fred.py`: frontend 5200, admin module 5201, studio 5203). It sends several messages: the first one has each app's
state, then there is **one message per app with nothing but its URL**, so each one can be tapped or copied on its own:

```
🟢 Frontend
🟡 Admin module (solo desde esta PC)
🟢 Studio (solo desde esta PC)

http://192.168.1.21:5200
http://localhost:5201
http://localhost:5203
```

(the last three lines are three separate messages). Each app's state is checked the way another device would reach it (a TCP connection
to the LAN IP): 🟢 answers there; 🟡 is up but only reachable from this PC (it listens on `127.0.0.1`: a Vite dev server needs `--host`, as
`fred-frontend.service` has, to be reachable from other devices) and its URL then uses `localhost`, the only address that works; 🔴 is down.
Studio is local-only **by design** (`fred-studio.service` binds to `127.0.0.1`), so it is 🟢 when it answers on this PC, always with the
`localhost` URL. WhatsApp only makes a URL tappable when the message carries it as link data; the bot attaches that for URLs on private
addresses (`src/links.ts` in tox-whatsapp-bot), which works here because each message holds a single URL (a `localhost` one is covered too). The Fred user services (`fred-frontend`, `fred-admin-module`, `fred-backend`, `fred-db`, `fred-studio`) are
in `services.json` (`!service -L <name> [-up | -down]`) and in `ALERT_SERVICES`. Admin only, like the other local-infrastructure commands.

## Remote browser: `web` / `w` (admin only)

The bot can't show a web app by itself, so `!web` opens it in an invisible Chromium **on this PC**, draws a number on every button, link and
field it can see, and sends a screenshot; you say what to tap or type by number. Only those screenshots leave the PC (through the chat): nothing is
exposed to the internet, which is the point (no tunnel, no VPN, no cost).

```
!web                    help and the list of apps
!web trip-trace         opens that app and sends the numbered screenshot
!web 3                  taps element 3
!web t 1 hola mundo     types "hola mundo" into field 1
!web login              fills the login again (opening an app with credentials already logs in by itself)
!web enter | esc | tab  keys          !web bajar | subir   scrolls
!web atras | ver | close             (ver = a fresh screenshot, close = closes the browser)
```

**Setup.** Copy `webapps.example.json` to `webapps.json` (`WEB_APPS_FILE`, git-ignored) and list the apps: `{"name": {"url": ..., "description": ...,
"login": {"user": ..., "password": ...}}}`. Playwright is in `requirements.txt` but Chromium has to be downloaded once (about 270 MB, into
`~/.cache/ms-playwright`, not the system): `.venv/bin/playwright install --only-shell chromium`.

**What keeps it safe.** The chat only picks an app *name* from that file, never a URL; the page can't navigate to another site (a top-level navigation
outside the app's own origin is refused and you're taken back); downloads, pop-ups, dialogs and service workers are refused. Passwords never go through
the chat: opening an app whose entry in `webapps.json` has credentials logs in by itself when the page shows a login form (`!web login` repeats
it), and what you type or the credentials are never logged. It's admin only, and in a group the
screenshots are visible to **everyone in that group**, so use a group that has only you and the bot.

**Cost on this PC** (HP Stream, 3.8 GB of RAM): the browser takes ~450 MB while open, so there is one session at a time, it's opened on demand
and closes itself after `WEB_IDLE_SECONDS` (180) without use. Before each screenshot it also waits `WEB_SCREENSHOT_DELAY_SECONDS` (3) so the page
can finish loading its data. The first `!web <app>` takes ~7-15 s (browser start plus a cold dev server), after
that each action takes a second or two. A login waits for the app's own API (a sleeping free-tier one can take ~30 s), so set the WhatsApp bot's `TOX_API_TIMEOUT_MS` to 100000 or the first
`!web <app>` would time out.

**Restarts.** The browser belongs to the API process (the bot only forwards messages), so restarting the *bot* changes nothing: the session
carries on. Restarting the *API* ends the session: a normal stop closes the browser (it's closed in the API's shutdown), and even a forced kill
(`kill -9`, a crash) leaves no browser behind, because Playwright's driver notices its parent died and closes it (checked). What is lost is the
page you were on: the next `!web <app>` opens it again (and logs in again), and `!web 3` says there's nothing open.

## Chat history and summaries: `h`, `res` (owner only)

`!h -on | -off | -status | -delete` (also `!historial`) turns the saving of a chat's text on or off, and
`!res [-today | -all | 6h | 2d | 150]` (also `!resumen`) has Claude summarise what was saved: today's messages by default
(since midnight in `TIMEZONE`), `-all` the whole history, or a span / a count; never more than the latest
`HISTORY_SUMMARY_MAX_MESSAGES`.

They are `owner_only` in the command registry: allowed from the owner's own number (the WhatsApp bot's personal mode,
`"personal": true`: `.b h -on`) and, through the groups bot, to admins (`ADMIN_USER_IDS`, and an admin of that group,
like every admin command: `!h -on`). Anyone else gets a 🚫 reaction and `!ayuda` doesn't list them. Turning it on or off
is answered with a ✅ reaction only: nothing is written in the chat. A chat is on for every bot that sees it, so a
group that has both the groups bot and the owner's number forwards each message twice; the API keeps it once.

- **What is saved:** the text of every message of a chat that is on (a picture's caption counts, the picture does not),
  with who wrote it, when, and WhatsApp's message id (kept once per chat, so two bots seeing the same chat don't
  duplicate it). The commands themselves (`.b ...`, `!...`) and the bots' own answers are not saved.
- **Where:** `HISTORY_DB_PATH`, a SQLite file of its own (git-ignored, created mode 600), not `tox.db`. The `history_chats`
  table of `tox.db` only says which chats are on.
- **Size limit:** past `HISTORY_MAX_MB` (1024) the oldest messages of every chat are deleted, down to 90% of it. It is
  checked when something is saved, at most once a minute, inside the API: no background thread or second process.
- **One chat, two ids:** a private chat can be named by phone number or by LID, so the bot sends both and the API keeps
  them as one conversation (`chat_aliases`).
- **API:** the bot polls `GET /api/v1/history/chats` (every chat id that is on) and sends the text of those chats with
  `POST /api/v1/history`, which ignores any chat that is not on.
- **Summaries leave this machine:** the text goes to Claude (the same CLI as `!claude`, no tools and no access to this
  PC, its own prompt and `CLAUDE_SUMMARY_MAX_BUDGET_USD` cap, in the same line as the questions). Nothing else is sent anywhere.

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
