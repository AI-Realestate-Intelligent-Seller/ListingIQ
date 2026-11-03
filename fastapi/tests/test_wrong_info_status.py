"""Wrong number / wrong info lead status."""

import json
from datetime import datetime, timedelta

import pytest

from app.ai import schemas as ai_schemas
from app.models import Campaign, Conversation, Message
from app.routes import assignments, followups
from app.schemas import LEAD_STATUSES, FollowUpStatusUpdate, LeadAssignmentStageRequest
from app.sms import bobbie, followup_scheduler
from app.sms.classifier import TERMINAL_STATUSES, classify_lead_message, merge_lead_status


@pytest.mark.parametrize('text', [
    'Wrong number', 'wrong #', 'You have the wrong person', 'you got the wrong guy lol',
    "We don't live there", 'we dont live there anymore', 'I never lived at that address',
    "He doesn't live here", "I'm not the owner", "We're not the owners", "That's not my house",
    "This isn't our property", 'I do not own that property', 'Nobody by that name here',
    'No one with that name lives here', 'Wrong info',
])
def test_wrong_person_replies_are_wrong_info(text):
    assert classify_lead_message(text) == {'lead_status': 'wrong_info', 'terminal': True}


@pytest.mark.parametrize('text,expected', [
    ('Not interested', 'not_interested'),
    ('Stop, wrong number', 'dnc'),  # an opt-out always wins
    ("I don't live there, it's a rental and I might sell", 'interested'),
    ('I want to sell my house', 'interested'),
    ('Not interested in selling my house', 'not_interested'),
])
def test_other_replies_are_not_wrong_info(text, expected):
    assert classify_lead_message(text)['lead_status'] == expected


@pytest.mark.parametrize('text', [
    'We don’t live there but we still own it', 'who is this?', 'Yes I’d sell'])
def test_owner_or_neutral_replies_are_left_to_the_ai(text):
    assert classify_lead_message(text) is None


def test_wrong_info_is_terminal_and_only_dnc_overrides_it():
    assert 'wrong_info' in TERMINAL_STATUSES
    assert merge_lead_status('processing', 'wrong_info') == 'wrong_info'
    assert merge_lead_status('interested', 'wrong_info') == 'wrong_info'
    assert merge_lead_status('wrong_info', 'interested') == 'wrong_info'
    assert merge_lead_status('wrong_info', 'dnc') == 'dnc'


def test_wrong_info_is_a_known_status_everywhere():
    assert 'wrong_info' in LEAD_STATUSES
    assert 'wrong_info' in bobbie.LEAD_STATUSES
    assert 'wrong_info' in assignments.ASSIGNMENT_STAGES
    assert 'wrong_info' in assignments.COMPLETED_STAGES
    assert 'wrong_info' in ai_schemas.DISPOSITION_SCHEMA['properties']['lead_status']['enum']
    assert 'wrong_info' in bobbie.DISPOSITION_SYSTEM
    assert FollowUpStatusUpdate(lead_status='wrong_info').lead_status == 'wrong_info'
    assert LeadAssignmentStageRequest(stage='wrong_info').stage == 'wrong_info'


def test_fallback_disposition_ends_with_wrong_info():
    result = bobbie.fallback_disposition("Sorry, we don't live there")
    assert result['action'] == 'end'
    assert result['lead_status'] == 'wrong_info'
    assert result['outcome'] == 'disqualified'
    # A plain rejection is unchanged.
    assert bobbie.fallback_disposition('Not interested')['lead_status'] == 'not_interested'


class _Conv:
    contact, property_address, lead_status = '+13125550111', '12 Oak St', 'wrong_info'


def _scripted_disposition(monkeypatch, **fields):
    payload = {'action': 'end', 'intent': 'wrong_number', 'outcome': 'negative',
               'lead_status': 'not_interested', 'confidence': 0.9, 'conversation_stage': 'complete',
               'next_step': 'close', 'qualification_focus': '',
               'calendar': {'state': 'none', 'should_fetch_availability': False,
                            'requested_time_text': '', 'reason': ''},
               'reason': 'wrong person', **fields}
    monkeypatch.setattr(bobbie, 'completion', lambda *a, **k: {'content': json.dumps(payload)})


def _history(text):
    return [{'direction': 'outbound', 'text': 'Hi, are you the owner of 12 Oak St?'},
            {'direction': 'inbound', 'text': text}]


def test_ai_not_interested_on_wrong_number_is_recorded_as_wrong_info(monkeypatch):
    _scripted_disposition(monkeypatch)
    result = bobbie.analyze_conversation_disposition(_Conv(), _history('wrong number'))
    assert result['action'] == 'end'
    assert result['lead_status'] == 'wrong_info'


def test_ai_wrong_info_is_accepted(monkeypatch):
    _scripted_disposition(monkeypatch, lead_status='wrong_info', outcome='disqualified')
    result = bobbie.analyze_conversation_disposition(_Conv(), _history('Nobody by that name here'))
    assert result['lead_status'] == 'wrong_info'


def test_real_rejection_stays_not_interested(monkeypatch):
    class Conv(_Conv):
        lead_status = 'not_interested'
    _scripted_disposition(monkeypatch)
    result = bobbie.analyze_conversation_disposition(Conv(), _history('Not interested, thanks'))
    assert result['lead_status'] == 'not_interested'


def test_followups_board_reason_for_wrong_info():
    conversation = Conversation(contact='+13125550111', lead_status='wrong_info', handled_by='bobbie',
                                dnc_alert=False, meeting_booked=False)
    assert followups._reason(conversation, None, 10) == ('wrong_info', 'Wrong number/info')


def test_no_followup_is_sent_to_a_wrong_info_lead(session, make_user):
    user = make_user('wronginfo@example.com', role='broker')
    campaign = Campaign(user_id=user.id, name='Wrong info', status='sent')
    session.add(campaign)
    session.commit()
    conversation = Conversation(contact='+13125550121', user_id=user.id, campaign_id=campaign.id,
                                ai_enabled=True, handled_by='bobbie', lead_status='wrong_info',
                                queue_status='idle')
    session.add(conversation)
    session.commit()
    start = datetime(2026, 8, 25, 9, 0)
    followup_scheduler.schedule_initial_followup(session, conversation, start)
    assert followup_scheduler.process_due_conversation(
        session, conversation.id, start + timedelta(hours=48)) == 'cancelled'
    assert session.query(Message).filter(Message.conversation_id == conversation.id).count() == 0
