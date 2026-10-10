"""Last line of defence for what !claude sends to a chat: whatever the model was told, anything that looks like a secret
of this machine is replaced with *** before the text leaves the API.

Two sources: the VALUES of secret-looking variables (KEY, TOKEN, SECRET, PASSWORD...) of every `.env*` file one level under
the work directory, plus the `password` of any webapps.json there; and a few well-known shapes (provider tokens, WhatsApp
ids). It is a safety net, not the main control: the system prompt asks the model not to print them in the first place.
"""

import json
import re
from pathlib import Path

MASK = "***"
MIN_SECRET_LENGTH = 6  # shorter values would also mangle ordinary words

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|PWD|CREDENTIAL|PRIVATE", re.IGNORECASE)
_SHAPES = (
    re.compile(r"\b(?:rnd|ghp|gho|ghs|ghu|github_pat|sk-ant|sk)[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b\d{6,}(?:-\d+)?@(?:s\.whatsapp\.net|lid|g\.us)\b"),
)


def secret_values(work_dir: Path) -> set[str]:
    values: set[str] = set()
    try:
        env_files = [*work_dir.glob(".env*"), *work_dir.glob("*/.env*")]
        webapps = list(work_dir.glob("*/webapps.json"))
    except OSError:
        return values
    for path in env_files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            name, sep, value = line.partition("=")
            value = value.strip().strip("'\"")
            if sep and not line.lstrip().startswith("#") and _SECRET_NAME.search(name) and len(value) >= MIN_SECRET_LENGTH:
                values.add(value)
    for path in webapps:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = data.values() if isinstance(data, dict) else data if isinstance(data, list) else []
        for entry in entries:
            password = entry.get("password") if isinstance(entry, dict) else None
            if isinstance(password, str) and len(password) >= MIN_SECRET_LENGTH:
                values.add(password)
    return values


def redact(text: str, secrets: set[str]) -> str:
    # Longest first, so a secret that contains another one is masked whole
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, MASK)
    for shape in _SHAPES:
        text = shape.sub(MASK, text)
    return text
