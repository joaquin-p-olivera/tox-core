import logging
from datetime import datetime, timezone

from ..services import github_client
from .flags import normalize_flag
from .registry import CommandContext, command

logger = logging.getLogger("tox.github")
CATEGORY = "Administración"
PR_FLAGS = {"-r", "--r", "-pr", "-prs", "--pr", "--prs"}
ISSUE_FLAGS = {"-i", "-issue", "-issues", "--issue", "--issues"}
MAX_PRS_SHOWN = 10
MAX_ISSUES_SHOWN = 15
TITLE_MAX = 100


def _title(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX - 1] + "…"


def _days(created: datetime | None, now: datetime) -> str:
    if created is None:
        return "?"
    days = (now - created).days
    return "hoy" if days <= 0 else f"{days} d"


def _names(results: list[github_client.RepoResult]) -> dict[github_client.Repo, str]:
    """Short repo names, or owner/name when two repos share the same name."""
    counts: dict[str, int] = {}
    for r in results:
        counts[r.repo.name] = counts.get(r.repo.name, 0) + 1
    return {r.repo: (r.repo.name if counts[r.repo.name] == 1 else r.repo.full) for r in results}


FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


def _opened(pr: github_client.PullRequest) -> datetime:
    return pr.created_at or FAR_FUTURE  # a PR without a date can't be ranked: it goes last


def _pr_lines(pr: github_client.PullRequest, now: datetime) -> list[str]:
    status = "📝 draft" if pr.draft else "🟢 open"
    commits = "" if pr.commits is None else f" · {pr.commits} commit" + ("" if pr.commits == 1 else "s")
    people = [f"👤 {pr.author or '?'}", f"🎯 {', '.join(pr.assignees)}" if pr.assignees else "🎯 sin asignar"]
    return [f"• #{pr.number} {_title(pr.title)}",
            f"  {status} · {_days(pr.created_at, now)}{commits}",
            f"  {' · '.join(people)}",
            f"  🌿 {pr.head} → {pr.base}",
            f"  🔗 {pr.url}"]


def _report(results: list[github_client.RepoResult], kind: str, now: datetime, single: bool) -> str:
    label, shown_max = ("PRs", MAX_PRS_SHOWN) if kind == "pulls" else ("issues", MAX_ISSUES_SHOWN)
    if kind == "pulls":  # most days open first: the PRs of each repo, and the repos by their oldest PR
        results = [github_client.RepoResult(r.repo, sorted(r.items, key=_opened), r.error, r.more) for r in results]
        results.sort(key=lambda r: _opened(r.items[0]) if r.items else FAR_FUTURE)
    names = _names(results)
    readable = [r for r in results if not r.error]   # a repo that failed to load must never count as "nothing open"
    with_items = [r for r in readable if r.items]
    empty = [names[r.repo] for r in readable if not r.items]
    total = sum(len(r.items) for r in with_items)

    if total:
        where = f"en {len(with_items)} de {len(results)} repos" if not single else f"en {names[results[0].repo]}"
        lines = [f"{'🔀' if kind == 'pulls' else '🐛'} {total} {label} abiertos {where}"]
    elif readable:
        lines = [f"✅ Sin {label} abiertos" + (f" en {names[readable[0].repo]}" if single else f" en {len(readable)} repos")]
    else:
        lines = []  # everything failed: only the warnings below
    for result in with_items:
        lines.append("")
        lines.append(f"📦 {names[result.repo]} ({len(result.items)}{'+' if result.more else ''})")
        for item in result.items[:shown_max]:
            if kind == "pulls":
                lines += _pr_lines(item, now)
            else:
                who = f" — 🎯 {', '.join(item.assignees)}" if item.assignees else ""
                lines.append(f"• #{item.number} {_title(item.title)}{who}")
        hidden = len(result.items) - shown_max
        if hidden > 0:
            lines.append(f"  … y {hidden}{'+' if result.more else ''} más")
    if total and empty and not single:
        lines += ["", f"✅ Sin {label} abiertos: {', '.join(empty)}"]
    errors = [f"⚠️ {names[r.repo]}: {r.error}" for r in results if r.error]
    if errors:
        lines += ([""] if lines else []) + errors
    return "\n".join(lines)


def _usage(prefix: str) -> str:
    return (f"Uso: {prefix}github <repo> [-r | -i]\n"
            "• sin flag o -r: PRs abiertos del repo (los más antiguos primero)\n"
            "• -i: issues abiertos del repo\n"
            "El repo puede ser una parte de su nombre.")


@command(
    "github",
    description="(Admin) PRs (-r, por defecto) o issues (-i) abiertos de un repo de tu lista",
    usage="github <repo> [-r | -i]",
    aliases=("gh",),
    category=CATEGORY,
    admin_only=True,
)
def github(ctx: CommandContext) -> str:
    usage = _usage(ctx.prefix)
    args = [normalize_flag(a) for a in ctx.args]
    flags = [a for a in args if a.startswith("-")]
    names = [a for a in args if not a.startswith("-")]
    wants_prs, wants_issues = any(f in PR_FLAGS for f in flags), any(f in ISSUE_FLAGS for f in flags)
    # A repo is required (listing every project at once is too much), at most one kind of flag,
    # and anything unknown is rejected instead of guessed. Nothing is fetched for any of these.
    if len(names) != 1 or any(f not in PR_FLAGS | ISSUE_FLAGS for f in flags) or (wants_prs and wants_issues):
        return usage
    kind = "issues" if wants_issues else "pulls"  # PRs when no flag is given

    settings = ctx.settings
    repos, problems = github_client.parse_repos(settings.GITHUB_REPOS)
    if not repos:
        note = "".join(f"\nNo entendí esta entrada: {p}" for p in problems)
        return "No hay repos configurados: falta GITHUB_REPOS en el .env de la API." + note

    query = names[0]
    exact = [r for r in repos if query in (r.name.lower(), r.full.lower())]
    repos = exact or [r for r in repos if query in r.name.lower()]
    if not repos:
        return f"No tengo un repo que coincida con \"{query}\"."
    single = len(repos) == 1

    token = github_client.resolve_token(settings.GITHUB_TOKEN, settings.GITHUB_TOKEN_FROM_GH)
    with github_client.make_client(token, settings.GITHUB_TIMEOUT_SECONDS) as client:
        results = github_client.fetch_all(client, repos, kind)
    logger.info("GITHUB by %s: %s of %d repos -> %d with errors", ctx.message.user_key, kind, len(repos),
                sum(1 for r in results if r.error))
    text = _report(results, kind, datetime.now(timezone.utc), single)
    if problems:
        text += "".join(f"\n⚠️ No entendí esta entrada de GITHUB_REPOS: {p}" for p in problems)
    return text
