"""Lead pool API — prospecting data the broker reviews before Bobbie texts.

The pool belongs to the brokerage, not the account: every read is scoped to the
accounts sharing the caller's brokerage_id, resolved from the JWT and never
from the request. `Lead.user_id` records who imported the row. One brokerage can
therefore never read, campaign or delete another's pool, while teammates work
one shared pool — see app.tenancy.
"""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..leads import campaign as campaign_service
from ..leads import catalog
from ..leads import events as lead_events
from ..leads import service as leads_service
from ..leads import uploads
from ..leads.importer import MAPPABLE_FIELDS
from ..logger import get_logger
from ..models import Lead, User
from ..tenancy import brokerage_user_ids
from ..schemas import LeadCampaignRequest, LeadDeleteRequest
from .auth import get_current_user, get_db

router = APIRouter()
logger = get_logger(__name__)

# The same roles that may run an SMS workspace may work a lead pool.
LEAD_ROLES = {'hob', 'broker', 'agent'}

# Old .xls is not supported: openpyxl reads the modern zip format only.
ACCEPTED_UPLOADS = ('.csv', '.xlsx')


def _parse_mapping(raw: str) -> dict[str, str]:
    """A column mapping chosen by the broker, as {field: column}.

    An empty string for a field means "no column", which is how a wrong guess
    is cleared. Unknown fields are ignored rather than rejected.
    """
    if not (raw or '').strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail='The column mapping is not valid JSON.')
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail='The column mapping must be an object.')
    return {field: str(column or '')
            for field, column in parsed.items()
            if field in MAPPABLE_FIELDS and isinstance(column, (str, type(None)))}


def _require_access(user: User) -> None:
    if user.role not in LEAD_ROLES:
        raise HTTPException(status_code=403, detail='Your role does not have access to the lead pool.')


@router.get('')
def list_leads(
    search: str = '',
    address: list[str] = Query(default=[], description='Street/address fragments'),
    signal: list[str] = Query(default=[]),
    stage: str = '',
    state: list[str] = Query(default=[], description='USPS state codes, e.g. IL'),
    city: list[str] = Query(default=[], description='Town keys from the location facets, e.g. "mattoon|IL"'),
    # Aliased rather than named `zip`, which would shadow the builtin.
    zip_code: list[str] = Query(default=[], alias='zip', description='Five-digit ZIP codes'),
    north: float | None = Query(None, ge=-90, le=90),
    south: float | None = Query(None, ge=-90, le=90),
    east: float | None = Query(None, ge=-180, le=180),
    west: float | None = Query(None, ge=-180, le=180),
    polygon: str = Query('', description='JSON array of [latitude, longitude] points'),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """The broker's pool, filtered by text, signals, stage and location.

    The three location filters are independent — any one of them narrows the
    pool on its own — and the counts returned in `locations` are dependent, each
    computed under the other two, so the menus narrow one another as they are
    used. See app.leads.location for how a location is read out of an address.
    """
    _require_access(current_user)
    try:
        leads_service.validate_bounds(north, south, east, west)
        polygon_points = leads_service.validate_polygon(json.loads(polygon)) if polygon else None
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail='polygon must be valid JSON.')
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    # Stage and last activity are system-owned; refresh them before reading.
    leads_service.sync_activity(session, brokerage_user_ids(session, current_user))
    filtered = leads_service.list_leads(
        session, current_user, search, signal, stage, state, city, zip_code,
        north, south, east, west, polygon_points, addresses=address,
    )
    return {
        'leads': filtered,
        'map': leads_service.map_summary(filtered),
        'facets': leads_service.facets(session, current_user),
        'locations': leads_service.location_facets(session, current_user, state, city, zip_code),
        'signal_catalog': catalog.catalog(),
        'stage_catalog': catalog.stage_catalog(),
    }


async def _stage_upload(file: UploadFile, user: User) -> tuple[str, Path, str]:
    """Validate and stream an upload to disk. Returns (token, path, filename)."""
    name = (file.filename or '').lower()
    if not name.endswith(ACCEPTED_UPLOADS):
        raise HTTPException(status_code=400, detail='Upload a .csv or .xlsx file.')

    try:
        token, path = await uploads.stage(file, user.id)
    except uploads.UploadTooLarge:
        limit = uploads.max_bytes() // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f'The file is larger than {limit} MB.')
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return token, path, name


@router.post('/preview')
async def preview_import(
    file: Optional[UploadFile] = File(None),
    token: str = Form(''),
    mapping: str = Form(''),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """What this file would import, without writing anything.

    Lets the broker choose a row limit and a duplicate policy against real
    numbers instead of guessing.
    """
    _require_access(current_user)
    overrides = _parse_mapping(mapping)

    if token:
        # Re-previewing the staged file with a different column mapping.
        path = uploads.resolve(token, current_user.id)
        if path is None:
            raise HTTPException(status_code=410, detail='That upload has expired. Choose the file again.')
        name = path.name.split('__', 1)[-1].lower()
    elif file is not None:
        token, path, name = await _stage_upload(file, current_user)
    else:
        raise HTTPException(status_code=400, detail='Upload a file or pass a preview token.')

    try:
        report = leads_service.analyze_upload(
            session, current_user, path.read_bytes(), name, overrides,
            mapping_confirmed='property_address' in overrides)
    except Exception as error:  # noqa: BLE001 - the parser must never leak a traceback
        logger.exception('Lead preview failed for user %s', current_user.id)
        raise HTTPException(status_code=400, detail=f'The file could not be read: {error}')

    if not report['importable']:
        # A failed mapping ends this preview. The next attempt must upload the
        # source again instead of reusing potentially incorrect staged input.
        uploads.discard(path)
        report.pop('rows', None)
        raise HTTPException(
            status_code=400,
            detail=report['warnings'][0] if report['warnings'] else 'No leads were found in the file.',
        )
    # The parsed rows are an implementation detail; the panel only needs counts.
    report.pop('rows', None)
    # The file waits under this token so confirming does not re-upload it.
    report['token'] = token
    return report


@router.post('/import')
async def import_leads(
    file: Optional[UploadFile] = File(None),
    token: str = Form(''),
    mapping: str = Form(''),
    limit: int = Form(0),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Import a CSV or Excel file, either freshly uploaded or already previewed.

    Pass `token` from /preview to import the file already on the server, or a
    `file` to upload and import in one step.

    `limit` caps how many rows are taken (0 = all). Every row becomes its own
    lead; nothing is merged.
    """
    _require_access(current_user)
    overrides = _parse_mapping(mapping)

    if token:
        path = uploads.resolve(token, current_user.id)
        if path is None:
            raise HTTPException(
                status_code=410,
                detail='That upload has expired. Choose the file again.',
            )
        name = path.name.split('__', 1)[-1].lower()
    elif file is not None:
        _, path, name = await _stage_upload(file, current_user)
    else:
        raise HTTPException(status_code=400, detail='Upload a file or pass a preview token.')

    if limit < 0:
        uploads.discard(path)
        raise HTTPException(status_code=400, detail='The row limit cannot be negative.')

    try:
        result = leads_service.import_file(
            session, current_user, path.read_bytes(), name, limit, overrides)
    except Exception as error:  # noqa: BLE001 - the parser must never leak a traceback
        session.rollback()
        logger.exception('Lead import failed for user %s', current_user.id)
        raise HTTPException(status_code=400, detail=f'The file could not be read: {error}')
    finally:
        # Imported or not, the staged copy has served its purpose.
        uploads.discard(path)

    if not result['created']:
        raise HTTPException(
            status_code=400,
            detail=result['warnings'][0] if result['warnings'] else 'No leads were found in the file.',
        )
    logger.info('Lead import for user %s: +%s leads', current_user.id, result['created'])
    return result


@router.post('/campaign')
def start_campaign(
    payload: LeadCampaignRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Hand the selected leads to Bobbie; each becomes an SMS conversation."""
    _require_access(current_user)
    try:
        return campaign_service.launch(session, current_user, payload.lead_ids, payload.outreach_reason or '')
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get('/{lead_id}')
def lead_detail(lead_id: int, current_user: User = Depends(get_current_user),
                session: Session = Depends(get_db)):
    """One lead in full, for the details panel."""
    _require_access(current_user)
    leads_service.sync_activity(session, brokerage_user_ids(session, current_user))
    lead = (session.query(Lead)
            .filter(Lead.id == lead_id,
                    Lead.user_id.in_(brokerage_user_ids(session, current_user)))
            .first())
    if not lead:
        raise HTTPException(status_code=404, detail='Lead not found')
    return leads_service.detail(session, lead)


@router.get('/{lead_id}/history')
def lead_history(lead_id: int, current_user: User = Depends(get_current_user),
                 session: Session = Depends(get_db)):
    """The lead's full timeline: intake, campaign attach, assignment moves,
    AI/agent ownership handoffs, and notable activity — oldest first."""
    _require_access(current_user)
    lead = (session.query(Lead)
            .filter(Lead.id == lead_id,
                    Lead.user_id.in_(brokerage_user_ids(session, current_user)))
            .first())
    if not lead:
        raise HTTPException(status_code=404, detail='Lead not found')
    return {'events': lead_events.history_for(session, lead)}


@router.post('/delete')
def delete_many(
    payload: LeadDeleteRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Remove a selection of leads from the pool.

    A POST rather than DELETE: a bulk removal carries a body, and DELETE with a
    request body is not reliably supported across proxies and clients.
    """
    _require_access(current_user)
    deleted = leads_service.delete_leads(session, current_user, payload.lead_ids)
    if not deleted:
        raise HTTPException(status_code=404, detail='None of those leads are in your pool.')
    logger.info('User %s deleted %s lead(s)', current_user.id, deleted)
    return {'deleted': deleted}


@router.delete('/{lead_id}')
def delete_lead(lead_id: int, current_user: User = Depends(get_current_user),
                session: Session = Depends(get_db)):
    _require_access(current_user)
    lead = (session.query(Lead)
            .filter(Lead.id == lead_id,
                    Lead.user_id.in_(brokerage_user_ids(session, current_user)))
            .first())
    if not lead:
        raise HTTPException(status_code=404, detail='Lead not found')
    session.delete(lead)
    session.commit()
    return {'ok': True}
