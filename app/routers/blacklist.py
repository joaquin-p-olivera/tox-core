from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_api_key
from ..models import BlacklistedUser
from ..schemas import BlacklistedOut

router = APIRouter(prefix="/api/v1", tags=["blacklist"], dependencies=[Depends(require_api_key)])


@router.get("/blacklist", response_model=list[BlacklistedOut])
def get_blacklist(platform: Literal["whatsapp", "telegram"], db: Annotated[Session, Depends(get_db)]) -> list[BlacklistedOut]:
    """Everyone permanently banned (!permaban) on this platform. A bot reads it to remove them from the groups it is
    in, and again whenever someone joins one, so it is read-only and never consumed."""
    rows = db.scalars(select(BlacklistedUser).where(BlacklistedUser.platform == platform).order_by(BlacklistedUser.id))
    return [BlacklistedOut(user_ids=row.all_ids, name=row.name) for row in rows]
