from datetime import datetime, timedelta

from app.core.config import settings
from app.models import Campaign, Conversation, Message
from app.sms import followup_scheduler


def _thread(session, make_user):
    user = make_user('followups@example.com', role='broker')
    campaign = Campaign(user_id=user.id, name='Silent owners', status='sent')
    session.add(campaign)
    session.commit()
    conversation = Conversation(
        contact='+13125550120', user_id=user.id, name='Ana',
        property_address='950 Edgar Dr Apt 4, Charleston, IL 61920',
        campaign_id=campaign.id, ai_enabled=True, handled_by='bobbie',
        lead_status='processing', queue_status='idle',
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def test_three_followups_then_marks_no_response_dead(session, make_user, monkeypatch):
    conversation = _thread(session, make_user)
    start = datetime(2026, 8, 25, 9, 0)
    sms = settings['sms']
    monkeypatch.setitem(sms, 'followup_enabled', True)
    monkeypatch.setitem(sms, 'followup_first_delay_hours', 48)
    monkeypatch.setitem(sms, 'followup_gap_hours', 72)
    monkeypatch.setitem(sms, 'followup_max_attempts', 3)
    monkeypatch.setattr(followup_scheduler.bobbie, 'generate_no_response_followup',
                        lambda _conversation, _history, attempt, maximum, agent_context=None:
                        f'Follow-up {attempt}/{maximum}' if agent_context is not None else None)

    def fake_send(db, thread, text, event_type, suppress_auto_reply=False):
        assert suppress_auto_reply is True
        message = Message(conversation_id=thread.id, direction='outbound', text=text,
                          event_type=event_type, created_at=start)
        db.add(message)
        db.commit()
        return message

    monkeypatch.setattr(followup_scheduler, 'send_and_store_message', fake_send)
    followup_scheduler.schedule_initial_followup(session, conversation, start)
    assert conversation.next_followup_at == start + timedelta(days=2)

    for attempt in range(1, 4):
        due = start + timedelta(days=2 + (attempt - 1) * 3)
        assert followup_scheduler.process_due_conversation(session, conversation.id, due) == 'sent'
        session.refresh(conversation)
        assert conversation.followup_attempt_count == attempt

    assert conversation.final_followup_sent_at == start + timedelta(days=8)
    assert followup_scheduler.process_due_conversation(
        session, conversation.id, start + timedelta(days=11)) == 'dead'
    session.refresh(conversation)
    assert conversation.lead_status == 'no_response'
    assert conversation.queue_status == 'dead'
    assert conversation.ai_enabled is False
    assert conversation.dead_at == start + timedelta(days=11)


def test_owner_reply_cancels_current_cadence(session, make_user, monkeypatch):
    conversation = _thread(session, make_user)
    start = datetime(2026, 8, 25, 9, 0)
    monkeypatch.setitem(settings['sms'], 'followup_enabled', True)
    followup_scheduler.schedule_initial_followup(session, conversation, start)
    session.add(Message(conversation_id=conversation.id, direction='inbound', text='Yes, tell me more',
                        created_at=start + timedelta(hours=1)))
    session.commit()
    followup_scheduler.cancel_followup_cadence(session, conversation, owner_replied=True)
    assert conversation.next_followup_at is None
    assert followup_scheduler.process_due_conversation(
        session, conversation.id, start + timedelta(days=2)) == 'cancelled'


def test_human_takeover_cancels_due_followup(session, make_user):
    conversation = _thread(session, make_user)
    followup_scheduler.schedule_initial_followup(session, conversation)
    from app.sms.service import hand_to_broker
    hand_to_broker(session, conversation)
    assert conversation.handled_by == 'broker'
    assert conversation.ai_enabled is False
    assert conversation.next_followup_at is None


def test_default_cadence_is_one_followup_then_dead_after_72_hours(session, make_user, monkeypatch):
    """Default: one follow-up 48h after outreach, then dead 72h after that follow-up."""
    import os
    if 'BOBBIE_FOLLOWUP_MAX_ATTEMPTS' not in os.environ:
        assert settings['sms']['followup_max_attempts'] == 1

    conversation = _thread(session, make_user)
    start = datetime(2026, 8, 25, 9, 0)
    sms = settings['sms']
    monkeypatch.setitem(sms, 'followup_enabled', True)
    monkeypatch.setitem(sms, 'followup_first_delay_hours', 48)
    monkeypatch.setitem(sms, 'followup_gap_hours', 72)
    monkeypatch.setitem(sms, 'followup_max_attempts', 1)
    calls = []
    monkeypatch.setattr(followup_scheduler.bobbie, 'generate_no_response_followup',
                        lambda _c, _h, attempt, maximum, agent_context=None:
                        calls.append((attempt, maximum)) or f'Follow-up {attempt}/{maximum}')
    sent = []

    def fake_send(db, thread, text, event_type, suppress_auto_reply=False):
        sent.append((text, event_type))
        message = Message(conversation_id=thread.id, direction='outbound', text=text,
                          event_type=event_type, created_at=start)
        db.add(message)
        db.commit()
        return message

    monkeypatch.setattr(followup_scheduler, 'send_and_store_message', fake_send)
    followup_scheduler.schedule_initial_followup(session, conversation, start)
    assert conversation.next_followup_at == start + timedelta(hours=48)

    followup_at = start + timedelta(hours=48)
    assert followup_scheduler.process_due_conversation(session, conversation.id, followup_at) == 'sent'
    session.refresh(conversation)
    assert calls == [(1, 1)]
    assert sent == [('Follow-up 1/1', 'ai.followup.1')]
    assert conversation.followup_attempt_count == 1
    assert conversation.final_followup_sent_at == followup_at
    assert conversation.next_followup_at == followup_at + timedelta(hours=72)
    assert conversation.dead_at is None

    # Not due yet: the scheduler only picks rows whose next_followup_at has passed.
    early = followup_at + timedelta(hours=71)
    assert followup_scheduler.run_due_followups(early) == {'sent': 0, 'dead': 0, 'cancelled': 0, 'retry': 0}
    session.refresh(conversation)
    assert conversation.dead_at is None

    dead_at = followup_at + timedelta(hours=72)
    assert followup_scheduler.process_due_conversation(session, conversation.id, dead_at) == 'dead'
    session.refresh(conversation)
    assert len(sent) == 1  # no second follow-up was sent
    assert conversation.lead_status == 'no_response'
    assert conversation.queue_status == 'dead'
    assert conversation.ai_enabled is False
    assert conversation.dead_at == dead_at
    assert conversation.next_followup_at is None


def test_reply_after_single_followup_prevents_dead(session, make_user, monkeypatch):
    conversation = _thread(session, make_user)
    start = datetime(2026, 8, 25, 9, 0)
    sms = settings['sms']
    monkeypatch.setitem(sms, 'followup_enabled', True)
    monkeypatch.setitem(sms, 'followup_max_attempts', 1)
    monkeypatch.setattr(followup_scheduler.bobbie, 'generate_no_response_followup',
                        lambda *_a, **_k: 'Follow-up')
    monkeypatch.setattr(followup_scheduler, 'send_and_store_message', lambda *a, **k: None)
    followup_scheduler.schedule_initial_followup(session, conversation, start)
    assert followup_scheduler.process_due_conversation(
        session, conversation.id, start + timedelta(hours=48)) == 'sent'
    session.add(Message(conversation_id=conversation.id, direction='inbound', text='Maybe',
                        created_at=start + timedelta(hours=60)))
    session.commit()
    followup_scheduler.cancel_followup_cadence(session, conversation, owner_replied=True)
    assert conversation.next_followup_at is None
    assert followup_scheduler.process_due_conversation(
        session, conversation.id, start + timedelta(hours=120)) == 'cancelled'
    session.refresh(conversation)
    assert conversation.dead_at is None
    assert conversation.queue_status != 'dead'


def test_single_followup_uses_first_followup_wording():
    from types import SimpleNamespace
    from app.sms.bobbie import generate_no_response_followup
    thread = SimpleNamespace(property_address='12 Oak St', contact='+13125550120')
    assert generate_no_response_followup(thread, [], 1, 1).startswith('Just circling back')
    # The three-step cadence is unchanged.
    assert generate_no_response_followup(thread, [], 1, 3).startswith('Just circling back')
    assert generate_no_response_followup(thread, [], 2, 3).startswith('I wanted to follow up once more')
    assert generate_no_response_followup(thread, [], 3, 3).startswith('Just closing the loop')
