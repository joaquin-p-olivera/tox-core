from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..config import Settings, get_settings
from ..dependencies import require_api_key
from ..services import audio_library

router = APIRouter(prefix="/api/v1", tags=["audios"], dependencies=[Depends(require_api_key)])


@router.get("/audios/{name}")
def get_audio(name: str, settings: Annotated[Settings, Depends(get_settings)]) -> FileResponse:
    """Serves a sound clip to the bots. The Content-Type tells them how to send it."""
    path = audio_library.find_audio(settings.AUDIOS_DIR, name)
    if path is None:
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(path, media_type=audio_library.mimetype_for(path))
