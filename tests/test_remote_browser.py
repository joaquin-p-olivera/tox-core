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
    "/slow": """<html><head><title>Lento</title></head><body>
        <form onsubmit="fetch('/slow-api').then(() => { location.href = '/home?slow=1' }); return false;">
          <input id="e" type="email"><input id="p" type="password"><button type="submit">Entrar</button></form></body></html>""",
    "/bad": """<html><head><title>Mal</title></head><body>
        <form onsubmit="fetch('/bad-api').then(() => { document.title = 'Credenciales inválidas' }); return false;">
          <input id="e" type="email"><input id="p" type="password"><button type="submit">Entrar</button></form></body></html>""",
    "/spa": "<html><head><title>SPA</title></head><body><script>setTimeout(() => { document.body.innerHTML = '<button>Tarde</button>' }, 800)</script></body></html>",
    "/about": "<html><head><title>Acerca</title></head><body><p>about</p></body></html>",
    "/home": "<html><head><title>Panel</title></head><body><h1>Bienvenido</h1></body></html>",
}


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow-api":  # a sleeping free-tier API waking up
            time.sleep(9.5)
        page = PAGES.get(self.path.split("?")[0], "ok" if self.path in ("/slow-api", "/bad-api") else None)
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
    instance = rb.RemoteBrowser(idle_seconds=600, pause_seconds=0.2)  # the real pause is 3 s: no need to wait that in every test
    yield instance
    instance.shutdown()


@pytest.fixture(scope="module")
def app(site, browser):
    app = rb.WebApp("prueba", site + "/")  # no credentials: opening it leaves the login form on screen
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


def with_login(app):
    return rb.WebApp(app.name, app.url, user="me@x.com", password="clave")


def test_opening_an_app_with_credentials_logs_in_by_itself(browser, app):
    snapshot = browser.open(with_login(app))
    assert snapshot.title == "Panel"
    assert "u=me%40x.com" in snapshot.url and "p=5" in snapshot.url  # the email, and a password of 5 characters
    assert all("clave" not in mark for mark in snapshot.marks)
    browser.open(app)  # back to the form for the next tests


def test_login_can_also_be_asked_for_and_needs_credentials(browser, app):
    browser.open(app)
    with pytest.raises(rb.WebError, match="No hay usuario y contraseña"):
        browser.login()
    browser.app = with_login(app)  # as if the app had credentials
    snapshot = browser.login()
    assert snapshot.title == "Panel" and "u=me%40x.com" in snapshot.url
    browser.open(app)


def test_a_login_that_takes_long_is_waited_for(browser, app, site):
    """A free-tier API that is asleep needs a while to answer: longer than the 8 s the page used to be given."""
    started = time.time()
    snapshot = browser.open(rb.WebApp("lento", site + "/slow", user="me@x.com", password="clave"))
    assert snapshot.title == "Panel" and "slow=1" in snapshot.url and time.time() - started >= 9
    browser.open(app)


def test_wrong_credentials_come_back_quickly_with_the_form_still_there(browser, app, site):
    started = time.time()
    snapshot = browser.open(rb.WebApp("mal", site + "/bad", user="me@x.com", password="mala"))
    elapsed = time.time() - started
    assert snapshot.title == "Credenciales inválidas"
    # Stopping early means well under the 40 s it would wait otherwise (this machine is slow: a quick one takes ~10-20 s)
    assert elapsed < rb.LOGIN_WAIT_SECONDS - 5, f"it waited {elapsed:.0f} s"
    browser.open(app)


def test_credentials_are_not_a_problem_on_a_page_without_a_login_form(browser, app, site):
    snapshot = browser.open(rb.WebApp("acerca", site + "/about", user="me@x.com", password="clave"))
    assert snapshot.title == "Acerca"  # nothing to log into: it just opens
    browser.open(app)


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


def test_a_screenshot_waits_the_configured_pause_for_the_data_to_arrive(site):
    slow = rb.RemoteBrowser(idle_seconds=600, pause_seconds=3.0)
    try:
        try:
            slow.open(rb.WebApp("prueba", site + "/"))
        except rb.WebError as error:
            pytest.skip(f"Chromium isn't available here: {error}")
        started = time.time()
        slow.refresh()  # nothing to load: the time it takes is the pause
        assert time.time() - started >= 3.0
    finally:
        slow.shutdown()
