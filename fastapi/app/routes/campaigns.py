"""Campaigns API — drafting, sending and reporting on batches of outreach.

Every campaign belongs to the authenticated broker (Campaign.user_id), taken
from the JWT and never from the request, so one brokerage can never read, edit
or send another's. The lead pool decides *who* is contacted; this module owns
*what they are sent* and *how it went*.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..leads import campaign as campaign_service
from ..logger import get_logger
from ..models import User
from .. import query_progress
from ..schemas import (
    CampaignDetail,
    CampaignDraftRequest,
    CampaignDraftResult,
    CampaignOut,
    CampaignPreview,
    CampaignReasonSuggestions,
    CampaignUpdateRequest,
)
from .auth import get_current_user, get_db

router = APIRouter()
logger = get_logger(__name__)

# The same roles that may work a lead pool may run its campaigns.
CAMPAIGN_ROLES = {'hob', 'broker', 'agent'}


def _require_access(user: User) -> None:
    if user.role not in CAMPAIGN_ROLES:
        raise HTTPException(status_code=403, detail='Your role does not have access to campaigns.')


def _load(session: Session, user: User, campaign_id: int):
    try:
        return campaign_service.owned(session, user, campaign_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.get('', response_model=list[CampaignOut])
def list_campaigns(current_user: User = Depends(get_current_user),
                   session: Session = Depends(get_db)):
    """Every campaign with its delivered / replied / no-reply counts."""
    _require_access(current_user)
    return campaign_service.overview(session, current_user)


def _load_campaigns_job(job_id: str, user_id: int) -> None:
    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            raise LookupError('The account no longer exists.')
        rows = campaign_service.overview(
            session,
            user,
            progress=lambda total, loaded: query_progress.update(
                job_id, total=total, loaded=loaded,
            ),
        )
        query_progress.complete(job_id, rows, total=len(rows))
    except Exception as error:  # noqa: BLE001 - the job must report every failure
        query_progress.fail(job_id, error)
    finally:
        session.close()


def _create_draft_job(job_id: str, user_id: int, payload: dict) -> None:
    """Build one complete draft off-request using the existing progress worker."""
    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            raise LookupError('The account no longer exists.')
        _require_access(user)
        campaign, not_added = campaign_service.create_draft(
            session,
            user,
            payload['lead_ids'],
            payload.get('name') or '',
            payload.get('message_template') or '',
            progress=lambda total, loaded: query_progress.update(
                job_id, total=total, loaded=loaded,
            ),
        )
        result = {
            'campaign': campaign_service.detail(
                session, user, campaign, preview_page=1,
            ),
            'not_added': not_added,
        }
        query_progress.complete(
            job_id, result, total=len(set(payload['lead_ids'])),
        )
        logger.info(
            'Campaign draft %s for user %s: %s locked recipients',
            campaign.id, user_id, campaign_service.member_count(session, user, campaign),
        )
    except Exception as error:  # noqa: BLE001 - every job failure must be visible
        session.rollback()
        query_progress.fail(job_id, error)
    finally:
        session.close()


@router.post('/load', status_code=202)
def start_campaign_load(
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Start a counted campaign query whose progress is held in Redis."""
    _require_access(current_user)
    try:
        job = query_progress.create(current_user.id, 'campaigns')
    except query_progress.ProgressStoreUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    background.add_task(_load_campaigns_job, job['job_id'], current_user.id)
    return {'job_id': job['job_id']}


@router.post('/draft', response_model=CampaignDraftResult)
def create_draft(payload: CampaignDraftRequest,
                 current_user: User = Depends(get_current_user),
                 session: Session = Depends(get_db)):
    """Hold the selected leads under a new draft, ready to compose. Sends nothing."""
    _require_access(current_user)
    try:
        campaign, not_added = campaign_service.create_draft(
            session, current_user, payload.lead_ids,
            payload.name or '', payload.message_template or '')
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    logger.info('Campaign draft %s for user %s: %s leads',
                campaign.id, current_user.id,
                campaign_service.member_count(session, current_user, campaign))
    return {'campaign': campaign_service.detail(session, current_user, campaign, preview_page=1),
            'not_added': not_added}


@router.post('/draft/load', status_code=202)
def start_draft_creation(
    payload: CampaignDraftRequest,
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Queue full-selection validation; preview pagination is not job input."""
    _require_access(current_user)
    try:
        job = query_progress.create(current_user.id, 'campaign-draft')
    except query_progress.ProgressStoreUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    background.add_task(_create_draft_job, job['job_id'], current_user.id, {
        'lead_ids': payload.lead_ids,
        'name': payload.name,
        'message_template': payload.message_template,
    })
    return {'job_id': job['job_id']}


@router.get('/{campaign_id}', response_model=CampaignDetail)
def campaign_detail(campaign_id: int,
                    preview_page: int = Query(1, ge=1),
                    current_user: User = Depends(get_current_user),
                    session: Session = Depends(get_db)):
    """One campaign with the requested 20-recipient preview page."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    return campaign_service.detail(
        session, current_user, campaign, preview_page=preview_page,
    )


@router.patch('/{campaign_id}', response_model=CampaignDetail)
def update_campaign(campaign_id: int, payload: CampaignUpdateRequest,
                    preview_page: int = Query(1, ge=1),
                    current_user: User = Depends(get_current_user),
                    session: Session = Depends(get_db)):
    """Rename the campaign or rewrite its message. Only a draft may be rewritten."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    try:
        campaign_service.update_draft(session, current_user, campaign, payload.name,
                                      payload.message_template, payload.outreach_reason)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return campaign_service.detail(
        session, current_user, campaign, preview_page=preview_page,
    )


@router.post('/{campaign_id}/reason-suggestions', response_model=CampaignReasonSuggestions)
def suggest_reasons(campaign_id: int, current_user: User = Depends(get_current_user),
                    session: Session = Depends(get_db)):
    """Reason wordings that fit the signals this set of leads has in common.

    POST rather than GET: each call spends a DeepSeek request, so it should not
    be something a browser or a proxy can repeat on its own.
    """
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    return campaign_service.suggest_reasons(session, current_user, campaign)


@router.get('/{campaign_id}/preview', response_model=CampaignPreview)
def preview_campaign(campaign_id: int,
                     preview_page: int = Query(1, ge=1),
                     current_user: User = Depends(get_current_user),
                     session: Session = Depends(get_db)):
    """What each recipient would read, without sending anything."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    return campaign_service.preview(
        session, current_user, campaign, page=preview_page,
    )


@router.post('/{campaign_id}/send')
def send_campaign(campaign_id: int, current_user: User = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    """Validate and durably enqueue the complete draft, then return immediately."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    try:
        result = campaign_service.send(session, current_user, campaign)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    logger.info('Campaign %s queued by user %s: %s queued, %s skipped',
                campaign.id, current_user.id, result['queued'], len(result['skipped']))
    return result


@router.delete('/{campaign_id}')
def delete_campaign(campaign_id: int, current_user: User = Depends(get_current_user),
                    session: Session = Depends(get_db)):
    """Discard a draft. A sent campaign is a record and cannot be deleted."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    if campaign.status != 'draft':
        raise HTTPException(status_code=400,
                            detail='A campaign that has been sent cannot be deleted.')
    # Real leads return to the pool; generated provider-contact rows disappear.
    campaign_service.release_draft_members(session, current_user, campaign)
    session.delete(campaign)
    session.commit()
    return {'deleted': campaign_id}
