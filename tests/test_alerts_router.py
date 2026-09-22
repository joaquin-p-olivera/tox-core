from app.models import PendingAlert

API_KEY = "test-key"


def test_pending_alerts_are_returned_once_and_only_for_their_platform(client, db_session):
    db_session.add_all([
        PendingAlert(platform="telegram", chat_id="-100", text="🔴 demo: caído."),
        PendingAlert(platform="whatsapp", chat_id="1@g.us", text="🔴 demo: caído."),
    ])
    db_session.commit()

    response = client.get("/api/v1/alerts/pending", params={"platform": "telegram"}, headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    body = response.json()
    assert body == [{"chat_id": "-100", "text": "🔴 demo: caído.", "mentions": []}]

    # gone once fetched
    assert client.get("/api/v1/alerts/pending", params={"platform": "telegram"},
                       headers={"X-API-Key": API_KEY}).json() == []
    # the whatsapp one is untouched
    assert client.get("/api/v1/alerts/pending", params={"platform": "whatsapp"},
                       headers={"X-API-Key": API_KEY}).json() == [
        {"chat_id": "1@g.us", "text": "🔴 demo: caído.", "mentions": []}]


def test_pending_alerts_carry_their_mentions(client, db_session):
    db_session.add(PendingAlert(platform="whatsapp", chat_id="g@g.us", text="{@0}", mentions="1@lid,2@lid"))
    db_session.commit()
    body = client.get("/api/v1/alerts/pending", params={"platform": "whatsapp"}, headers={"X-API-Key": API_KEY}).json()
    assert body == [{"chat_id": "g@g.us", "text": "{@0}",
                     "mentions": [{"user_id": "1@lid", "user_name": None}, {"user_id": "2@lid", "user_name": None}]}]


def test_requires_the_api_key(client):
    assert client.get("/api/v1/alerts/pending", params={"platform": "telegram"}).status_code == 401
