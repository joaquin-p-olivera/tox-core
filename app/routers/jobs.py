from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import require_api_key
from ..schemas import JobOut
from ..services import jobs

router = APIRouter(prefix="/api/v1", tags=["jobs"], dependencies=[Depends(require_api_key)])


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    """State of a long-running command (a reply carrying `job`). A bot polls it until `done` and then sends `replies`.
    404 when it is unknown or expired (e.g. the API restarted meanwhile)."""
    found = jobs.get(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    done, replies = found
    return JobOut(status="done" if done else "running", replies=replies)
