"""Read-only client for the GitHub REST API, used by the admin-only !github command.

Only GET requests to api.github.com. The repositories come from the configuration (GITHUB_REPOS), never from a chat:
what people type only selects among them, so nothing typed can change which URL is requested.
"""

import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("tox.github")

BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
PER_PAGE = 30
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$")
_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+/[^/\s#?]+?)(?:\.git)?/?(?:[#?].*)?$", re.IGNORECASE)


class GitHubError(Exception):
    """The message is safe to show in the chat (it never contains the token)."""


@dataclass(frozen=True)
class Repo:
    owner: str
    name: str

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str
    author: str | None
    assignees: list[str]
    draft: bool
    head: str
    base: str
    created_at: datetime | None
    commits: int | None = None


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    assignees: list[str]


@dataclass(frozen=True)
class RepoResult:
    repo: Repo
    items: list = field(default_factory=list)  # PullRequest or Issue, oldest first
    error: str | None = None
    more: bool = False  # GitHub had a full page: there may be more open items than these


def parse_repos(text: str) -> tuple[list[Repo], list[str]]:
    """"owner/name" or GitHub URLs, comma or newline separated. Returns (repos, entries that couldn't be understood)."""
    repos: list[Repo] = []
    problems: list[str] = []
    for raw in re.split(r"[,\n]", text):
        entry = raw.strip()
        if not entry:
            continue
        match = _URL.match(entry)
        candidate = match.group(1) if match else entry
        if not _REPO.match(candidate):
            problems.append(entry)
            continue
        repo = Repo(*candidate.split("/", 1))
        if repo not in repos:
            repos.append(repo)
    return repos, problems


_TOKEN_TTL_SECONDS = 300
_token_cache: dict = {"value": None, "at": 0.0}
_token_lock = threading.Lock()


def reset_token_cache() -> None:
    with _token_lock:
        _token_cache.update(value=None, at=0.0)


def resolve_token(configured: str, from_gh: bool) -> str:
    """GITHUB_TOKEN if set; otherwise, when allowed, the GitHub CLI's own session (`gh auth token`, a fixed command).
    A token is cached for a few minutes so a burst of commands doesn't spawn the CLI every time; a failure is not cached."""
    if configured:
        return configured
    if not from_gh:
        return ""
    with _token_lock:
        if _token_cache["value"] is not None and time.monotonic() - _token_cache["at"] < _TOKEN_TTL_SECONDS:
            return _token_cache["value"]
        try:
            done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5,
                                  stdin=subprocess.DEVNULL, shell=False, check=False)
            token = done.stdout.strip() if done.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            token = ""
        if token:  # remember only a real token: "not logged in yet" must not delay noticing the login
            _token_cache.update(value=token, at=time.monotonic())
        return token


def make_client(token: str, timeout: float) -> httpx.Client:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION, "User-Agent": "tox-api"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=BASE_URL, headers=headers, timeout=timeout)


def _get(client: httpx.Client, path: str, params: dict | None = None):
    has_token = "authorization" in {k.lower() for k in client.headers}
    try:
        response = client.get(path, params=params)
    except httpx.TimeoutException:
        raise GitHubError("GitHub no respondió a tiempo.") from None
    except httpx.HTTPError:
        raise GitHubError("No pude conectar con GitHub.") from None
    status = response.status_code
    if status == 401:
        raise GitHubError("GitHub rechazó el token (¿venció? Volvé a iniciar sesión con `gh auth login` o revisá GITHUB_TOKEN).")
    if status in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        hint = "" if has_token else " Sin sesión son 60 por hora: iniciá sesión con `gh auth login` o configurá GITHUB_TOKEN."
        raise GitHubError("Se agotó el límite de consultas de GitHub." + hint)
    if status == 404:
        raise GitHubError("no lo encuentro o el token no tiene acceso." if has_token else
                          "no lo encuentro (¿es privado? hace falta iniciar sesión: `gh auth login` o un GITHUB_TOKEN con acceso).")
    if status == 403:
        raise GitHubError("GitHub no me dejó ver ese repo (permisos del token).")
    if status >= 400:
        raise GitHubError(f"GitHub respondió {status}.")
    try:
        return response.json()
    except ValueError:
        raise GitHubError("GitHub devolvió una respuesta inesperada.") from None


def _parse_time(text: str | None) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _logins(item: dict) -> list[str]:
    assignees = [a.get("login") for a in item.get("assignees") or [] if isinstance(a, dict) and a.get("login")]
    if not assignees and isinstance(item.get("assignee"), dict) and item["assignee"].get("login"):
        assignees = [item["assignee"]["login"]]
    return assignees


def _list(client: httpx.Client, repo: Repo, kind: str) -> list[dict]:
    params = {"state": "open", "per_page": PER_PAGE}
    if kind == "pulls":  # oldest first: with a long list, the ones that have been waiting longest are the ones we get.
        params.update(sort="created", direction="asc")  # (issues aren't ranked: GitHub's own order is fine)
    data = _get(client, f"/repos/{repo.owner}/{repo.name}/{kind}", params)
    if not isinstance(data, list):
        raise GitHubError("GitHub devolvió una respuesta inesperada.")
    return [item for item in data if isinstance(item, dict)]


def fetch_pull_requests(client: httpx.Client, repo: Repo, detail_limit: int = 10) -> RepoResult:
    """Open PRs, oldest first. The commit count only exists on the single-PR endpoint, so it is fetched for the first `detail_limit`."""
    try:
        raw = _list(client, repo, "pulls")
    except GitHubError as error:
        return RepoResult(repo, error=str(error))
    prs = []
    for item in raw:
        try:
            prs.append(PullRequest(
                number=int(item["number"]), title=str(item.get("title") or ""), url=str(item.get("html_url") or ""),
                author=(item.get("user") or {}).get("login"), assignees=_logins(item), draft=bool(item.get("draft")),
                head=(item.get("head") or {}).get("ref") or "?", base=(item.get("base") or {}).get("ref") or "?",
                created_at=_parse_time(item.get("created_at")),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    def with_commits(pr: PullRequest) -> PullRequest:
        try:
            detail = _get(client, f"/repos/{repo.owner}/{repo.name}/pulls/{pr.number}")
            commits = detail.get("commits") if isinstance(detail, dict) else None
        except GitHubError as error:
            logger.warning("Could not read PR %s#%d: %s", repo.full, pr.number, error)
            return pr
        return PullRequest(**{**pr.__dict__, "commits": commits if isinstance(commits, int) else None})

    head, tail = prs[:detail_limit], prs[detail_limit:]
    with ThreadPoolExecutor(max_workers=5) as pool:
        detailed = list(pool.map(with_commits, head))
    return RepoResult(repo, detailed + tail, more=len(raw) >= PER_PAGE)


def fetch_issues(client: httpx.Client, repo: Repo) -> RepoResult:
    """Open issues, in GitHub's own order (its issues endpoint also returns PRs: those are dropped)."""
    try:
        raw = _list(client, repo, "issues")
    except GitHubError as error:
        return RepoResult(repo, error=str(error))
    issues = []
    for item in raw:
        if "pull_request" in item:
            continue
        try:
            issues.append(Issue(int(item["number"]), str(item.get("title") or ""), _logins(item)))
        except (KeyError, TypeError, ValueError):
            continue
    return RepoResult(repo, issues, more=len(raw) >= PER_PAGE)


def fetch_all(client: httpx.Client, repos: list[Repo], kind: str) -> list[RepoResult]:
    """Every repo in parallel; one failing repo doesn't affect the others."""
    fetch = fetch_pull_requests if kind == "pulls" else fetch_issues
    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(lambda repo: fetch(client, repo), repos))
