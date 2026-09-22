from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from ..config import Settings, get_settings
from ..dependencies import require_api_key
from ..services import audio_library, media_cache

router = APIRouter(prefix="/api/v1", tags=["audios"], dependencies=[Depends(require_api_key)])


@router.get("/audios/{name}")
def get_audio(name: str, settings: Annotated[Settings, Depends(get_settings)]) -> Response:
    """Serves a sound clip to the bots: either a preset file (AUDIOS_DIR, e.g. !m) or a generated one
    (e.g. !voz), which lives in the short-lived media cache instead of on disk."""
    path = audio_library.find_audio(settings.AUDIOS_DIR, name)
    if path is not None:
        return FileResponse(path, media_type=audio_library.mimetype_for(path))
    cached = media_cache.get(name)
    if cached is not None:
        data, mimetype = cached
        return Response(content=data, media_type=mimetype)
    raise HTTPException(status_code=404, detail="Audio not found")
