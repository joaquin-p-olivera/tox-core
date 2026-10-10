import base64
import io
from datetime import datetime

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.models import PendingAlert, Reminder
from app.services import network_scan, reminders, speedtest, wake_on_lan
from tests.conftest import REFUSED
from tests.test_mention import raw

HEADERS = {"X-API-Key": "test-key"}


def png_bytes(size=(80, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# --- !qr ---

def test_qr_sends_a_real_png(client):
    (reply,) = raw(client, "!qr https://example.com")
    response = client.get(f"/api/v1/images/{reply['image']}", headers=HEADERS)
    assert response.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(response.content)).format == "PNG"


def test_qr_needs_text_and_has_a_limit(client):
    assert raw(client, "!qr")[0]["text"].startswith("Uso:")
    assert "demasiado largo" in raw(client, "!qr " + "x" * 600)[0]["text"]


# --- !sticker from a picture ---

def test_sticker_from_an_image_is_a_512_webp_that_keeps_its_proportions(client):
    payload = {"platform": "whatsapp", "chat_id": "g1", "user_id": "u1", "text": "!sticker", "is_group": True,
               "image": base64.b64encode(png_bytes((80, 40))).decode()}
    (reply,) = client.post("/api/v1/messages", headers=HEADERS, json=payload).json()["replies"]
    response = client.get(f"/api/v1/stickers/{reply['sticker']}", headers=HEADERS)
    sticker = Image.open(io.BytesIO(response.content))
    assert sticker.format == "WEBP" and sticker.size == (512, 512)
    assert sticker.convert("RGBA").getpixel((0, 0))[3] == 0  # transparent margin above the wide picture


def test_sticker_with_something_that_is_not_an_image(client):
    payload = {"platform": "whatsapp", "chat_id": "g1", "user_id": "u1", "text": "!sticker", "is_group": True,
               "image": base64.b64encode(b"not a picture").decode()}
    (reply,) = client.post("/api/v1/messages", headers=HEADERS, json=payload).json()["replies"]
    assert reply["sticker"] is None and "No pude leer" in reply["text"]


# --- !recordar ---

def test_the_time_may_come_first_or_last_and_must_be_valid():
    assert reminders.split_time_and_text("18:30 sacar la basura") == ((18, 30), "sacar la basura")
    assert reminders.split_time_and_text("sacar la basura 7:05") == ((7, 5), "sacar la basura")
    for bad in ("sacar la basura", "25:00 algo", "18:61 algo", "18:30", ""):
        assert reminders.split_time_and_text(bad) is None


def test_the_next_occurrence_is_today_if_ahead_else_tomorrow_in_the_configured_zone():
    now = datetime(2026, 10, 10, 12, 0)  # 09:00 in Montevideo (UTC-3)
    assert reminders.next_occurrence(18, 30, now, "America/Montevideo") == datetime(2026, 10, 10, 21, 30)
    assert reminders.next_occurrence(8, 0, now, "America/Montevideo") == datetime(2026, 10, 11, 11, 0)


def test_a_reminder_is_stored_and_delivered_to_the_same_chat_tagging_who_asked(client, db_session):
    (reply,) = raw(client, "!recordar 18:30 sacar la basura", user="u7", chat="g9")
    assert reply["text"].startswith("⏰ Listo, te lo recuerdo") and "18:30: sacar la basura" in reply["text"]

    assert client.get("/api/v1/alerts/pending?platform=whatsapp", headers=HEADERS).json() == []  # not due yet
    db_session.execute(Reminder.__table__.update().values(due_at=datetime(2020, 1, 1, 12, 0)))
    db_session.commit()
    (alert,) = client.get("/api/v1/alerts/pending?platform=whatsapp", headers=HEADERS).json()
    assert alert["chat_id"] == "g9" and alert["mentions"] == [{"user_id": "u7", "user_name": None}]
    assert alert["text"].startswith("⏰ Recordatorio para {@0}: sacar la basura")
    assert client.get("/api/v1/alerts/pending?platform=whatsapp", headers=HEADERS).json() == []  # only once


def test_recordar_asks_for_what_and_when_and_limits_pending_ones(client):
    for text in ("!recordar", "!recordar 18:30", "!recordar comprar pan"):
        assert raw(client, text)[0]["text"].startswith("Uso:")
    for _ in range(reminders.MAX_PENDING_PER_USER):
        raw(client, "!recordar 23:59 algo")
    assert "recordatorios pendientes" in raw(client, "!recordar 23:59 uno más")[0]["text"]
    assert "recordatorios pendientes" not in raw(client, "!recordar 23:59 algo", user="u2")[0]["text"]


# --- !wol ---

@pytest.fixture
def devices_file(settings, tmp_path):
    path = tmp_path / "devices.json"
    path.write_text('{"00:11:22:33:44:55": "Mac de Ana", "5a:11:22:33:44:66": "Desktop"}')
    return path


def test_the_magic_packet_is_ff_then_the_mac_sixteen_times():
    packet = wake_on_lan.magic_packet("00:11:22:33:44:55")
    assert packet == b"\xff" * 6 + bytes.fromhex("001122334455") * 16
    with pytest.raises(ValueError):
        wake_on_lan.magic_packet("nope")


def test_wol_without_params_lists_the_devices(client, devices_file, monkeypatch):
    monkeypatch.setattr(wake_on_lan, "send", lambda *a: pytest.fail("listing must not send anything"))
    (reply,) = raw(client, "!wol", user="admin1@lid")
    assert "1. Mac de Ana — 00:11:22:33:44:55" in reply["text"] and "2. Desktop" in reply["text"]


def test_wol_wakes_one_by_number_or_name(client, devices_file, monkeypatch):
    sent = []
    monkeypatch.setattr(wake_on_lan, "send", lambda mac, broadcasts: sent.append(mac))
    monkeypatch.setattr(network_scan, "primary_network", lambda: None)
    assert "Mac de Ana" in raw(client, "!wol 1", user="admin1@lid")[0]["text"]
    assert "Desktop" in raw(client, "!wol desk", user="admin1@lid")[0]["text"]
    assert sent == ["00:11:22:33:44:55", "5a:11:22:33:44:66"]
    assert "No encontré" in raw(client, "!wol 9", user="admin1@lid")[0]["text"]
    assert len(sent) == 2


def test_wol_and_speedtest_are_admin_only(send, devices_file, monkeypatch):
    monkeypatch.setattr(wake_on_lan, "send", lambda *a: pytest.fail("a non-admin must never send"))
    monkeypatch.setattr(speedtest, "run", lambda *a, **k: pytest.fail("a non-admin must never run a test"))
    assert send("!wol 1", user="someone@lid") == REFUSED
    assert send("!speedtest", user="someone@lid") == REFUSED


# --- !speedtest ---

def fake_cloudflare():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/__down":
            return httpx.Response(200, content=b"x" * int(request.url.params["bytes"] or 0))
        return httpx.Response(200)
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def test_speedtest_reports_latency_download_and_upload():
    result = speedtest.run(phase_seconds=0.05, client_factory=fake_cloudflare())
    assert result.latency_ms >= 0 and result.download_mbps > 0 and result.upload_mbps > 0


def test_speedtest_command_formats_the_result_and_a_failure_is_friendly(client, monkeypatch):
    monkeypatch.setattr(speedtest, "run", lambda: speedtest.SpeedResult(18.4, 94.26, 21.0))
    text = raw(client, "!speedtest", user="admin1@lid")[0]["text"]
    assert "94.3 Mbps" in text and "21.0 Mbps" in text and "18 ms" in text

    def failing():
        raise speedtest.Busy("Ya hay una prueba de velocidad en curso, esperá a que termine.")
    monkeypatch.setattr(speedtest, "run", failing)
    assert "en curso" in raw(client, "!speedtest", user="admin1@lid")[0]["text"]
