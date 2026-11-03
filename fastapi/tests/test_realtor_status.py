"""A person who says they are a realtor gets an apology and is never contacted again (AI chat only)."""

import json

import pytest

from app import db
from app.ai import schemas as ai_schemas
from app.models import Campaign, Conversation, Message
from app.routes import assignments, followups
from app.schemas import LEAD_STATUSES, FollowUpStatusUpdate, LeadAssignmentStageRequest
from app.sms import bobbie, followup_scheduler, service
from app.sms.classifier import TERMINAL_STATUSES, classify_lead_message, is_realtor, merge_lead_status

REALTOR_SAYS = [
    "I'm a realtor", 'Im a realtor lol', 'I am a real estate agent', 'We are realtors', "I'm also a realtor",
    "I'm actually a licensed agent", "I'm the listing agent", 'I am a real estate broker',
    "I'm an agent myself", "I'm in real estate myself", 'I work in real estate', 'I work as a realtor',
    "I'm a licensed real estate agent", "Hi, I'm a Realtor with Keller Williams",
]
NOT_REALTOR = [
    'I have a realtor', 'My realtor is handling it', "I'm working with a realtor", "I'm not a realtor",
    'Talk to my agent', "I'm talking to a broker", 'Who is this?', 'We already listed with an agent',
    "I'm a mortgage broker", 'I have a licensed realtor',
]


@pytest.mark.parametrize('text', REALTOR_SAYS)
def test_detects_a_realtor(text):
    assert is_realtor(text)


@pytest.mark.parametrize('text', NOT_REALTOR)
def test_having_or_using_a_realtor_is_not_being_one(text):
    assert not is_realtor(text)


def test_general_classifier_does_not_mark_realtor():
    # Realtor handling is only for AI chats, so the inbound classifier leaves it alone.
    assert classify_lead_message("I'm a realtor") is None


def test_realtor_is_known_and_terminal():
    assert 'realtor' in TERMINAL_STATUSES
    assert merge_lead_status('realtor', 'interested') == 'realtor'
    assert 'realtor' in LEAD_STATUSES
    assert 'realtor' in bobbie.LEAD_STATUSES
    assert 'realtor' in assignments.ASSIGNMENT_STAGES and 'realtor' in assignments.COMPLETED_STAGES
    assert 'realtor' in ai_schemas.DISPOSITION_SCHEMA['properties']['lead_status']['enum']
    assert 'realtor' in bobbie.DISPOSITION_SYSTEM
    assert FollowUpStatusUpdate(lead_status='realtor').lead_status == 'realtor'
    assert LeadAssignmentStageRequest(stage='realtor').stage == 'realtor'


class _Conv:
    contact, property_address, lead_status = '+13125550131', '12 Oak St', 'processing'


def _scripted(monkeypatch, **fields):
    payload = {'action': 'continue', 'intent': 'x', 'outcome': 'neutral', 'lead_status': 'processing',
               'confidence': 0.9, 'conversation_stage': 'discovery', 'next_step': 'answer_and_qualify',
               'qualification_focus': '', 'calendar': {'state': 'none', 'should_fetch_availability': False,
                                                       'requested_time_text': '', 'reason': ''},
               'reason': 'x', **fields}
    monkeypatch.setattr(bobbie, 'completion', lambda *a, **k: {'content': json.dumps(payload)})


def _history(text):
    return [{'direction': 'outbound', 'text': 'Hi, are you the owner of 12 Oak St?'},
            {'direction': 'inbound', 'text': text}]


def test_disposition_forces_end_for_a_realtor_even_if_the_ai_continues(monkeypatch):
    _scripted(monkeypatch)
    result = bobbie.analyze_conversation_disposition(_Conv(), _history("I'm a realtor myself"))
    assert (result['action'], result['lead_status'], result['outcome']) == ('end', 'realtor', 'disqualified')


def test_disposition_accepts_ai_realtor_only_when_ending(monkeypatch):
    _scripted(monkeypatch, action='end', outcome='disqualified', lead_status='realtor')
    assert bobbie.analyze_conversation_disposition(_Conv(), _history('I sell homes for a living'))[
        'lead_status'] == 'realtor'
    _scripted(monkeypatch, lead_status='realtor')
    assert bobbie.analyze_conversation_disposition(_Conv(), _history('I have a realtor'))[
        'lead_status'] == 'processing'


def _ai_thread(make_user, email):
    user = make_user(email, role='broker')
    with db.SessionLocal() as session:
        campaign = Campaign(user_id=user.id, name='Spring', status='sent')
        session.add(campaign)
        session.commit()
        conversation = Conversation(contact='+13125550132', user_id=user.id, campaign_id=campaign.id,
                                    property_address='12 Oak St', ai_enabled=True, handled_by='bobbie',
                                    lead_status='processing', queue_status='idle')
        session.add(conversation)
        session.commit()
        return conversation.id


def _state(conversation_id):
    with db.SessionLocal() as session:
        c = session.get(Conversation, conversation_id)
        return {'lead_status': c.lead_status, 'dnc_alert': c.dnc_alert, 'ai_enabled': c.ai_enabled,
                'handled_by': c.handled_by, 'next_followup_at': c.next_followup_at}


def _capture_sends(monkeypatch):
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _s, _c, text, event, *rest: sent.append((text, event)) or Message(id=1))
    return sent


def test_ai_chat_apologizes_and_marks_realtor_never_contact(make_user, monkeypatch):
    conversation_id = _ai_thread(make_user, 'realtor1@example.com')
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(bobbie, 'completion', lambda *a, **k: pytest.fail('no AI call needed'))
    service.process_ai_reply(conversation_id, "Thanks, but I'm a realtor myself")
    assert sent == [(service.REALTOR_APOLOGY, 'ai.closing')]
    assert 'apologies' in service.REALTOR_APOLOGY.lower()
    assert _state(conversation_id) == {'lead_status': 'realtor', 'dnc_alert': True, 'ai_enabled': False,
                                       'handled_by': 'broker', 'next_followup_at': None}
    # A later message does not wake the AI again.
    service.process_ai_reply(conversation_id, 'hello?')
    assert len(sent) == 1


def test_ai_disposition_realtor_uses_the_same_apology(make_user, monkeypatch):
    conversation_id = _ai_thread(make_user, 'realtor2@example.com')
    sent = _capture_sends(monkeypatch)
    monkeypatch.setattr(service, 'resolve_agent_runtime_context', lambda *a, **k: object())
    monkeypatch.setattr(bobbie, 'analyze_conversation_disposition',
                        lambda *a, **k: {**bobbie.fallback_disposition('x'), 'action': 'end',
                                         'lead_status': 'realtor'})
    service.process_ai_reply(conversation_id, 'I sell houses in this area for a living')
    assert sent == [(service.REALTOR_APOLOGY, 'ai.closing')]
    assert _state(conversation_id)['lead_status'] == 'realtor'
    assert _state(conversation_id)['dnc_alert'] is True


def test_broker_handled_chat_is_not_auto_marked(make_user, monkeypatch):
    conversation_id = _ai_thread(make_user, 'realtor3@example.com')
    with db.SessionLocal() as session:
        session.get(Conversation, conversation_id).handled_by = 'broker'
        session.commit()
    sent = _capture_sends(monkeypatch)
    service.process_ai_reply(conversation_id, "I'm a realtor")
    assert sent == []
    assert _state(conversation_id)['lead_status'] == 'processing'


def test_realtor_is_blocked_from_future_campaign_texts(make_user, monkeypatch):
    conversation_id = _ai_thread(make_user, 'realtor4@example.com')
    _capture_sends(monkeypatch)
    service.process_ai_reply(conversation_id, "I'm a realtor")
    from types import SimpleNamespace
    from app.models import Lead
    from app.sms import outbound_worker
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        lead = Lead(user_id=conversation.user_id, phone=conversation.contact, property_address='12 Oak St')
        session.add(lead)
        session.commit()
        job = SimpleNamespace(lead_id=lead.id, to_number=conversation.contact)
        assert outbound_worker._opt_out_reason(session, job) is not None


def test_followups_board_reason_and_manual_status():
    conversation = Conversation(contact='+1', lead_status='realtor', dnc_alert=True, handled_by='broker',
                                meeting_booked=False)
    assert followups._reason(conversation, None, 3) == ('realtor', 'Realtor — do not contact')
