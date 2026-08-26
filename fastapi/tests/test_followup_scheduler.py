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
                        lambda _conversation, _history, attempt, maximum: f'Follow-up {attempt}/{maximum}')

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
