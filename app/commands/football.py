"""!futbol <país>: this week's fixtures. !tabla <país>: the standings table, as an image.

Public commands (no admin gate): pure sports info with no privacy or security surface, unlike
!github's access to (possibly private) repos or !service's control over the host. Uses ESPN's public
API, which needs no key (see football_client.py for why: API-Football's free plan doesn't cover the
current season).
"""

import logging

from ..schemas import Reply
from ..services import football_client as fc, media_cache, table_image
from .registry import CommandContext, command

logger = logging.getLogger("tox.football")

CATEGORY = "Diversión"
MAX_FIXTURES_SHOWN = 15
MAX_STANDINGS_SHOWN = 15
COUNTRY_NAMES = "Uruguay, Ecuador, Argentina, España"


def _resolve(ctx: CommandContext, usage: str) -> "fc.Country | str":
    """The country to use, or the error/usage text to return as-is."""
    if len(ctx.args) != 1:
        return usage
    country = fc.resolve_country(ctx.args[0])
    if country is None:
        return f"No conozco ese país. Los que tengo son: {COUNTRY_NAMES}."
    return country


@command(
    "futbol",
    description="Partidos de la semana de la liga top de Uruguay, Ecuador, Argentina o España",
    usage="futbol <país>",
    aliases=("fut",),
    category=CATEGORY,
)
def futbol(ctx: CommandContext) -> str:
    usage = f"Uso: {ctx.prefix}futbol <país> — países: {COUNTRY_NAMES}"
    country = _resolve(ctx, usage)
    if isinstance(country, str):
        return country

    try:
        with fc.make_client(ctx.settings.FOOTBALL_TIMEOUT_SECONDS) as client:
            fixtures = fc.fetch_fixtures(client, country, ctx.settings.FOOTBALL_CACHE_TTL_SECONDS)
    except fc.FootballError as error:
        return f"⚠️ {error}"

    logger.info("FUTBOL by %s: %s", ctx.message.user_key, country.label)
    return _fixtures_report(country, fixtures)


@command(
    "tabla",
    description="Tabla de posiciones (como imagen) de la liga top de Uruguay, Ecuador, Argentina o España",
    usage="tabla <país>",
    category=CATEGORY,
)
def tabla(ctx: CommandContext) -> str | Reply:
    usage = f"Uso: {ctx.prefix}tabla <país> — países: {COUNTRY_NAMES}"
    country = _resolve(ctx, usage)
    if isinstance(country, str):
        return country

    try:
        with fc.make_client(ctx.settings.FOOTBALL_TIMEOUT_SECONDS) as client:
            standings = fc.fetch_standings(client, country, ctx.settings.FOOTBALL_CACHE_TTL_SECONDS)
    except fc.FootballError as error:
        return f"⚠️ {error}"

    logger.info("TABLA by %s: %s", ctx.message.user_key, country.label)
    return _table_reply(country, standings, ctx.settings.MEDIA_CACHE_SECONDS)


def _fixture_line(fixture) -> str:
    if fixture.completed:
        return f"• {fixture.home} {fixture.home_score}-{fixture.away_score} {fixture.away} (finalizado)"
    if fixture.live:
        return f"• 🔴 EN VIVO {fixture.home} {fixture.home_score}-{fixture.away_score} {fixture.away}"
    return f"• {fixture.home} vs {fixture.away} — {fc.fmt_kickoff(fixture.when)}"


def _fixtures_report(country: "fc.Country", fixtures: list) -> str:
    if not fixtures:
        return f"⚽ No hay partidos esta semana para {country.label}."
    lines = [f"⚽ Fixtures — {country.label}"]
    lines.extend(_fixture_line(fixture) for fixture in fixtures[:MAX_FIXTURES_SHOWN])
    if len(fixtures) > MAX_FIXTURES_SHOWN:
        lines.append(f"… y {len(fixtures) - MAX_FIXTURES_SHOWN} más")
    return "\n".join(lines)


def _standing_line(row) -> str:
    return f"{row.rank:2}. {row.team:<20} {row.points} pts  ({row.won}-{row.drawn}-{row.lost})"


def _table_report(country: "fc.Country", standings: list) -> str:
    """Plain-text table: the fallback when there's nothing to show, or the image couldn't be rendered."""
    if not standings:
        return f"📊 No tengo la tabla de {country.label} en este momento."
    lines = [f"📊 Tabla — {country.label}"]
    groups = dict.fromkeys(row.group for row in standings)  # preserves first-seen order, no duplicates
    for group in groups:
        all_rows = [row for row in standings if row.group == group]
        if group:
            lines.append(f"— {group} —")
        lines.extend(_standing_line(row) for row in all_rows[:MAX_STANDINGS_SHOWN])
        if len(all_rows) > MAX_STANDINGS_SHOWN:
            lines.append(f"… y {len(all_rows) - MAX_STANDINGS_SHOWN} más")
    return "\n".join(lines)


def _table_reply(country: "fc.Country", standings: list, media_cache_seconds: int) -> str | Reply:
    """An image of the table when possible; plain text if there's nothing to show or rendering fails
    for any reason (never a broken command over a cosmetic issue). Generating it isn't cached
    separately: the underlying data already is (FOOTBALL_CACHE_TTL_SECONDS), and drawing the image
    from it takes a few hundred ms even for the largest table — not worth another cache layer for."""
    if not standings:
        return _table_report(country, standings)
    try:
        png = table_image.render_table(country.label, standings)
    except Exception:
        logger.exception("Could not render the table image for %s", country.label)
        return _table_report(country, standings)
    image_id = media_cache.store(png, "image/png", media_cache_seconds)
    return Reply(image=image_id)
