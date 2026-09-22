import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import github_client as gc

ADMIN = "admin1@lid"
REPOS = "https://github.com/acme/proj-api, acme/proj-web ,https://github.com/other/proj-api.git, acme/proj-empty"


def iso(days_ago=0, hours=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr(number, title="Add thing", author="alice", assignees=(), draft=False, head="feature/x", base="main", days=3, commits=2):
    return {"number": number, "title": title, "html_url": f"https://github.com/acme/proj-api/pull/{number}", "user": {"login": author},
            "assignees": [{"login": a} for a in assignees], "draft": draft, "head": {"ref": head}, "base": {"ref": base},
            "created_at": iso(days), "_commits": commits}


def issue(number, title="Bug", assignees=(), is_pr=False):
    item = {"number": number, "title": title, "assignees": [{"login": a} for a in assignees], "html_url": f"https://github.com/x/{number}"}
    if is_pr:
        item["pull_request"] = {"url": "..."}
    return item


@pytest.fixture
def hub(monkeypatch, settings):
    """A fake GitHub. hub.pulls / hub.issues: {"owner/name": [items]}; hub.fail: {"owner/name": status or exception}."""
    class Fake:
        pass

    fake = Fake()
    fake.pulls, fake.issues, fake.fail, fake.requests, fake.headers = {}, {}, {}, [], []
    object.__setattr__(settings, "GITHUB_REPOS", REPOS)
    object.__setattr__(settings, "GITHUB_TOKEN", "")
    object.__setattr__(settings, "GITHUB_TOKEN_FROM_GH", False)
    gc.reset_token_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        fake.requests.append(request)
        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/(pulls|issues)(?:/(\d+))?", request.url.path)
        if not match:
            return httpx.Response(404)
        repo, kind, number = match.groups()
        failure = fake.fail.get(repo)
        if isinstance(failure, Exception):
            raise failure
        if isinstance(failure, int):
            headers = {"x-ratelimit-remaining": "0"} if failure in (403, 429) else {}
            return httpx.Response(failure, json={"message": "nope"}, headers=headers)
        if kind == "pulls" and number:
            found = next((p for p in fake.pulls.get(repo, []) if isinstance(p, dict) and p.get("number") == int(number)), None)
            return httpx.Response(200, json={**found, "commits": found["_commits"]}) if found else httpx.Response(404)
        items = fake.pulls.get(repo, []) if kind == "pulls" else fake.issues.get(repo, [])
        return httpx.Response(200, json=[{k: v for k, v in i.items() if k != "_commits"} if isinstance(i, dict) else i for i in items])

    def make_client(token, timeout):
        fake.headers.append(token)
        return httpx.Client(base_url=gc.BASE_URL, transport=httpx.MockTransport(handler),
                            headers={"Authorization": f"Bearer {token}"} if token else {})

    monkeypatch.setattr(gc, "make_client", make_client)
    return fake


def ask(send, text="!github proj", user=ADMIN):
    return send(text, user=user)[0]


# ---- access -------------------------------------------------------------------------------------

def test_only_admins_and_a_non_admin_causes_no_request(send, hub):
    for text in ("!github", "!gh -issue", "!github proj-api -pr"):
        assert send(text, user="someone@lid") == ["Este comando es solo para administradores."]
    assert hub.requests == []


def test_it_is_hidden_from_help_for_non_admins(send):
    assert "!github" not in send("!ayuda", user="someone@lid")[0]
    assert "!github" in send("!ayuda", user=ADMIN)[0]
    assert send("!ayuda github", user="someone@lid") == ["No conozco ese comando. Probá !ayuda"]


def test_a_second_admin_can_be_added_in_the_settings(send, settings, hub):
    object.__setattr__(settings, "ADMIN_USER_IDS", "whatsapp:admin1@lid, whatsapp:91700000000001@lid")
    assert not ask(send, "!github", user="91700000000001@lid").startswith("Este comando")
    assert send("!github", user="99999@lid") == ["Este comando es solo para administradores."]


# ---- parsing the repo list ----------------------------------------------------------------------

def test_parse_repos_accepts_names_and_urls():
    repos, problems = gc.parse_repos("acme/proj-api, https://github.com/acme/proj-web/, http://www.github.com/other/x.git,\n  Acme/Api , acme/proj-api")
    assert [r.full for r in repos] == ["acme/proj-api", "acme/proj-web", "other/x", "Acme/Api"]   # duplicates (same case) dropped
    assert problems == []


@pytest.mark.parametrize("bad", ["noslash", "a/b/c", "https://gitlab.com/a/b", "../x/y", "a b/c", "/x", "x/"])
def test_parse_repos_reports_what_it_does_not_understand(bad):
    assert gc.parse_repos(f"acme/proj-api, {bad}") == ([gc.Repo("acme", "proj-api")], [bad])


def test_parse_repos_of_nothing():
    assert gc.parse_repos("") == ([], []) and gc.parse_repos(" , \n ,") == ([], [])


def test_not_configured(send, settings, hub):
    object.__setattr__(settings, "GITHUB_REPOS", "")
    assert ask(send) == "No hay repos configurados: falta GITHUB_REPOS en el .env de la API."
    object.__setattr__(settings, "GITHUB_REPOS", "basura")
    assert ask(send) == "No hay repos configurados: falta GITHUB_REPOS en el .env de la API.\nNo entendí esta entrada: basura"
    assert hub.requests == []


# ---- pull requests ------------------------------------------------------------------------------

def test_a_pull_request_shows_everything_asked_for(send, hub):
    hub.pulls["acme/proj-api"] = [pr(41, "Add metrics endpoint", author="alice", assignees=["bob"], head="feature/metrics", base="develop", days=5, commits=3)]
    lines = ask(send, "!github acme/proj-api").splitlines()
    assert lines == [
        "🔀 1 PRs abiertos en proj-api",   # one repo selected: no ambiguity, so no owner
        "",
        "📦 proj-api (1)",
        "• #41 Add metrics endpoint",
        "  🟢 open · 5 d · 3 commits",
        "  👤 alice · 🎯 bob",
        "  🌿 feature/metrics → develop",
        "  🔗 https://github.com/acme/proj-api/pull/41",
    ]


def test_draft_no_assignee_singular_commit_and_today(send, hub):
    hub.pulls["acme/proj-web"] = [pr(7, draft=True, days=0, commits=1, assignees=[])]
    text = ask(send, "!github acme/proj-web")
    assert "  📝 draft · hoy · 1 commit" in text and "  👤 alice · 🎯 sin asignar" in text


def test_several_assignees_and_a_missing_author(send, hub):
    item = pr(9, assignees=["bob", "carol"])
    item["user"] = None   # a deleted account
    hub.pulls["acme/proj-web"] = [item]
    assert "  👤 ? · 🎯 bob, carol" in ask(send, "!github acme/proj-web")


def test_the_default_is_pull_requests_and_every_flag_spelling_works(send, hub):
    hub.pulls["acme/proj-web"] = [pr(1)]
    hub.issues["acme/proj-web"] = [issue(2)]
    default = ask(send, "!github proj-web")
    assert default.startswith("🔀") and default == ask(send, "!github proj-web -r") == ask(send, "!github proj-web -R")
    assert default == ask(send, "!github proj-web -pr") == ask(send, "!github proj-web -PRS") == ask(send, "/gh proj-web --pr")
    assert ask(send, "!github proj-web –r") == default    # en dash from a phone keyboard


def test_pull_requests_are_listed_oldest_first_as_github_returns_them(send, hub):
    hub.pulls["acme/proj-api"] = [pr(1, "Old one", days=30), pr(2, "Newer", days=2)]
    text = ask(send, "!github acme/proj-api")
    assert text.index("Old one") < text.index("Newer") and "· 30 d ·" in text


def test_commit_counts_come_from_the_single_pr_endpoint(send, hub):
    hub.pulls["acme/proj-api"] = [pr(1, commits=4), pr(2, commits=1)]
    ask(send, "!github acme/proj-api")
    paths = [r.url.path for r in hub.requests]
    assert "/repos/acme/proj-api/pulls" in paths and "/repos/acme/proj-api/pulls/1" in paths and "/repos/acme/proj-api/pulls/2" in paths


def test_a_failed_commit_lookup_only_drops_the_commit_count(send, hub, monkeypatch):
    hub.pulls["acme/proj-api"] = [pr(1), pr(2)]
    original = gc._get

    def flaky(client, path, params=None):
        if path.endswith("/pulls/2"):
            raise gc.GitHubError("GitHub no respondió a tiempo.")
        return original(client, path, params)

    monkeypatch.setattr(gc, "_get", flaky)
    text = ask(send, "!github acme/proj-api")
    assert "  🟢 open · 3 d · 2 commits" in text and "  🟢 open · 3 d\n" in text + "\n"


def test_long_lists_are_capped_with_a_count_of_the_rest(send, hub):
    hub.pulls["acme/proj-api"] = [pr(i, f"PR {i}") for i in range(1, 14)]
    text = ask(send, "!github acme/proj-api")
    assert text.count("• #") == 10 and "  … y 3 más" in text and "📦 proj-api (13)" in text


def test_a_full_page_says_there_may_be_more(send, hub):
    hub.issues["acme/proj-web"] = [issue(i) for i in range(1, 31)]
    text = ask(send, "!github proj-web -issue")
    assert "📦 proj-web (30+)" in text and "  … y 15+ más" in text


def test_long_titles_are_cut_and_whitespace_is_tidied(send, hub):
    hub.pulls["acme/proj-api"] = [pr(1, "x" * 300), pr(2, "line one\n\nline   two")]
    text = ask(send, "!github acme/proj-api")
    assert "• #1 " + "x" * 99 + "…" in text and "• #2 line one line two" in text


def test_gh_and_github_are_the_same_command(send, hub):
    hub.pulls["acme/proj-web"] = [pr(1)]
    text = ask(send, "!github proj-web")
    assert text.startswith("🔀") and ask(send, "!gh proj-web") == ask(send, "!GH proj-web") == ask(send, "/gh proj-web") == ask(send, "/github proj-web") == text
    assert ask(send, "!gh") == USAGE and ask(send, "!gh proj-web -i").startswith("✅ Sin issues")


# ---- ordering -----------------------------------------------------------------------------------

def test_pull_requests_go_from_the_most_to_the_fewest_days_open_whatever_order_github_returns(send, hub):
    hub.pulls["acme/proj-api"] = [pr(2, "Recent", days=2), pr(1, "Ancient", days=90), pr(3, "Middle", days=15), pr(4, "Brand new", days=0)]
    text = ask(send, "!github acme/proj-api")
    order = [text.index(t) for t in ("Ancient", "Middle", "Recent", "Brand new")]
    assert order == sorted(order) and "· 90 d ·" in text and "· hoy" in text


def test_repos_are_ordered_by_their_oldest_pull_request(send, hub):
    hub.pulls["acme/proj-web"] = [pr(1, "Web PR", days=5)]
    hub.pulls["acme/proj-api"] = [pr(2, "Api recent", days=1), pr(3, "Api old", days=40)]
    hub.pulls["acme/proj-empty"] = []
    text = ask(send)   # "!github proj": all four repos
    assert text.index("📦 acme/proj-api") < text.index("📦 proj-web")   # its oldest PR (40 d) beats the other repo's (5 d)
    assert text.index("Api old") < text.index("Api recent")


def test_a_pull_request_without_a_date_goes_last(send, hub):
    dated, undated = pr(1, "Dated", days=3), pr(2, "Undated")
    undated["created_at"] = None
    hub.pulls["acme/proj-api"] = [undated, dated]
    text = ask(send, "!github acme/proj-api")
    assert text.index("Dated") < text.index("Undated") and "🟢 open · ? " in text


def test_issues_are_not_reordered_and_no_order_is_requested(send, hub):
    hub.issues["acme/proj-web"] = [issue(30, "Third"), issue(10, "First"), issue(20, "Second")]
    text = ask(send, "!github proj-web -i")
    assert [text.index(t) for t in ("Third", "First", "Second")] == sorted(text.index(t) for t in ("Third", "First", "Second"))
    issue_call = next(r for r in hub.requests if r.url.path.endswith("/issues"))
    assert "sort" not in issue_call.url.params and "direction" not in issue_call.url.params


def test_the_pull_request_list_is_requested_oldest_first(send, hub):
    ask(send, "!github proj-web")
    call = next(r for r in hub.requests if r.url.path.endswith("/pulls"))
    assert call.url.params["sort"] == "created" and call.url.params["direction"] == "asc" and call.url.params["state"] == "open"


# ---- issues -------------------------------------------------------------------------------------

def test_issues_show_only_title_number_and_assignee(send, hub):
    hub.issues["acme/proj-web"] = [issue(12, "Login is slow", assignees=["bob"]), issue(15, "Typo in README"), issue(20, "Two owners", ["a", "b"])]
    assert ask(send, "!github acme/proj-web -issue").splitlines() == [
        "🐛 3 issues abiertos en proj-web", "", "📦 proj-web (3)",
        "• #12 Login is slow — 🎯 bob", "• #15 Typo in README", "• #20 Two owners — 🎯 a, b"]


def test_pull_requests_returned_by_the_issues_endpoint_are_dropped(send, hub):
    hub.issues["acme/proj-web"] = [issue(1, "Real issue"), issue(2, "It is a PR", is_pr=True)]
    text = ask(send, "!github proj-web -i")
    assert "Real issue" in text and "It is a PR" not in text and "📦 proj-web (1)" in text


@pytest.mark.parametrize("flag", ["-i", "-issue", "-issues", "--issue", "--issues", "-ISSUE"])
def test_issue_flag_spellings(send, hub, flag):
    hub.issues["acme/proj-web"] = [issue(1)]
    assert ask(send, f"!github proj-web {flag}").startswith("🐛 1 issues abiertos")


# ---- choosing repos -----------------------------------------------------------------------------

def test_a_shared_word_reaches_several_repos_and_empty_ones_are_summarised(send, hub):
    hub.pulls["acme/proj-api"] = [pr(1)]
    hub.pulls["other/proj-api"] = [pr(2, "Other")]
    text = ask(send)   # "!github proj" matches all four
    assert text.splitlines()[0] == "🔀 2 PRs abiertos en 2 de 4 repos"
    assert "📦 acme/proj-api (1)" in text and "📦 other/proj-api (1)" in text, "two repos called proj-api show their owner"
    assert text.endswith("✅ Sin PRs abiertos: proj-web, proj-empty")
    assert {r.url.path for r in hub.requests if r.url.path.endswith("/pulls")} == {f"/repos/{r}/pulls" for r in ("acme/proj-api", "acme/proj-web", "other/proj-api", "acme/proj-empty")}


def test_a_repo_filter_by_exact_partial_or_full_name(send, hub):
    hub.pulls["acme/proj-web"] = [pr(1)]
    hub.pulls["acme/proj-api"] = [pr(2)]
    hub.pulls["other/proj-api"] = [pr(3)]
    assert "📦 proj-web (1)" in ask(send, "!github PROJ-WEB") and "📦 proj-web (1)" in ask(send, "!github proj-w")
    assert ask(send, "!github other/proj-api").splitlines()[0] == "🔀 1 PRs abiertos en proj-api"          # full name is exact
    both = ask(send, "!github proj-api")                                                              # exact name matches both "api"s
    assert both.splitlines()[0] == "🔀 2 PRs abiertos en 2 de 2 repos" and "📦 acme/proj-api (1)" in both and "📦 other/proj-api (1)" in both


def test_an_unknown_repo(send, hub):
    assert ask(send, "!github nada") == 'No tengo un repo que coincida con "nada".'
    assert hub.requests == []


def test_a_single_repo_without_items_says_so(send, hub):
    assert ask(send, "!github acme/proj-empty") == "✅ Sin PRs abiertos en proj-empty"
    assert ask(send, "!github acme/proj-empty -i") == "✅ Sin issues abiertos en proj-empty"
    assert ask(send, "!github proj -i").startswith("✅ Sin issues abiertos en 4 repos")


USAGE = ("Uso: !github <repo> [-r | -i]\n"
         "• sin flag o -r: PRs abiertos del repo (los más antiguos primero)\n"
         "• -i: issues abiertos del repo\n"
         "El repo puede ser una parte de su nombre.")


def test_the_helper_never_names_a_real_repo(send, hub, settings):
    reply = ask(send, "!github")
    assert reply == USAGE
    for repo in gc.parse_repos(settings.GITHUB_REPOS)[0]:
        assert repo.name not in reply and repo.owner not in reply


@pytest.mark.parametrize("text", [
    "!github", "!gh", "/github", "!github -r", "!github -i", "!github -pr", "!github --issue",       # no repo: the helper, nothing listed
    "!github a b", "!github proj-api proj-web",                                                       # two repos
    "!github -r -i", "!github proj-api -i -r", "!github proj-api -pr -issue", "!github -x", "!github proj-web --foo"])
def test_no_repo_or_bad_arguments_show_the_helper_and_fetch_nothing(send, hub, text):
    expected = USAGE.replace("!", "/") if text.startswith("/") else USAGE   # it shows the prefix the user typed
    assert ask(send, text) == expected
    assert hub.requests == []


# ---- errors -------------------------------------------------------------------------------------

def test_one_failing_repo_does_not_hide_the_others(send, hub):
    hub.pulls["acme/proj-api"] = [pr(1)]
    hub.fail["acme/proj-web"] = 404
    text = ask(send)
    assert "📦 acme/proj-api (1)" in text   # two repos are called "api": the owner is shown
    assert "⚠️ proj-web: no lo encuentro (¿es privado? hace falta iniciar sesión: `gh auth login` o un GITHUB_TOKEN con acceso)." in text


def test_a_private_repo_with_a_token_that_cannot_see_it(send, hub, settings):
    object.__setattr__(settings, "GITHUB_TOKEN", "ghp_secret")
    hub.fail["acme/proj-web"] = 404
    assert "⚠️ proj-web: no lo encuentro o el token no tiene acceso." in ask(send)


@pytest.mark.parametrize("failure, expected", [
    (401, "⚠️ proj-api: GitHub rechazó el token (¿venció? Volvé a iniciar sesión con `gh auth login` o revisá GITHUB_TOKEN)."),
    (403, "⚠️ proj-api: Se agotó el límite de consultas de GitHub. Sin sesión son 60 por hora: iniciá sesión con `gh auth login` o configurá GITHUB_TOKEN."),
    (500, "⚠️ proj-api: GitHub respondió 500."),
    (httpx.ReadTimeout("slow"), "⚠️ proj-api: GitHub no respondió a tiempo."),
    (httpx.ConnectError("refused"), "⚠️ proj-api: No pude conectar con GitHub.")])
def test_failures_become_clear_messages(send, hub, failure, expected):
    hub.fail["acme/proj-api"] = failure
    assert expected in ask(send, "!github acme/proj-api")


def test_a_forbidden_repo_that_is_not_a_rate_limit():
    """403 with the rate-limit header is the limit; without it, it is a permissions problem."""
    client = httpx.Client(base_url=gc.BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(403, json={"message": "Resource not accessible"})))
    assert gc.fetch_pull_requests(client, gc.Repo("acme", "api")).error == "GitHub no me dejó ver ese repo (permisos del token)."


def test_unexpected_answers_do_not_crash(send, hub, monkeypatch):
    def make_client(token, timeout):
        return httpx.Client(base_url=gc.BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"not": "a list"})))
    monkeypatch.setattr(gc, "make_client", make_client)
    assert "⚠️ proj-api: GitHub devolvió una respuesta inesperada." in ask(send, "!github acme/proj-api")


def test_malformed_items_are_skipped(send, hub):
    hub.pulls["acme/proj-api"] = [pr(1), {"title": "no number"}, "garbage"]
    assert "📦 proj-api (1)" in ask(send, "!github acme/proj-api")


def test_a_repo_that_failed_is_never_reported_as_having_nothing_open(send, hub):
    hub.fail["acme/proj-api"] = 404
    single = ask(send, "!github acme/proj-api")
    assert single == "⚠️ proj-api: no lo encuentro (¿es privado? hace falta iniciar sesión: `gh auth login` o un GITHUB_TOKEN con acceso)."
    assert "Sin PRs" not in single and "✅" not in single


def test_when_everything_fails_there_is_only_the_warnings(send, hub):
    for repo in ("acme/proj-api", "acme/proj-web", "other/proj-api", "acme/proj-empty"):
        hub.fail[repo] = 500
    text = ask(send)
    assert text.count("⚠️") == 4 and "✅" not in text and not text.startswith("\n")


def test_the_empty_count_only_includes_repos_that_were_read(send, hub):
    hub.fail["acme/proj-api"] = 500          # 1 of 4 fails, the 3 others have nothing open
    text = ask(send)
    assert text.splitlines()[0] == "✅ Sin PRs abiertos en 3 repos" and "⚠️ acme/proj-api: GitHub respondió 500." in text


# ---- safety -------------------------------------------------------------------------------------

def test_only_read_requests_to_the_configured_repos(send, hub):
    hub.pulls["acme/proj-api"] = [pr(1)]
    ask(send)
    ask(send, "!github -issue")
    assert hub.requests and all(r.method == "GET" for r in hub.requests)
    assert all(re.fullmatch(r"/repos/(acme/proj-api|acme/proj-web|other/proj-api|acme/proj-empty)/(pulls|issues)(/\d+)?", r.url.path) for r in hub.requests)


def test_what_is_typed_can_only_select_never_build_a_path(send, hub):
    for text in ("!github ../../user", "!github acme/proj-api/../../x", "!github %2e%2e", "!github acme/proj-api?x=1"):
        assert ask(send, text).startswith(("No tengo un repo", "Uso:"))
    assert hub.requests == []


def test_the_token_is_used_but_never_shown(send, hub, settings):
    object.__setattr__(settings, "GITHUB_TOKEN", "ghp_super_secret_token")
    hub.pulls["acme/proj-api"] = [pr(1)]
    hub.fail["acme/proj-web"] = 401
    reply = ask(send)
    assert "ghp_super_secret_token" not in reply and set(hub.headers) == {"ghp_super_secret_token"}


# ---- where the token comes from -----------------------------------------------------------------

def test_the_configured_token_wins(monkeypatch):
    monkeypatch.setattr(gc.subprocess, "run", lambda *a, **k: pytest.fail("gh must not be run when GITHUB_TOKEN is set"))
    assert gc.resolve_token("configured", True) == "configured"


def test_no_token_and_no_permission_to_use_gh(monkeypatch):
    monkeypatch.setattr(gc.subprocess, "run", lambda *a, **k: pytest.fail("gh must not be run unless allowed"))
    assert gc.resolve_token("", False) == ""


def test_the_gh_session_is_used_when_allowed_and_cached(monkeypatch):
    gc.reset_token_cache()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert argv == ["gh", "auth", "token"] and kwargs["shell"] is False
        return type("Done", (), {"returncode": 0, "stdout": "gho_from_cli\n"})()

    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    assert gc.resolve_token("", True) == "gho_from_cli" and gc.resolve_token("", True) == "gho_from_cli"
    assert len(calls) == 1, "cached: not spawned for every command"


@pytest.mark.parametrize("outcome", ["not logged in", "missing", "timeout"])
def test_a_missing_or_logged_out_gh_gives_no_token_instead_of_failing(monkeypatch, outcome):
    gc.reset_token_cache()

    def fake_run(argv, **kwargs):
        if outcome == "missing":
            raise FileNotFoundError("gh")
        if outcome == "timeout":
            raise gc.subprocess.TimeoutExpired(argv, 5)
        return type("Done", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    assert gc.resolve_token("", True) == ""


def test_a_failed_lookup_is_not_cached_so_a_new_login_is_noticed_at_once(monkeypatch):
    gc.reset_token_cache()
    answers = [type("Done", (), {"returncode": 1, "stdout": ""})(), type("Done", (), {"returncode": 0, "stdout": "gho_after_login\n"})()]
    monkeypatch.setattr(gc.subprocess, "run", lambda *a, **k: answers.pop(0))
    assert gc.resolve_token("", True) == ""                   # not logged in yet
    assert gc.resolve_token("", True) == "gho_after_login"    # the very next command sees the login


def test_the_command_uses_the_gh_token_when_enabled(send, hub, settings, monkeypatch):
    object.__setattr__(settings, "GITHUB_TOKEN_FROM_GH", True)
    monkeypatch.setattr(gc, "resolve_token", lambda configured, from_gh: "gho_from_cli" if from_gh and not configured else configured)
    hub.pulls["acme/proj-api"] = [pr(1)]
    ask(send, "!github acme/proj-api")
    assert hub.headers == ["gho_from_cli"]
