"""Start/stop/status for a fixed allowlist of LOCAL services on the machine running the API.

Security model (this module can run programs, so read before changing it):
* Only services declared in the JSON file can be touched. The chat picks a *name* and one of three
  fixed actions; it never supplies a program or an argument.
* Programs run as an argv list with ``shell=False``: nothing is ever interpreted by a shell.
* Every run has a timeout. Callers must check the user is an admin first (``admin_only=True``).
"""

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("tox.services")

# Built-in, read-only report of the machine itself: an allowlist entry can't take these names
HOST_NAMES = ("host", "master")  # either name asks for the machine's own report
RESERVED_NAMES = set(HOST_NAMES)
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ACTIONS = ("start", "stop", "status")
MAX_ARGV_ITEMS = 20
MAX_ARG_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 100
MAX_OUTPUT_CHARS = 400

# Optional read-only lookups used to build the detailed status. They are only ever run by the status view:
# there is no flag or word that lets the chat run them (or anything else) on its own.
EXTRA_COMMANDS = ("info", "last_usage", "health")

# The only two flags that change something; no flag means "status" (read-only).
FLAG_ACTIONS = {"-up": "start", "--up": "start", "-down": "stop", "--down": "stop"}


@dataclass(frozen=True)
class ServiceDef:
    name: str
    description: str
    commands: dict[str, tuple[str, ...]]  # action -> argv


@dataclass(frozen=True)
class Catalog:
    services: dict[str, ServiceDef] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # human-readable, shown to the admin
    file_found: bool = False


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None = None
    output: str = ""
    timed_out: bool = False
    error: str | None = None


def _valid_argv(value: object) -> bool:
    return (
        isinstance(value, list)
        and 0 < len(value) <= MAX_ARGV_ITEMS
        and all(isinstance(item, str) and 0 < len(item) <= MAX_ARG_LENGTH for item in value)
    )


def load_catalog(path: str) -> Catalog:
    """Reads the allowlist. Read on every call so edits apply without restarting the API."""
    file = Path(path)
    if not file.is_file():
        return Catalog()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return Catalog(problems=[f"No pude leer {file.name}: {error}"], file_found=True)
    if not isinstance(raw, dict):
        return Catalog(problems=[f"{file.name} tiene que ser un objeto JSON {{\"nombre\": {{...}}}}"], file_found=True)

    services: dict[str, ServiceDef] = {}
    problems: list[str] = []
    for name, entry in raw.items():
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            problems.append(f"\"{name}\": el nombre debe ser minúsculas, números, - o _ (máx. 32)")
            continue
        if name in RESERVED_NAMES:
            problems.append(f"{name}: nombre reservado (es el estado de la PC, junto con {' / '.join(HOST_NAMES)}), usá otro")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{name}: debe ser un objeto")
            continue
        commands: dict[str, tuple[str, ...]] = {}
        reported = False
        for action in (*ACTIONS, *EXTRA_COMMANDS):
            if action not in entry:
                continue
            if _valid_argv(entry[action]):
                commands[action] = tuple(entry[action])
            else:
                reported = True
                problems.append(f"{name}.{action}: debe ser una lista de textos, ej. [\"systemctl\", \"--user\", \"start\", \"x\"]")
        if not any(action in commands for action in ACTIONS):
            if not reported:  # otherwise the specific error above already explains it
                problems.append(f"{name}: no tiene ninguna acción válida (start, stop, status)")
            continue
        description = entry.get("description", "")
        description = description[:MAX_DESCRIPTION_LENGTH] if isinstance(description, str) else ""
        services[name] = ServiceDef(name=name, description=description, commands=commands)
    return Catalog(services=services, problems=problems, file_found=True)


def _environment() -> dict[str, str]:
    """The API's environment, plus the runtime dir `systemctl --user` needs when the API wasn't started from a login shell."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def run_action(service: ServiceDef, action: str, timeout_seconds: int) -> RunResult:
    """Runs one of the service's configured commands. Never raises for expected failures."""
    argv = list(service.commands[action])
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            stdin=subprocess.DEVNULL,
            shell=False,
            check=False,
            env=_environment(),
        )
    except subprocess.TimeoutExpired:  # subprocess.run already killed the child
        return RunResult(timed_out=True)
    except FileNotFoundError:
        return RunResult(error=f"no encontré el programa \"{argv[0]}\"")
    except PermissionError:
        return RunResult(error=f"sin permiso para ejecutar \"{argv[0]}\"")
    except OSError as error:
        return RunResult(error=str(error))

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "…"
    return RunResult(exit_code=completed.returncode, output=output)
