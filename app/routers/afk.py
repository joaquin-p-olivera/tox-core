from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_api_key
from ..services import afk

router = APIRouter(prefix="/api/v1", tags=["afk"], dependencies=[Depends(require_api_key)])


class AwayUsers(BaseModel):
    """Everybody who is away right now (!afk), by the id they said it from."""

    user_ids: list[str]


@router.get("/afk", response_model=AwayUsers)
def get_afk(platform: Literal["whatsapp", "telegram"], db: Annotated[Session, Depends(get_db)]) -> AwayUsers:
    """Who is away (!afk). A bot reads it to know which ordinary messages are worth forwarding: a chat with the
    history off sends nothing but commands, and the away list is the one thing that has to be watched in every chat
    -- whoever is looked for there has to be answered for, and whoever is away and writes is back. Polled like
    /censored, read-only and never consumed, so being away is not something the API has to push.
    """
    return AwayUsers(user_ids=afk.away_ids(db, platform))
