"""The remote browser against a real (headless) Chromium and a tiny local site. Skipped when Chromium isn't installed."""

import http.server
import threading
import time

import pytest

from app.services import remote_browser as rb

PAGES = {
    "/": """<html><head><title>Inicio</title></head><body><h1>Hola</h1>
        <a href="/about">Ir a about</a>
        <a href="http://example.invalid/fuera">Salir del sitio</a>
        <form onsubmit="location.href='/home?u=' + encodeURIComponent(document.getElementById('email').value)
                        + '&p=' + document.getElementById('pw').value.length; return false;">
          <label for="email">Email</label><input id="email" type="email">
          <label for="pw">Contraseña</label><input id="pw" type="password">
          <button type="submit">Entrar</button>
        </form>
        <label for="s">Color</label><select id="s"><option>rojo</option><option>verde</option></select>
        <script>document.getElementById('s').onchange = (e) => document.title = 'color ' + e.target.value</script>
        </body></html>""",
    "/spa": "<html><head><title>SPA</title></head><body><script>setTimeout(() => { document.body.innerHTML = '<button>Tarde</button>' }, 800)</script></body></html>",
    "/about": "<html><head><title>Acerca</title></head><body><p>about</p></body></html>",
    "/home": "<html><head><title>Panel</title></head><body><h1>Bienvenido</h1></body></html>",
}


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        page = PAGES.get(self.path.split("?")[0])
        self.send_response(200 if page else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((page or "no").encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    instance = rb.RemoteBrowser(idle_seconds=600)
    yield instance
    instance.shutdown()


@pytest.fixture(scope="module")
def app(site, browser):
    app = rb.WebApp("prueba", site + "/", user="me@x.com", password="clave")
    try:
        browser.open(app)
    except rb.WebError as error:
        pytest.skip(f"Chromium isn't available here: {error}")
    return app


def mark_of(snapshot, text):
    """The number the page shows next to the element whose description contains `text`."""
    return next(i for i, description in enumerate(snapshot.marks, 1) if text in description)


def test_opening_gives_a_jpeg_and_numbers_what_can_be_tapped(browser, app):
    snapshot = browser.refresh()
    assert snapshot.image[:2] == b"\xff\xd8" and snapshot.title == "Inicio"
    assert "Ir a about (enlace)" in snapshot.marks and "Entrar (botón)" in snapshot.marks
    assert "Email (campo)" in snapshot.marks and "Contraseña (campo)" in snapshot.marks and "Color (lista)" in snapshot.marks


def test_tapping_a_number_follows_that_link_and_back_returns(browser, app):
    snapshot = browser.click(mark_of(browser.refresh(), "Ir a about"))
    assert snapshot.title == "Acerca" and snapshot.url.endswith("/about")
    assert browser.back().title == "Inicio"


def test_a_link_to_another_site_is_not_followed(browser, app):
    with pytest.raises(rb.WebError, match="sale de la app"):
        browser.click(mark_of(browser.refresh(), "Salir del sitio"))
    assert browser.refresh().title == "Inicio", "still on the app's page"


def test_typing_into_a_list_picks_the_option(browser, app):
    snapshot = browser.type(mark_of(browser.refresh(), "Color"), "verde")
    assert browser.refresh().title == "color verde" and snapshot.title == "color verde"


def test_login_types_the_configured_credentials_without_the_chat_ever_seeing_them(browser, app):
    snapshot = browser.login()
    assert snapshot.title == "Panel"
    assert "u=me%40x.com" in snapshot.url and "p=5" in snapshot.url  # the email, and a password of 5 characters
    assert all("clave" not in mark for mark in snapshot.marks)


def test_an_element_that_is_not_on_the_screen_is_reported(browser, app):
    with pytest.raises(rb.WebError, match="No hay un elemento 99"):
        browser.click(99)


def test_an_app_that_is_not_running_is_reported(browser):
    with pytest.raises(rb.WebError, match="No pude abrir"):
        browser.open(rb.WebApp("caida", "http://127.0.0.1:1/"))


def test_nothing_works_before_a_page_is_opened():
    fresh = rb.RemoteBrowser()
    try:
        with pytest.raises(rb.WebError, match="No hay ninguna página abierta"):
            fresh.click(1)
    finally:
        fresh.shutdown()


def test_a_page_that_draws_itself_after_loading_is_waited_for(browser, app, site):
    """What a React app does: the load event fires on an empty page and the content appears a moment later."""
    snapshot = browser.open(rb.WebApp("spa", site + "/spa"))
    assert "Tarde (botón)" in snapshot.marks


def test_the_browser_closes_itself_after_the_idle_time(browser, app, site):
    browser.open(app)  # make sure a session is open (the failed open above left no usable page)
    browser._idle_seconds = 1
    browser.refresh()  # restarts the idle timer with the short time
    deadline = time.time() + 20  # closing a browser takes a few seconds on a slow machine
    while browser.app is not None and time.time() < deadline:
        time.sleep(0.2)
    assert browser.app is None
