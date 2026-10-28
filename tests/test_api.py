from .conftest import API_KEY

PAYLOAD = {"platform": "whatsapp", "chat_id": "g", "user_id": "u", "text": "!uuid"}


def test_health(client):
    from app import __version__

    # The version is there so a deploy can be told apart from the one before it without opening a shell.
    assert client.get("/health").json() == {"status": "ok", "version": __version__}


def test_rejects_missing_api_key(client):
    assert client.post("/api/v1/messages", json=PAYLOAD).status_code == 401


def test_rejects_wrong_api_key(client):
    response = client.post("/api/v1/messages", json=PAYLOAD, headers={"X-API-Key": "nope"})
    assert response.status_code == 401


def test_accepts_valid_api_key(client):
    response = client.post("/api/v1/messages", json=PAYLOAD, headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    (reply,) = response.json()["replies"]
    assert len(reply["text"]) == 36 and reply["mentions"] == [] and reply["audio"] is None


def test_validates_payload(client):
    response = client.post(
        "/api/v1/messages", json={**PAYLOAD, "platform": "signal"}, headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 422
