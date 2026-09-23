import io

import httpx
import pytest
from PIL import Image

from app.services import football_client as fc
from tests.test_mention import raw

API_KEY = "test-key"


def event(home="Peñarol", away="Nacional", when="2026-09-25T23:00:00Z",
          completed=False, live=False, home_score=None, away_score=None):
    status_type = {"completed": completed, "state": "in" if live else ("post" if completed else "pre")}
    competitors = [
        {"homeAway": "home", "team": {"displayName": home}, "score": str(home_score) if home_score is not None else "0"},
        {"homeAway": "away", "team": {"displayName": away}, "score": str(away_score) if away_score is not None else "0"},
    ]
    return {"date": when, "competitions": [{"status": {"type": status_type}, "competitors": competitors}]}


def standing_entry(team, points, played=10, won=5, drawn=3, lost=2):
    def stat(name, value):
        return {"name": name, "value": value}

    return {"team": {"displayName": team},
            "stats": [stat("points", points), stat("pointDifferential", points), stat("gamesPlayed", played),
                      stat("wins", won), stat("ties", drawn), stat("losses", lost)]}


@pytest.fixture
def fake_espn(monkeypatch, settings):
    """A fake ESPN. fake.events / fake.groups: raw items to serve next per call. fake.status: force an
    HTTP status instead. fake.requests: every URL requested (as a list of (url, params))."""
    class Fake:
        pass

    fake = Fake()
    fake.events, fake.groups, fake.status, fake.requests = [], [], None, []
    fc.reset_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        fake.requests.append((request.url.path, dict(request.url.params)))
        if fake.status is not None:
            return httpx.Response(fake.status, json={})
        if request.url.path.endswith("/scoreboard"):
            # Only the "today" call (offset 0) returns events, so a fixed-date test isn't order-sensitive.
            events = fake.events if request.url.params.get("dates") == fake.events_on_date else []
            return httpx.Response(200, json={"events": events})
        if request.url.path.endswith("/standings"):
            return httpx.Response(200, json={"children": [{"name": "", "standings": {"entries": fake.groups}}]}
                                   if fake.groups else {"children": []})
        return httpx.Response(404)

    fake.events_on_date = None

    def make_client(timeout):
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(fc, "make_client", make_client)
    return fake


def test_fixtures_happy_path(send, fake_espn):
    from datetime import date

    today = date.today().strftime("%Y%m%d")
    fake_espn.events_on_date = today
    fake_espn.events = [event(), event("Liverpool", "River Plate", completed=True, home_score=2, away_score=1)]
    (reply,) = send("!futbol uruguay")
    assert "Fixtures — Uruguay" in reply
    assert "Peñarol vs Nacional" in reply
    assert "Liverpool 2-1 River Plate" in reply and "finalizado" in reply


def test_tabla_happy_path_sends_a_real_image(client, fake_espn):
    fake_espn.groups = [standing_entry("River Plate", 18), standing_entry("Boca Juniors", 15)]
    (reply,) = raw(client, "!tabla argentina")
    assert reply["text"] == "" and reply["image"]

    response = client.get(f"/api/v1/images/{reply['image']}", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "PNG"


def test_unknown_country_causes_no_request(send, fake_espn):
    for text in ("!futbol brasil", "!tabla brasil"):
        (reply,) = send(text)
        assert "No conozco ese país" in reply
    assert fake_espn.requests == []


def test_a_second_call_is_served_from_cache(send, fake_espn):
    fake_espn.groups = [standing_entry("River Plate", 18)]
    send("!tabla uruguay")
    calls_after_first = len(fake_espn.requests)
    send("!tabla uruguay")
    assert len(fake_espn.requests) == calls_after_first, "no new request on the second call"


def test_a_failed_fetch_is_not_cached(client, fake_espn):
    fake_espn.status = 500
    (reply,) = raw(client, "!tabla uruguay")
    assert reply["text"].startswith("⚠️") and reply["image"] is None
    fake_espn.status, fake_espn.groups = None, [standing_entry("Peñarol", 20)]
    (reply,) = raw(client, "!tabla uruguay")
    assert reply["image"]


def test_usage_causes_no_request(send, fake_espn):
    for text in ("!futbol", "!fut", "!tabla", "!futbol uruguay argentina", "!tabla uruguay argentina"):
        (reply,) = send(text)
        assert "Uso:" in reply
    assert fake_espn.requests == []


def test_a_league_split_into_real_zones_still_renders_one_image(client, monkeypatch, settings):
    """Confirmed real for Argentina: two independent zone tables (not merged into one ranking).
    Group-by-group rendering itself is covered by test_table_image.py; this only checks the command
    handles a multi-group response without crashing."""
    fc.reset_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"children": [
            {"name": "Group A", "standings": {"entries": [standing_entry("Estudiantes", 30)]}},
            {"name": "Group B", "standings": {"entries": [standing_entry("Independiente Rivadavia", 34)]}},
        ]})

    monkeypatch.setattr(fc, "make_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(handler)))
    (reply,) = raw(client, "!tabla argentina")
    assert reply["image"]


# ---- season/stage selection --------------------------------------------------------------------
# Regression coverage for a real bug: a hardcoded seasontype (e.g. always 1) showed the wrong stage's
# table for leagues that split their year into several (confirmed real for Uruguay: 1=Apertura,
# already finished, vs 4=Clausura, the one actually being played).

def test_the_current_stage_is_read_from_the_scoreboard_not_hardcoded():
    fc.reset_cache()
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        if request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"leagues": [{"season": {"type": {"id": "4"}}}]})
        return httpx.Response(200, json={"children": [{"name": "", "standings": {"entries": [standing_entry("Clausura Leader", 18)]}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        standings = fc.fetch_standings(client, fc.resolve_country("uruguay"), 60)
    assert standings[0].team == "Clausura Leader"
    assert {"seasontype": "4"} in seen_params


def test_falls_back_to_stage_1_when_the_current_stage_has_no_table_yet():
    fc.reset_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"leagues": [{"season": {"type": {"id": "2"}}}]})
        if request.url.params.get("seasontype") == "2":
            return httpx.Response(200, json={"children": []})  # the new stage hasn't been populated yet
        return httpx.Response(200, json={"children": [{"name": "", "standings": {"entries": [standing_entry("Regular Season Leader", 50)]}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        standings = fc.fetch_standings(client, fc.resolve_country("ecuador"), 60)
    assert standings[0].team == "Regular Season Leader"
