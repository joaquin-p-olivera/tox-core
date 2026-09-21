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
{ "replies": [{ "text": "🎲 3 + 5 = 8 (2d6)", "mentions": [] }] }
```

`participants` is optional: the full member list when the platform can provide it (WhatsApp). Its `user_id`s must
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
| `ping`                                 | `p`                        | Replies `pong`                                            |
| `ayuda [comando]`                      | `help`, `comandos`         | Lists commands or explains one                            |
| `id`                                   |                            | Shows the platform/chat/user IDs the bot sees             |
| `chiste`                               |                            | Random joke                                               |
| `dado [NdM \| M]`                      | `dados`                    | Dice, default `1d6` (e.g. `2d6`, `d20`, `20`)             |
| `moneda`                               | `cara`                     | Coin flip                                                 |
| `elegir a \| b \| c`                   |                            | Picks one option (separate with `\|` or `,`)              |
| `m`                                    |                            | `y tu mamá donde está? @someone` (random member, never the sender) |
| `trivia [categoría]`                   | `preguntas`                | Starts a multiplayer trivia question                      |
| `responder <A-D>`                      | `a`, `b`, `c`, `d`         | Answers the open question (`!b` is a shortcut)            |
| `ranking`                              | `top`, `puntajes`          | Per-chat trivia leaderboard                               |
| `uuid`                                 |                            | Random UUID v4                                            |
| `base64 codificar\|decodificar <texto>` | `b64`                      | Base64 helper                                             |
| `http <código>`                        |                            | Explains an HTTP status code                              |

### `!m` and who counts as a member

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
