from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_api_key
from ..services import censorship

router = APIRouter(prefix="/api/v1", tags=["censorship"], dependencies=[Depends(require_api_key)])


class CensoredChat(BaseModel):
    """One chat with somebody under censorship in it, and every id those people may appear under."""

    chat_id: str
    user_ids: list[str]


@router.get("/censored", response_model=list[CensoredChat])
def get_censored(platform: Literal["whatsapp", "telegram"], db: Annotated[Session, Depends(get_db)]) -> list[CensoredChat]:
    """Who is under censorship (!censura), by chat. A bot reads it to delete their messages as they arrive, so it is
    read-only and never consumed. Chats with nobody censored are left out."""
    return [CensoredChat(chat_id=chat_id, user_ids=user_ids)
            for chat_id, user_ids in sorted(censorship.by_chat(db, platform).items())]
