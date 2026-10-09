"""A remote-controlled browser, driven from the chat by the admin-only !web command.

The bot can't show a web app by itself, so this opens it in a real (invisible) Chromium on this PC, draws a number on
every button/link/field it can see, and returns a screenshot: the person then says what to tap or type by number. Only the
screenshots leave the PC, through the chat; nothing is exposed to the internet.

Rules that make it safe to leave installed:
* Only apps listed in WEB_APPS_FILE can be opened (the chat picks a *name*, never a URL), and the page can't navigate
  to another site: a top-level navigation outside the app's own origin is blocked. Downloads, pop-ups, dialogs and
  service workers are refused.
* One session at a time, opened on demand and closed after WEB_IDLE_SECONDS without use: this PC has ~4 GB of RAM and the
  browser takes ~450 MB while open.
* Passwords never go through the chat: `login` types the credentials kept in WEB_APPS_FILE (git-ignored). Nothing typed or
  stored there is ever logged or echoed.

Playwright's sync API belongs to the thread that started it, so one dedicated worker thread owns every browser object and
the rest of the API only submits jobs to it.
"""

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger("tox.web")

VIEWPORT = {"width": 412, "height": 915}  # a phone-sized page: small screenshots, mobile layouts
ACTION_TIMEOUT_SECONDS = 45  # the first open on this machine takes ~15 s (browser start + a cold dev server)
PAGE_LOAD_TIMEOUT_MS = 30_000
CLICK_TIMEOUT_MS = 8_000
SCROLL_PIXELS = 700
MAX_MARKS = 40
BROWSER_ARGS = ["--disable-gpu", "--disable-dev-shm-usage"]
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# Words the !web command already uses: an app can't take them as a name
RESERVED_NAMES = {"t", "escribir", "login", "ver", "cerrar", "bajar", "subir", "atras", "enter", "esc", "tab", "ayuda"}
KEYS = {"enter": "Enter", "esc": "Escape", "tab": "Tab"}


class WebError(Exception):
    """The message is safe to show in the chat (it never contains a password or a typed text)."""


@dataclass(frozen=True)
class WebApp:
    name: str
    url: str
    description: str = ""
    user: str = ""
    password: str = ""


@dataclass(frozen=True)
class Snapshot:
    image: bytes  # JPEG
    title: str
    url: str
    marks: list[str] = field(default_factory=list)  # marks[0] describes the element numbered 1


def load_apps(path: str) -> tuple[dict[str, WebApp], list[str]]:
    """The apps the chat may open, and what's wrong with the file (shown to the admin). Read on every call."""
    file = Path(path)
    if not file.is_file():
        return {}, []
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {}, [f"No pude leer {file.name}: {error}"]
    if not isinstance(raw, dict):
        return {}, [f"{file.name} tiene que ser un objeto JSON {{\"nombre\": {{...}}}}"]

    apps: dict[str, WebApp] = {}
    problems: list[str] = []
    for name, entry in raw.items():
        if not isinstance(name, str) or not NAME_PATTERN.match(name) or name in RESERVED_NAMES:
            problems.append(f"\"{name}\": nombre inválido o reservado (minúsculas, números, - o _; máx. 32)")
            continue
        url = entry.get("url") if isinstance(entry, dict) else None
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
            problems.append(f"{name}: falta una \"url\" http(s) válida")
            continue
        login = entry.get("login") if isinstance(entry.get("login"), dict) else {}
        description = entry.get("description")
        apps[name] = WebApp(
            name=name, url=url, description=description if isinstance(description, str) else "",
            user=str(login.get("user") or ""), password=str(login.get("password") or ""),
        )
    return apps, problems


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


# Draws a number on every visible, tappable thing and returns what each one is. The numbers also live on the elements
# (data-tt-mark) so "tap 3" can find them again.
MARK_JS = """
() => {
  const ATTR = 'data-tt-mark';
  document.querySelectorAll('[' + ATTR + ']').forEach((e) => e.removeAttribute(ATTR));
  document.getElementById('tt-marks')?.remove();
  const selector = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=link], '
    + '[role=tab], [role=menuitem], [onclick], [tabindex]:not([tabindex="-1"])';
  const vw = window.innerWidth, vh = window.innerHeight;
  const found = [];
  for (const el of document.querySelectorAll(selector)) {
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6 || r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || s.pointerEvents === 'none') continue;
    const x = Math.min(Math.max(r.left + r.width / 2, 0), vw - 1);
    const y = Math.min(Math.max(r.top + r.height / 2, 0), vh - 1);
    const top = document.elementFromPoint(x, y);
    if (!top || !(el === top || el.contains(top) || top.contains(el))) continue;
    const same = (f) => Math.abs(f.r.left - r.left) < 3 && Math.abs(f.r.top - r.top) < 3
      && Math.abs(f.r.width - r.width) < 3 && Math.abs(f.r.height - r.height) < 3;
    if (found.some(same)) continue;
    found.push({ el, r });
    if (found.length >= %(max)d) break;
  }
  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    const kind = tag === 'a' ? 'enlace' : (tag === 'button' || role === 'button') ? 'botón' : tag === 'select' ? 'lista'
      : (tag === 'input' || tag === 'textarea') ? (el.type === 'checkbox' || el.type === 'radio' ? 'casilla' : 'campo')
      : 'elemento';
    const typed = el.type === 'password' ? '' : el.value;  // never read a password back
    const label = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText)
      || el.innerText || el.placeholder || typed || el.title || el.name || '';
    const text = String(label).replace(/\\s+/g, ' ').trim().slice(0, 30);
    return (text || '(sin texto)') + ' (' + kind + ')';
  };
  const layer = document.createElement('div');
  layer.id = 'tt-marks';
  layer.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  const descriptions = found.map(({ el, r }, i) => {
    el.setAttribute(ATTR, String(i + 1));
    const tag = document.createElement('div');
    tag.textContent = String(i + 1);
    tag.style.cssText = 'position:fixed;left:' + Math.max(r.left, 0) + 'px;top:' + Math.max(r.top, 0) + 'px;'
      + 'background:#e11d48;color:#fff;font:bold 12px/1 sans-serif;padding:2px 4px;border-radius:3px;border:1px solid #fff';
    layer.appendChild(tag);
    return describe(el);
  });
  document.body.appendChild(layer);
  return descriptions;
}
""" % {"max": MAX_MARKS}
CONTENT_JS = "() => !!document.body && (document.body.innerText || '').trim().length > 0"
CONTENT_TIMEOUT_MS = 12_000
UNMARK_JS = "() => document.getElementById('tt-marks')?.remove()"


class RemoteBrowser:
    def __init__(self, idle_seconds: int = 180) -> None:
        self._idle_seconds = idle_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remote-browser")
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()
        self.app: WebApp | None = None  # the app of the open session (None = no browser open)
        # Everything below is touched only by the worker thread
        self._playwright = self._browser = self._context = self._page = None
        self._origin = ""
        self._blocked = False
        self._creating = False  # True while we open a page ourselves (so the pop-up rule doesn't close it)
        self._last_url = ""  # the last page of the app that was shown (to come back to after a blocked navigation)

    # ---- public: callable from any thread ---------------------------------------------------------------------------

    def open(self, app: WebApp) -> Snapshot:
        return self._run(self._open, app)

    def click(self, number: int) -> Snapshot:
        return self._run(self._click, number)

    def type(self, number: int, text: str) -> Snapshot:
        return self._run(self._type, number, text)

    def press(self, key: str) -> Snapshot:
        return self._run(self._press, key)

    def scroll(self, down: bool) -> Snapshot:
        return self._run(self._scroll, down)

    def back(self) -> Snapshot:
        return self._run(self._back)

    def login(self) -> Snapshot:
        return self._run(self._login)

    def refresh(self) -> Snapshot:
        return self._run(self._refresh)

    def close(self) -> None:
        self._cancel_timer()
        try:
            self._executor.submit(self._close).result(timeout=ACTION_TIMEOUT_SECONDS)
        except FutureTimeout:
            logger.warning("The browser took too long to close")

    def shutdown(self) -> None:
        self.close()
        self._executor.shutdown(wait=True)

    # ---- plumbing ----------------------------------------------------------------------------------------------------

    def _run(self, job, *args):
        try:
            result = self._executor.submit(job, *args).result(timeout=ACTION_TIMEOUT_SECONDS)
        except FutureTimeout:
            raise WebError("El navegador tardó demasiado en responder. Probá !web ver en unos segundos.") from None
        self._restart_timer()
        return result

    def _restart_timer(self) -> None:
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._idle_seconds, self._close_idle)
            self._timer.daemon = True
            self._timer.start()

    def _cancel_timer(self) -> None:
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _close_idle(self) -> None:
        logger.info("Closing the remote browser: %d s without use", self._idle_seconds)
        try:
            self._executor.submit(self._close)
        except RuntimeError:  # already shut down
            pass

    # ---- worker thread only ------------------------------------------------------------------------------------------

    def _ensure_page(self) -> None:
        if self._page is not None and not self._page.is_closed():
            return
        self._close()
        try:
            from playwright.sync_api import sync_playwright  # imported here: the API runs fine without Playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(args=BROWSER_ARGS)
        except Exception as error:  # not installed, missing system libraries...
            logger.warning("The browser could not start: %s", error)
            self._close()
            raise WebError("El navegador remoto no está disponible en esta PC (mirá el log de la API).") from None
        self._context = self._browser.new_context(
            viewport=VIEWPORT, locale="es-UY", accept_downloads=False, service_workers="block")
        self._context.route("**/*", self._guard)
        # Any page we didn't open ourselves (a pop-up the app opens) is closed
        self._context.on("page", lambda popup: None if self._creating or popup is self._page else popup.close())
        self._new_page()

    def _new_page(self) -> None:
        """Replaces the current page with a fresh one (also how a page left broken by a failed navigation is discarded)."""
        old = self._page
        self._creating = True
        try:
            self._page = self._context.new_page()
        finally:
            self._creating = False
        self._page.on("dialog", lambda dialog: dialog.dismiss())
        if old is not None and not old.is_closed():
            try:
                old.close()
            except Exception:
                pass

    def _guard(self, route) -> None:
        """Lets everything through except a top-level navigation to another site (images, scripts and API calls the app
        makes to other hosts are fine; the person can't be sent somewhere else)."""
        request = route.request
        if request.is_navigation_request() and request.frame.parent_frame is None and _origin(request.url) != self._origin:
            self._blocked = True
            logger.warning("Blocked a navigation outside the app: %s", _origin(request.url))
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _settle(self, seconds: float = 3.0) -> None:
        """Gives the page a moment to finish what the last action started (never fails)."""
        try:
            self._page.wait_for_load_state("load", timeout=PAGE_LOAD_TIMEOUT_MS)
            self._page.wait_for_load_state("networkidle", timeout=int(seconds * 1000))
        except Exception:
            pass
        try:  # a single-page app (React...) draws itself after the load event: wait until there is something on screen
            self._page.wait_for_function(CONTENT_JS, timeout=CONTENT_TIMEOUT_MS)
        except Exception:
            pass

    def _snapshot(self) -> Snapshot:
        page = self._page
        marks = page.evaluate(MARK_JS)
        image = page.screenshot(type="jpeg", quality=70)
        page.evaluate(UNMARK_JS)
        self._last_url = page.url
        return Snapshot(image=image, title=page.title(), url=page.url, marks=list(marks))

    def _after_action(self, seconds: float = 3.0) -> Snapshot:
        self._settle(seconds)
        if self._blocked:
            self._blocked = False
            try:  # the refused navigation left Chromium on its error page: go back to where the person was
                self._new_page()
                self._page.goto(self._last_url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT_MS)
            except Exception:
                pass
            raise WebError("Ese enlace sale de la app y por seguridad no lo sigo.")
        return self._snapshot()

    def _need_page(self) -> None:
        if self._page is None or self._page.is_closed():
            raise WebError("No hay ninguna página abierta. Abrí una con !web <app>.")

    def _open(self, app: WebApp) -> Snapshot:
        self._ensure_page()
        self.app, self._origin, self._blocked = app, _origin(app.url), False
        try:
            self._page.goto(app.url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT_MS)
        except Exception as error:
            logger.warning("Could not open %s: %s", app.name, error)
            self._new_page()  # Chromium is on its own error page, which would collide with the next attempt
            raise WebError(f"No pude abrir {app.name}: ¿está prendida? ({app.url})") from None
        return self._after_action()

    def _element(self, number: int):
        self._need_page()
        locator = self._page.locator(f'[data-tt-mark="{int(number)}"]')
        if locator.count() == 0:
            raise WebError(f"No hay un elemento {number} en la captura actual. Mandá !web ver para actualizarla.")
        return locator.first

    def _click(self, number: int) -> Snapshot:
        element = self._element(number)
        try:
            element.click(timeout=CLICK_TIMEOUT_MS)
        except Exception as error:
            logger.warning("Could not tap element %s: %s", number, error)
            raise WebError(f"No pude tocar el elemento {number} (¿está tapado por otra cosa?).") from None
        return self._after_action()

    def _type(self, number: int, text: str) -> Snapshot:
        element = self._element(number)
        try:
            if element.evaluate("e => e.tagName.toLowerCase()") == "select":
                element.select_option(label=text, timeout=CLICK_TIMEOUT_MS)
            else:
                element.fill(text, timeout=CLICK_TIMEOUT_MS)
        except Exception as error:
            logger.warning("Could not type into element %s: %s", number, error)
            raise WebError(f"No pude escribir en el elemento {number} (¿es un campo?).") from None
        return self._after_action(1.0)

    def _press(self, key: str) -> Snapshot:
        self._need_page()
        self._page.keyboard.press(KEYS[key])
        return self._after_action()

    def _scroll(self, down: bool) -> Snapshot:
        self._need_page()
        self._page.mouse.wheel(0, SCROLL_PIXELS if down else -SCROLL_PIXELS)
        self._page.wait_for_timeout(300)
        return self._snapshot()

    def _back(self) -> Snapshot:
        self._need_page()
        self._page.go_back(timeout=PAGE_LOAD_TIMEOUT_MS)
        return self._after_action()

    def _refresh(self) -> Snapshot:
        self._need_page()
        return self._snapshot()

    def _login(self) -> Snapshot:
        self._need_page()
        app = self.app
        if app is None or not app.user or not app.password:
            raise WebError("No hay usuario y contraseña configurados para esa app en el archivo de apps (WEB_APPS_FILE).")
        page = self._page
        password = page.locator("input[type=password]:visible").first
        if password.count() == 0:
            raise WebError("No veo un formulario de login en esta página.")
        user = page.locator("input[type=email]:visible, input[name*=user i]:visible, input[name*=mail i]:visible, "
                            "input[type=text]:visible").first
        try:
            user.fill(app.user, timeout=CLICK_TIMEOUT_MS)
            password.fill(app.password, timeout=CLICK_TIMEOUT_MS)
            submit = page.locator("button[type=submit]:visible, input[type=submit]:visible")
            if submit.count():
                submit.first.click(timeout=CLICK_TIMEOUT_MS)
            else:
                password.press("Enter")
        except Exception as error:
            logger.warning("Could not fill the login form of %s: %s", app.name, type(error).__name__)  # no details: may hold a value
            raise WebError("No pude completar el formulario de login.") from None
        return self._after_action(8.0)  # the app's own API may need a few seconds to answer

    def _close(self) -> None:
        for thing in (self._context, self._browser):
            try:
                if thing is not None:
                    thing.close()
            except Exception:
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = self._browser = self._context = self._page = None
        self.app = None


_instance: RemoteBrowser | None = None
_instance_lock = threading.Lock()


def get_browser(idle_seconds: int) -> RemoteBrowser:
    """The one shared browser (created on first use)."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = RemoteBrowser(idle_seconds)
        return _instance


def shutdown() -> None:
    """Closes the browser if one is open (called when the API stops)."""
    global _instance
    with _instance_lock:
        instance, _instance = _instance, None
    if instance is not None:
        instance.shutdown()
