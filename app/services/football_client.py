"""Read-only client for ESPN's public (undocumented, no API key) soccer JSON API, used by the
public !futbol command.

API-Football was the original plan, but its free plan turned out to only allow seasons 2022-2024
(confirmed with a real key: every current-season request is rejected with "Free plans do not have
access to this season"), which makes it useless for "this week's fixtures". ESPN's site API is what
espn.com itself calls, needs no key, and does have the current season for all 4 leagues below — the
trade-off is it's unofficial and undocumented, so it could change or disappear without notice; if it
ever does, this is the one module that needs to change.

Confirmed empirically (no official docs to link):
- Scoreboard: https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates=YYYYMMDD
  (one calendar day at a time; a date *range* like YYYYMMDD-YYYYMMDD returns 400 for soccer).
- Standings: https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings?seasontype=<id>
  (without seasontype, some leagues split into stages return no `children` at all). The id is NOT a
  fixed number: a league can have several stages across a year, each with its own id and its own
  table (confirmed real for Uruguay: 1=Apertura, 2=Intermedio, 4=Clausura, 5/6=playoffs — hardcoding
  1 silently showed the finished Apertura table instead of the actual current Clausura one). The
  scoreboard response's `leagues[0].season.type.id` says which stage ESPN considers current right
  now, so that's fetched once (cheap, no quota) and used instead of guessing.
"""

import logging
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
from cachetools import TTLCache

logger = logging.getLogger("tox.football")

SCOREBOARD_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
STANDINGS_BASE_URL = "https://site.api.espn.com/apis/v2/sports/soccer"
# How far back/ahead of today to look for fixtures: a round doesn't respect calendar-week boundaries
# (confirmed: one Argentine round spanned Sat-Tue), so this is a rolling window around "now" instead
# of a strict Mon-Sun week, wide enough to catch a round already underway or about to start.
FIXTURES_DAYS_BACK = 3
FIXTURES_DAYS_AHEAD = 6


class FootballError(Exception):
    """The message is safe to show in the chat."""


@dataclass(frozen=True)
class Country:
    label: str  # shown in replies, e.g. "España"
    slug: str  # ESPN's league slug, confirmed working: uru.1, ecu.1, arg.1, esp.1


# Confirmed with real requests (league names came back exactly as expected, current season 2026):
# uru.1 "Liga AUF Uruguaya", ecu.1 "LigaPro Ecuador", arg.1 "Argentine Liga Profesional de Fútbol",
# esp.1 "Spanish LALIGA". "spain"/"espana" both point at the same Country.
COUNTRIES: dict[str, Country] = {
    "uruguay": Country("Uruguay", "uru.1"),
    "ecuador": Country("Ecuador", "ecu.1"),
    "argentina": Country("Argentina", "arg.1"),
    "espana": Country("España", "esp.1"),
    "spain": Country("España", "esp.1"),
}


def _normalize(value: str) -> str:
    """"España" -> "espana": accents and case don't matter when typing (mirrors trivia_service.normalize_category)."""
    decomposed = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def resolve_country(text: str) -> Country | None:
    return COUNTRIES.get(_normalize(text))


@dataclass(frozen=True)
class Fixture:
    home: str
    away: str
    when: str  # ISO8601 kickoff time (UTC), exactly as ESPN returns it
    completed: bool
    live: bool
    home_score: int | None
    away_score: int | None


@dataclass(frozen=True)
class Standing:
    rank: int
    team: str
    points: int
    played: int
    won: int
    drawn: int
    lost: int
    group: str = ""  # e.g. "Group A" when a competition splits into zones (Argentina); "" otherwise


def make_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def _get(client: httpx.Client, url: str, params: dict) -> dict:
    try:
        response = client.get(url, params=params)
    except httpx.TimeoutException:
        raise FootballError("ESPN no respondió a tiempo.") from None
    except httpx.HTTPError:
        raise FootballError("No pude conectar con ESPN.") from None
    if response.status_code >= 400:
        raise FootballError(f"ESPN respondió {response.status_code}.")
    try:
        data = response.json()
    except ValueError:
        raise FootballError("ESPN devolvió una respuesta inesperada.") from None
    if not isinstance(data, dict):
        raise FootballError("ESPN devolvió una respuesta inesperada.")
    return data


def _team_name(competitor: dict) -> str:
    team = competitor.get("team") or {}
    return team.get("displayName") or team.get("name") or "?"


def _parse_fixture(event: dict) -> Fixture | None:
    try:
        competition = event["competitions"][0]
        competitors = competition["competitors"]
        home = next(c for c in competitors if c.get("homeAway") == "home")
        away = next(c for c in competitors if c.get("homeAway") == "away")
        status = competition["status"]["type"]
        completed = bool(status.get("completed"))
        live = status.get("state") == "in"

        def score(competitor: dict) -> int | None:
            if not (completed or live):
                return None
            try:
                return int(competitor["score"])
            except (KeyError, TypeError, ValueError):
                return None

        return Fixture(
            home=_team_name(home), away=_team_name(away), when=event["date"],
            completed=completed, live=live, home_score=score(home), away_score=score(away),
        )
    except (KeyError, StopIteration, TypeError):
        return None


def _fetch_fixtures_window(client: httpx.Client, country: Country, today: date) -> list[Fixture]:
    """One request per day in the window (ESPN's soccer scoreboard doesn't accept a date *range*),
    deduplicated by kickoff+teams in case a day is returned twice."""
    seen: set[tuple[str, str, str]] = set()
    fixtures: list[Fixture] = []
    for offset in range(-FIXTURES_DAYS_BACK, FIXTURES_DAYS_AHEAD + 1):
        day = (today + timedelta(days=offset)).strftime("%Y%m%d")
        data = _get(client, f"{SCOREBOARD_BASE_URL}/{country.slug}/scoreboard", {"dates": day})
        for event in data.get("events") or []:
            fixture = _parse_fixture(event)
            if fixture is None:
                continue
            dedup_key = (fixture.when, fixture.home, fixture.away)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            fixtures.append(fixture)
    fixtures.sort(key=lambda f: f.when)
    return fixtures


def _stat(entry: dict, name: str) -> int:
    for stat in entry.get("stats") or []:
        if stat.get("name") == name:
            try:
                return int(stat["value"])
            except (KeyError, TypeError, ValueError):
                return 0
    return 0


def _parse_standings(data: dict) -> list[Standing]:
    """Flattens every group (`children`) into one list, tagging each row with its group's name when
    there's more than one (confirmed real for Argentina: "Group A"/"Group B", two separate zones this
    season — not a bug). The rank is computed here (sorted by points, then goal difference) rather than
    trusted from ESPN's own "rank" stat: a real response had two different Ecuadorian teams both marked
    rank=1, so that field isn't reliable."""
    children = data.get("children") or []
    standings: list[Standing] = []
    for group in children:
        # ESPN's own group names come in English ("Group A"); the chat is Spanish. Only the
        # "Group <letter>" shape has actually been seen so far (Argentina's real zone split).
        name = (group.get("name") or "").replace("Group ", "Zona ") if len(children) > 1 else ""
        entries = ((group.get("standings") or {}).get("entries")) or []
        rows = []
        for entry in entries:
            team = entry.get("team") or {}
            rows.append((
                _stat(entry, "points"), _stat(entry, "pointDifferential"),
                team.get("displayName") or team.get("name") or "?",
                _stat(entry, "gamesPlayed"), _stat(entry, "wins"), _stat(entry, "ties"), _stat(entry, "losses"),
            ))
        rows.sort(key=lambda r: (-r[0], -r[1]))
        for rank, (points, _diff, team, played, won, drawn, lost) in enumerate(rows, start=1):
            standings.append(Standing(rank, team, points, played, won, drawn, lost, name))
    return standings


# Only 4 countries x 2 kinds = 8 possible keys, ever. The TTL comes from Settings
# (FOOTBALL_CACHE_TTL_SECONDS) so it can be tuned without a code change; unlike github_client's cache,
# this isn't about a request quota (ESPN's endpoint publishes none) but about not hammering an
# undocumented API and keeping the command fast. Only successful results are cached.
_DEFAULT_TTL_SECONDS = 86400
_cache: TTLCache = TTLCache(maxsize=16, ttl=_DEFAULT_TTL_SECONDS)
_cache_lock = threading.Lock()


def reset_cache() -> None:
    """For tests: drops every cached result."""
    with _cache_lock:
        _cache.clear()


def _configure_cache(ttl_seconds: int) -> None:
    global _cache
    with _cache_lock:
        if _cache.ttl != ttl_seconds:
            _cache = TTLCache(maxsize=16, ttl=ttl_seconds)


def _cache_key(country: Country, kind: str) -> tuple[str, str]:
    return (country.label, kind)


def fetch_fixtures(client: httpx.Client, country: Country, ttl_seconds: int, today: date | None = None) -> list[Fixture]:
    _configure_cache(ttl_seconds)
    key = _cache_key(country, "fixtures")
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    fixtures = _fetch_fixtures_window(client, country, today or date.today())
    with _cache_lock:
        _cache[key] = fixtures
    return fixtures


def _current_season_type(client: httpx.Client, country: Country) -> int:
    """Which stage ESPN considers current right now for this league (see the module docstring:
    hardcoding one number is wrong, a league can have several stages across a year). 1 (the first
    stage) is a reasonable fallback if this can't be read for some reason."""
    try:
        data = _get(client, f"{SCOREBOARD_BASE_URL}/{country.slug}/scoreboard", {})
        return int(data["leagues"][0]["season"]["type"]["id"])
    except (FootballError, KeyError, IndexError, TypeError, ValueError):
        return 1


def fetch_standings(client: httpx.Client, country: Country, ttl_seconds: int) -> list[Standing]:
    _configure_cache(ttl_seconds)
    key = _cache_key(country, "standings")
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    season_type = _current_season_type(client, country)
    data = _get(client, f"{STANDINGS_BASE_URL}/{country.slug}/standings", {"seasontype": season_type})
    standings = _parse_standings(data)
    if not standings and season_type != 1:
        # Confirmed real for Ecuador: the "current" stage (a knockout "Final Stage") can start before
        # ESPN has populated any table for it. Falling back to stage 1 (the regular season every
        # league has had so far) beats showing nothing.
        data = _get(client, f"{STANDINGS_BASE_URL}/{country.slug}/standings", {"seasontype": 1})
        standings = _parse_standings(data)
    with _cache_lock:
        _cache[key] = standings
    return standings


def fmt_kickoff(iso_when: str) -> str:
    """"2026-09-25T23:00:00Z" -> "jue 25/09 20:00" in this machine's own time zone."""
    try:
        moment = datetime.fromisoformat(iso_when.replace("Z", "+00:00"))
    except ValueError:
        return iso_when
    return f"{moment.astimezone():%a %d/%m %H:%M}"
