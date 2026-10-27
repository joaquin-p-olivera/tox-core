from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..dependencies import require_api_key
from ..services import media_cache

router = APIRouter(prefix="/api/v1", tags=["videos"], dependencies=[Depends(require_api_key)])


@router.get("/videos/{video_id}")
def get_video(video_id: str) -> Response:
    """Serves the video of a YouTube link (!yt). Short-lived: bots must fetch it right after the reply."""
    cached = media_cache.get(video_id)
    if cached is None:
        raise HTTPException(status_code=404, detail="Video not found or expired")
    data, mimetype = cached
    return Response(content=data, media_type=mimetype)
