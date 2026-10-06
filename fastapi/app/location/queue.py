"""Persistent, database-backed lead geocoding queue."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Lead

from .geocoding_service import Geocoder, build_query, get_geocoder


def prepare_lead(lead: Lead) -> None:
    """Queue a new/changed usable address without redoing an unchanged success."""
    query = build_query(lead.property_address, lead.area)
    normalized = query.address if query else None
    # Provider imports may already carry trustworthy coordinates. Record the
    # address fingerprint without discarding them on the initial INSERT.
    if (lead.normalized_address is None and lead.latitude is not None
            and lead.longitude is not None and lead.geocoding_status == 'success'):
        lead.normalized_address = normalized
        return
    if normalized == lead.normalized_address and lead.geocoding_status == 'success':
        return
    if normalized != lead.normalized_address:
        lead.latitude = None
        lead.longitude = None
        lead.geocoded_at = None
        lead.geocoding_provider = None
        lead.geocoding_retry_count = 0
    lead.normalized_address = normalized
    lead.geocoding_error = None
    lead.geocoding_status = 'pending' if normalized else 'invalid_address'


def recover_interrupted(session: Session) -> int:
    count = (session.query(Lead)
             .filter(Lead.geocoding_status == 'processing')
             .update({Lead.geocoding_status: 'pending'}, synchronize_session=False))
    session.commit()
    return count


def process_one(session: Session, geocoder: Geocoder | None = None) -> bool:
    """Process at most one lead so the worker can enforce a global interval."""
    lead = (session.query(Lead)
            .filter(Lead.geocoding_status == 'pending')
            .order_by(Lead.created_at.asc(), Lead.id.asc())
            # Several API processes may each run a worker; never claim the same row twice.
            .with_for_update(skip_locked=True)
            .first())
    if lead is None:
        return False

    query = build_query(lead.property_address, lead.area)
    if query is None:
        lead.geocoding_status = 'invalid_address'
        lead.geocoding_error = 'Address is incomplete or cannot be normalized.'
        session.commit()
        return True

    # Persist the claim before making a network call. A restarted worker turns
    # abandoned processing rows back into pending via recover_interrupted().
    lead.geocoding_status = 'processing'
    lead.normalized_address = query.address
    session.commit()

    try:
        provider = geocoder or get_geocoder()
        result = provider.geocode(query)
        if result is None:
            lead.geocoding_status = 'failed'
            lead.geocoding_error = 'No reliable geocoding match was found.'
            lead.geocoding_retry_count += 1
        else:
            lead.latitude = result.latitude
            lead.longitude = result.longitude
            lead.normalized_address = query.address
            lead.geocoding_provider = result.provider
            lead.geocoded_at = datetime.utcnow()
            lead.geocoding_status = 'success'
            lead.geocoding_error = None
    except Exception as error:  # noqa: BLE001 - durable status keeps the worker alive
        lead.geocoding_retry_count += 1
        lead.geocoding_error = str(error)[:500]
        lead.geocoding_status = (
            'failed'
            if lead.geocoding_retry_count >= settings['geocoding']['max_retries']
            else 'pending'
        )
    session.commit()
    return True
