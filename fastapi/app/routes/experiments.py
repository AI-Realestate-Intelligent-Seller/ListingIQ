from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import cache
from ..experiments import scheduler, service
from ..models import User
from ..schemas import (
    ExperimentCreateRequest,
    ExperimentFollowupUpdate,
    ExperimentOut,
    ExperimentRandomLeadAdd,
    ExperimentRenameRequest,
)
from .auth import get_current_user, get_db

router = APIRouter()


def _require_access(user: User):
    if user.role not in {'hob', 'broker'}:
        raise HTTPException(status_code=403, detail='Only brokerage leaders can manage experiments.')


@router.get('', response_model=list[ExperimentOut])
def list_experiments(current_user: User = Depends(get_current_user),
                     session: Session = Depends(get_db)):
    _require_access(current_user)
    return cache.get_or_load(
        'experiments-list-v3',
        cache.DASHBOARD_TABLES,
        {'user': current_user.id, 'role': current_user.role},
        lambda: service.list_experiments(session, current_user),
    )


@router.post('', response_model=ExperimentOut)
def create_experiment(payload: ExperimentCreateRequest,
                      current_user: User = Depends(get_current_user),
                      session: Session = Depends(get_db)):
    _require_access(current_user)
    try:
        experiment = service.create(session, current_user, payload)
        if payload.launch_now:
            scheduler.launch_experiment(experiment.id)
            session.expire_all()
            experiment = service.owned(session, current_user, experiment.id)
        return service.serialize(experiment, session)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get('/{experiment_id}', response_model=ExperimentOut)
def get_experiment(experiment_id: int, current_user: User = Depends(get_current_user),
                   session: Session = Depends(get_db)):
    _require_access(current_user)
    try:
        return cache.get_or_load(
            'experiment-detail-v4',
            cache.DASHBOARD_TABLES,
            {'user': current_user.id, 'role': current_user.role,
             'experiment': experiment_id},
            lambda: service.serialize(
                service.owned(session, current_user, experiment_id), session,
                include_outreach_detail=True,
            ),
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.patch('/{experiment_id}', response_model=ExperimentOut)
def rename_experiment(
    experiment_id: int,
    payload: ExperimentRenameRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    _require_access(current_user)
    try:
        experiment = service.owned(session, current_user, experiment_id)
        return service.serialize(
            service.rename(session, current_user, experiment, payload.name), session,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post('/{experiment_id}/cancel', response_model=ExperimentOut)
def cancel_experiment(experiment_id: int, current_user: User = Depends(get_current_user),
                      session: Session = Depends(get_db)):
    _require_access(current_user)
    try:
        row = service.owned(session, current_user, experiment_id)
        return service.serialize(service.cancel(session, current_user, row), session)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.patch('/{experiment_id}/followups/{campaign_id}', response_model=ExperimentOut)
def update_experiment_followup(
    experiment_id: int,
    campaign_id: int,
    payload: ExperimentFollowupUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    _require_access(current_user)
    try:
        experiment = service.owned(session, current_user, experiment_id)
        updated = service.update_followup(
            session, current_user, experiment, campaign_id, payload,
        )
        return service.serialize(updated, session)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post(
    '/{experiment_id}/variants/{campaign_id}/random-leads',
    response_model=ExperimentOut,
)
def add_experiment_random_leads(
    experiment_id: int,
    campaign_id: int,
    payload: ExperimentRandomLeadAdd,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    _require_access(current_user)
    try:
        experiment = service.owned(session, current_user, experiment_id)
        updated = service.add_random_leads(
            session, current_user, experiment, campaign_id, payload.count,
        )
        return service.serialize(updated, session)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
