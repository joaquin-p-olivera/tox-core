from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import require_api_key
from ..schemas import IncomingMessage, MessageResponse
from ..services.command_router import handle_message

router = APIRouter(prefix="/api/v1", tags=["messages"], dependencies=[Depends(require_api_key)])


@router.post("/messages", response_model=MessageResponse)
def receive_message(
    message: IncomingMessage,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    """Process a chat message. Bots forward every message here and send back the replies."""
    return MessageResponse(replies=handle_message(message, db, settings))
