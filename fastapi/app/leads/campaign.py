"""Turning selected leads into Bobbie conversations.

A campaign is simply a batch hand-off: each selected lead gets a conversation
in the SMS workspace, Bobbie sends the introduction, and the lead's stage moves
to "In campaign". Anything that cannot be sent is reported back per lead rather
than failing the whole batch — a broker selecting forty rows should not lose
thirty-nine of them to one bad phone number.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import Conversation, Lead, Message, User
from ..sms import service as sms_service
from ..sms.outreach import build_initial_outreach, build_single_lead_context
from .service import lead_details, outreach_reason, split_signals

MAX_CAMPAIGN_SIZE = 200


def _blocking_reason(lead: Lead) -> str | None:
    if lead.dnc:
        return 'On the do-not-contact list.'
    if not lead.phone:
        return 'No usable phone number.'
    if not lead.property_address:
        return 'No property address for Bobbie to reference.'
    if lead.conversation_id is not None:
        return 'Already in a campaign.'
    return None


def launch(session: Session, user: User, lead_ids: list[int], reason: str = '') -> dict:
    """Start Bobbie on each eligible lead. Returns per-lead outcomes."""
    if not lead_ids:
        return {'started': [], 'skipped': []}
    if len(lead_ids) > MAX_CAMPAIGN_SIZE:
        raise ValueError(f'A campaign can hold at most {MAX_CAMPAIGN_SIZE} leads.')

    leads = (session.query(Lead)
             .filter(Lead.user_id == user.id, Lead.id.in_(lead_ids))
             .all())
    found = {lead.id: lead for lead in leads}

    started: list[dict] = []
    skipped: list[dict] = []

    for lead_id in lead_ids:
        lead = found.get(lead_id)
        if lead is None:
            skipped.append({'lead_id': lead_id, 'owner_name': None, 'reason': 'Lead not found.'})
            continue
        blocked = _blocking_reason(lead)
        if blocked:
            skipped.append({'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': blocked})
            continue

        # Reuse an untouched thread for this number rather than duplicating it.
        conversation = (session.query(Conversation)
                        .filter(Conversation.contact == lead.phone, Conversation.user_id == user.id)
                        .first())
        if conversation is not None and session.query(Message).filter(
                Message.conversation_id == conversation.id).count():
            lead.conversation_id = conversation.id
            session.commit()
            skipped.append({
                'lead_id': lead.id,
                'owner_name': lead.owner_name,
                'reason': 'This number already has a conversation in the SMS tab.',
            })
            continue

        why = outreach_reason(lead, reason)
        name = lead.owner_name or 'there'
        conversation = conversation or Conversation(
            contact=lead.phone, user_id=user.id, created_at=datetime.utcnow())
        conversation.name = lead.owner_name
        conversation.property_address = lead.property_address
        context = build_single_lead_context(lead.property_address, why)
        context['lead_source'] = 'Lead pool campaign'
        context['signals'] = split_signals(lead)
        # Imported attributes are approved facts, so Bobbie may cite them.
        context['property_details'] = lead_details(lead)
        conversation.lead_context = json.dumps(context)
        conversation.ai_enabled = True
        conversation.recipient_ai_enabled = False
        conversation.handled_by = 'bobbie'
        conversation.lead_status = 'processing'
        conversation.queue_status = 'idle'
        conversation.processed_at = None
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        text = build_initial_outreach(name, lead.property_address, why)
        try:
            sms_service.send_and_store_message(session, conversation, text, 'outreach.initial')
        except sms_service.SmsDeliveryError as error:
            skipped.append({'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': str(error)})
            continue

        lead.conversation_id = conversation.id
        lead.last_activity_at = datetime.utcnow()
        session.commit()
        started.append({
            'lead_id': lead.id,
            'owner_name': lead.owner_name,
            'conversation_id': conversation.id,
            'text': text,
        })

    return {'started': started, 'skipped': skipped}
