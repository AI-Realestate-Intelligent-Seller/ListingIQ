from fastapi import APIRouter, Depends, HTTPException

from ..models import User
from ..query_progress import ProgressStoreUnavailable, read
from .auth import get_current_user

router = APIRouter()


@router.get('/{job_id}')
def query_progress(job_id: str, current_user: User = Depends(get_current_user)):
    try:
        job = read(job_id)
    except ProgressStoreUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    if job is None or job.get('user_id') != current_user.id:
        raise HTTPException(status_code=404, detail='Query progress was not found.')
    return {key: value for key, value in job.items() if key != 'user_id'}
