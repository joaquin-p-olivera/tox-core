from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..dependencies import require_api_key
from ..services import media_cache

router = APIRouter(prefix="/api/v1", tags=["images"], dependencies=[Depends(require_api_key)])


@router.get("/images/{image_id}")
def get_image(image_id: str) -> Response:
    """Serves a generated image (e.g. !futbol -t's table). Short-lived: bots must fetch it right after the reply."""
    cached = media_cache.get(image_id)
    if cached is None:
        raise HTTPException(status_code=404, detail="Image not found or expired")
    data, mimetype = cached
    return Response(content=data, media_type=mimetype)
