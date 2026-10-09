import logging
import unicodedata

from ..schemas import Reply
from ..services import media_cache, remote_browser
from .registry import CommandContext, command

logger = logging.getLogger("tox.web")

MAX_LISTED_MARKS = 25


def _plain(word: str) -> str:
    """"Atrás" -> "atras": accents and case don't matter when typing on a phone."""
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if unicodedata.category(c) != "Mn")


def _help(prefix: str, apps: dict, problems: list[str]) -> str:
    lines = ["🌐 Navegador remoto: abre una app de la PC y la manejás con capturas numeradas."]
    if apps:
        lines.append("Apps:")
        lines += [f"• {app.name}" + (f" — {app.description}" if app.description else "") for app in apps.values()]
    else:
        lines.append("No hay apps configuradas (falta webapps.json, mirá webapps.example.json).")
    lines += [
        "",
        f"{prefix}web <app> — la abre",
        f"{prefix}web <número> — toca ese elemento",
        f"{prefix}web t <número> <texto> — escribe en ese campo",
        f"{prefix}web login — repite el login (al abrir una app con credenciales, entra solo)",
        f"{prefix}web enter | esc | tab — teclas",
        f"{prefix}web bajar | subir — desplaza la página",
        f"{prefix}web atras | ver | close",
    ]
    if problems:
        lines += ["", "⚠️ Problemas en la configuración:", *(f"- {problem}" for problem in problems)]
    return "\n".join(lines)


def _caption(snapshot: remote_browser.Snapshot, app_name: str) -> str:
    lines = [f"🌐 {app_name} — {snapshot.title or snapshot.url}"]
    if not snapshot.marks:
        return lines[0] + "\n(no veo nada para tocar en esta pantalla)"
    lines += [f"{number} {description}" for number, description in enumerate(snapshot.marks[:MAX_LISTED_MARKS], 1)]
    if len(snapshot.marks) > MAX_LISTED_MARKS:
        lines.append(f"… y {len(snapshot.marks) - MAX_LISTED_MARKS} más en la captura")
    return "\n".join(lines)


@command(
    "web",
    description="(Admin) Abre una app de la PC en un navegador remoto y la manejás por chat con capturas numeradas",
    usage="web <app> | web <número> | web t <número> <texto> | web login | web close",
    aliases=("w",),
    category="Administración",
    admin_only=True,
)
def web(ctx: CommandContext) -> str | Reply:
    settings = ctx.settings
    apps, problems = remote_browser.load_apps(settings.WEB_APPS_FILE)
    if not ctx.args:
        return _help(ctx.prefix, apps, problems)

    word = _plain(ctx.args[0])
    browser = remote_browser.get_browser(settings.WEB_IDLE_SECONDS, settings.WEB_SCREENSHOT_DELAY_SECONDS)
    who = ctx.message.user_key
    try:
        if word.isdigit():
            logger.info("WEB by %s: tap %s", who, word)
            snapshot = browser.click(int(word))
        elif word in ("t", "escribir"):
            parts = ctx.raw_args.split(None, 2)  # the text keeps its own spaces
            if len(parts) < 3 or not parts[1].isdigit():
                return f"Uso: {ctx.prefix}web t <número> <texto> — por ejemplo {ctx.prefix}web t 2 hola"
            logger.info("WEB by %s: type into %s (%d characters)", who, parts[1], len(parts[2]))  # never the text itself
            snapshot = browser.type(int(parts[1]), parts[2])
        elif word == "login":
            logger.info("WEB by %s: login", who)
            snapshot = browser.login()
        elif word in remote_browser.KEYS:
            logger.info("WEB by %s: key %s", who, word)
            snapshot = browser.press(word)
        elif word in ("bajar", "subir"):
            snapshot = browser.scroll(down=word == "bajar")
        elif word == "atras":
            snapshot = browser.back()
        elif word == "ver":
            snapshot = browser.refresh()
        elif word == "close":
            was_open = browser.app is not None
            browser.close()
            return "🔒 Navegador cerrado." if was_open else "No había nada abierto."
        elif word in apps:
            logger.info("WEB by %s: open %s", who, word)
            snapshot = browser.open(apps[word])
        else:
            return f"No conozco \"{ctx.args[0]}\".\n\n" + _help(ctx.prefix, apps, problems)
    except remote_browser.WebError as error:
        return f"⚠️ {error}"

    image_id = media_cache.store(snapshot.image, "image/jpeg", settings.MEDIA_CACHE_SECONDS)
    app_name = browser.app.name if browser.app else "web"
    return Reply(image=image_id, text=_caption(snapshot, app_name))
