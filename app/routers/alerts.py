from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_api_key
from ..models import PendingAlert
from ..schemas import Mention, PendingAlertOut

router = APIRouter(prefix="/api/v1", tags=["alerts"], dependencies=[Depends(require_api_key)])


@router.get("/alerts/pending", response_model=list[PendingAlertOut])
def get_pending_alerts(
    platform: Literal["whatsapp", "telegram"],
    db: Annotated[Session, Depends(get_db)],
) -> list[PendingAlertOut]:
    """A bot polls this on its own, with no user message involved, to pick up proactive alerts (e.g.
    !service health changes). Fetch-and-delete: once returned, a row is gone, so a bot restarted between
    polls never gets the same alert twice."""
    rows = list(db.scalars(select(PendingAlert).where(PendingAlert.platform == platform).order_by(PendingAlert.created_at)))
    for row in rows:
        db.delete(row)
    db.commit()
    return [
        PendingAlertOut(chat_id=row.chat_id, text=row.text,
                        mentions=[Mention(user_id=uid) for uid in row.mentions.split(",") if uid])
        for row in rows
    ]
