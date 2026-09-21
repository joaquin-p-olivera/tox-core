# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial FastAPI scaffold: settings (`.env` / `.env.prod` selected by `APP_ENV`), SQLite database, API key authentication (`X-API-Key`), `/health`.
- `POST /api/v1/messages`: platform-agnostic entry point the bots forward chat messages to.
- Command framework with decorator-based registry, `!` and `/` prefixes, aliases and an auto-generated `!help`.
- Commands, all in Spanish: `ping`, `ayuda`, `id`, `chiste`, `dado`, `moneda`, `elegir`, `m`, `uuid`, `base64`, `http`.
- `m`: asks `y tu mamá donde está?` and mentions a random chat member other than the sender.
- Replies can carry `mentions` (`{@0}` placeholders) and messages can carry the chat's `participants`; users seen in a chat are remembered as a fallback member list.
- Multiplayer trivia game (`trivia`, `responder`, `ranking`) with 72 questions in 7 categories (general knowledge, Uruguay and games) and a per-chat leaderboard.
- Test suite (pytest) covering the router, every command, the trivia rules and API authentication.
