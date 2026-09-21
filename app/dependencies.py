import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from .config import get_settings


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().API_KEY
    # Constant-time comparison so the key can't be guessed byte by byte.
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
