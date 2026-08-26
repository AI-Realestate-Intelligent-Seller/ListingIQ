"""Campaigns API — drafting, sending and reporting on batches of outreach.

Every campaign belongs to the authenticated broker (Campaign.user_id), taken
from the JWT and never from the request, so one brokerage can never read, edit
or send another's. The lead pool decides *who* is contacted; this module owns
*what they are sent* and *how it went*.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..leads import campaign as campaign_service
from ..logger import get_logger
from ..models import User
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
                campaign.id, current_user.id, len(payload.lead_ids) - len(not_added))
    return {'campaign': campaign_service.detail(session, current_user, campaign),
            'not_added': not_added}


@router.get('/{campaign_id}', response_model=CampaignDetail)
def campaign_detail(campaign_id: int, current_user: User = Depends(get_current_user),
                    session: Session = Depends(get_db)):
    """One campaign in full, including the rendered message for every recipient."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    return campaign_service.detail(session, current_user, campaign)


@router.patch('/{campaign_id}', response_model=CampaignDetail)
def update_campaign(campaign_id: int, payload: CampaignUpdateRequest,
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
    return campaign_service.detail(session, current_user, campaign)


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
def preview_campaign(campaign_id: int, current_user: User = Depends(get_current_user),
                     session: Session = Depends(get_db)):
    """What each recipient would read, without sending anything."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    return campaign_service.preview(session, current_user, campaign)


@router.post('/{campaign_id}/send')
def send_campaign(campaign_id: int, current_user: User = Depends(get_current_user),
                  session: Session = Depends(get_db)):
    """Send the draft. Bobbie opens a thread per lead and answers the replies."""
    _require_access(current_user)
    campaign = _load(session, current_user, campaign_id)
    try:
        result = campaign_service.send(session, current_user, campaign)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    logger.info('Campaign %s sent by user %s: %s started, %s skipped',
                campaign.id, current_user.id, len(result['started']), len(result['skipped']))
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
    # The leads go back to the pool unattached; only the draft disappears.
    for lead in campaign_service.members(session, current_user, campaign):
        lead.campaign_id = None
    session.delete(campaign)
    session.commit()
    return {'deleted': campaign_id}
