"""Runs the Claude Code CLI for the admin-only `!claude` command: `claude -p` in the work directory (normally ~/dev) with
full permissions, one question at a time, and turns its JSON output into a short, scrubbed text for the chat.

Deliberate choices (all asked for by the owner):
  * Full access: `--permission-mode bypassPermissions`. That is only acceptable because the command is admin-only
    (listed admin AND administrator of the group, see CommandContext.is_admin).
  * A money cap per question (`--max-budget-usd`), a questions-per-hour limit (enforced by the command), and a
    wall-clock timeout after which the whole process group is killed.
  * One question at a time: a second one while one runs is refused, never queued (this PC is small).
  * The answer is summarised by the prompt, and cut and redacted here as well: the model can ignore the prompt.
  * Conversation continuity: the CLI session of each chat is kept (in memory) and resumed with `--resume`; `-n` in the
    command starts a new one. An API restart only forgets the pointer.

The question goes through stdin (never argv), so text starting with "-" can't be taken for an option.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Settings
from . import redaction

logger = logging.getLogger("tox.claude")

ELLIPSIS = "…"

SYSTEM_PROMPT = """Estás respondiendo desde un comando de WhatsApp (!claude) que solo usan los administradores.
Reglas de la respuesta:
- Respondé en español, resumido, en un MÁXIMO de {limit} caracteres en total. Sin markdown ni listas largas: texto plano.
- Podés usar herramientas y ejecutar lo que se te pida, pero al final informá solo lo importante, en pocas líneas.
- NUNCA incluyas en la respuesta el contenido de archivos .env ni datos sensibles: claves de API, tokens, contraseñas,
  credenciales, teléfonos, ids de usuarios o de grupos. Si hace falta mostrar un .env, mostrá solo su estructura (los
  nombres de las variables) con los valores omitidos o reemplazados por ***.
"""


class Busy(Exception):
    """Another question is already running."""


class ClaudeError(Exception):
    """Carries a message that is fine to show in the chat."""


@dataclass(frozen=True)
class ClaudeResult:
    text: str
    session_id: str | None
    cost_usd: float | None


_busy = threading.Lock()
_sessions: dict[str, str] = {}  # chat_key -> CLI session id


def forget_sessions() -> None:
    """For tests."""
    _sessions.clear()


def workdir(settings: Settings) -> Path:
    return Path(settings.CLAUDE_WORKDIR).expanduser()


def _binary(settings: Settings) -> str:
    candidates = [settings.CLAUDE_BIN, shutil.which("claude"), str(Path("~/.local/bin/claude").expanduser())]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ClaudeError("No encontré el programa `claude` en esta PC.")


def _run(argv: list[str], question: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               errors="replace", cwd=cwd, shell=False, start_new_session=True)
    try:
        stdout, stderr = process.communicate(question, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)  # the CLI and whatever it started
        process.communicate()
        raise ClaudeError(f"Claude tardó más de {timeout // 60} min y lo corté.") from None
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _parse(output: str) -> dict | None:
    try:
        data = json.loads(output)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def execute(question: str, session_id: str | None, settings: Settings) -> ClaudeResult:
    """Runs one question. Raises ClaudeError (with a chat-safe message) when it can't be answered."""
    cwd = workdir(settings)
    if not cwd.is_dir():
        raise ClaudeError(f"No existe la carpeta de trabajo ({settings.CLAUDE_WORKDIR}).")
    argv = [_binary(settings), "-p", "--output-format", "json", "--permission-mode", "bypassPermissions",
            "--max-budget-usd", f"{settings.CLAUDE_MAX_BUDGET_USD:g}",
            "--append-system-prompt", SYSTEM_PROMPT.format(limit=settings.CLAUDE_MAX_ANSWER_CHARS)]
    done = _run([*argv, *(["--resume", session_id] if session_id else [])], question, cwd, settings.CLAUDE_TIMEOUT_SECONDS)
    data = _parse(done.stdout)
    if session_id and (data is None or data.get("is_error")):
        # The remembered conversation may be gone: ask again from scratch, once
        done = _run(argv, question, cwd, settings.CLAUDE_TIMEOUT_SECONDS)
        data = _parse(done.stdout)
    if data is None:
        logger.error("claude exited %s without JSON: %s", done.returncode, done.stderr.strip()[:300])
        raise ClaudeError("Claude no devolvió una respuesta válida.")
    if data.get("subtype") == "error_max_budget_usd":
        raise ClaudeError(f"La consulta pasó el límite de US${settings.CLAUDE_MAX_BUDGET_USD:g} y se cortó.")
    if data.get("is_error") or not isinstance(data.get("result"), str):
        logger.error("claude error: %s", str(data.get("subtype") or data.get("result"))[:300])
        raise ClaudeError("Claude terminó con un error.")
    return ClaudeResult(data["result"], data.get("session_id"), data.get("total_cost_usd"))


def sanitize(text: str, settings: Settings) -> str:
    """Scrubs secrets, then cuts to CLAUDE_MAX_ANSWER_CHARS (the model may not have kept to the limit)."""
    text = redaction.redact(text.strip(), redaction.secret_values(workdir(settings)))
    limit = settings.CLAUDE_MAX_ANSWER_CHARS
    return text if len(text) <= limit else text[: limit - len(ELLIPSIS)].rstrip() + ELLIPSIS


def start(chat_key: str, question: str, new_conversation: bool, settings: Settings,
          on_done: Callable[[str], None]) -> None:
    """Starts answering in a background thread and calls `on_done(text)` with what to send (an answer or an error
    message). Raises Busy, without starting anything, if another question is running."""
    if not _busy.acquire(blocking=False):
        raise Busy()
    if new_conversation:
        _sessions.pop(chat_key, None)
    session_id = _sessions.get(chat_key)

    def work() -> None:
        try:
            try:
                result = execute(question, session_id, settings)
                if result.session_id:
                    _sessions[chat_key] = result.session_id
                logger.info("CLAUDE answered (%s chars, cost %s USD)", len(result.text), result.cost_usd)
                text = sanitize(result.text, settings) or "(Claude no devolvió texto)"
            except ClaudeError as error:
                text = f"⚠️ {error}"
            except Exception:  # whatever happens, the chat must get an answer and the lock must be released
                logger.exception("CLAUDE failed")
                text = "⚠️ Algo salió mal al consultar a Claude."
            on_done(text)
        finally:
            _busy.release()

    threading.Thread(target=work, name="claude", daemon=True).start()
