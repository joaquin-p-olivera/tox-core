from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import require_api_key
from ..services import chat_history

router = APIRouter(prefix="/api/v1", tags=["history"], dependencies=[Depends(require_api_key)])


class HistoryMessage(BaseModel):
    chat_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1, max_length=200)
    sender_id: str = Field(min_length=1)
    sender_name: str | None = None
    text: str = Field(min_length=1, max_length=8192)
    sent_at: datetime


class HistoryBatch(BaseModel):
    platform: Literal["whatsapp", "telegram"]
    messages: list[HistoryMessage] = Field(max_length=100)


class HistoryChats(BaseModel):
    chats: list[str]


@router.get("/history/chats", response_model=HistoryChats)
def get_history_chats(platform: Literal["whatsapp", "telegram"], db: Annotated[Session, Depends(get_db)]) -> HistoryChats:
    """Every chat id (with its aliases) whose messages are being saved: a bot only forwards those."""
    return HistoryChats(chats=sorted(chat_history.enabled_ids(db, platform)))


@router.post("/history")
def save_history(batch: HistoryBatch, db: Annotated[Session, Depends(get_db)],
                 settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, int]:
    """Saves forwarded messages, but only those of chats that were turned on: anything else is ignored, so a bot
    that is out of date can't make the API keep a conversation nobody asked it to."""
    known = chat_history.enabled_ids(db, batch.platform)
    by_chat: dict[str, list[chat_history.Incoming]] = {}
    for message in batch.messages:
        chat_key = known.get(message.chat_id)
        if chat_key is None:
            continue
        sent_at = message.sent_at.astimezone(timezone.utc).replace(tzinfo=None) if message.sent_at.tzinfo else message.sent_at
        by_chat.setdefault(chat_key, []).append(chat_history.Incoming(
            message.message_id, message.sender_id, message.sender_name, message.text, sent_at))
    stored = sum(chat_history.store(settings, chat_key, items) for chat_key, items in by_chat.items())
    return {"stored": stored}
