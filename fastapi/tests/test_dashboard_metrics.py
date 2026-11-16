"""Dashboard communication card totals and their persisted-state definitions."""

from app.dashboard_metrics import communication_totals
from app.models import Campaign, Conversation, LoftyExport, Message


def test_communication_totals_are_owner_scoped_and_exclude_unsent_attempts(make_user, session):
    broker = make_user('totals-broker@example.test', role='broker')
    other = make_user('totals-other@example.test', role='broker')
    session.add_all([
        Campaign(user_id=broker.id, name='Draft', status='draft'),
        Campaign(user_id=broker.id, name='Sent', status='sent'),
        Campaign(user_id=other.id, name='Other', status='sent'),
    ])

    positive = Conversation(
        user_id=broker.id,
        contact='+13125550101',
        lead_status='interested',
    )
    opt_out = Conversation(
        user_id=broker.id,
        contact='+13125550102',
        lead_status='dnc',
        dnc_alert=True,
    )
    booked = Conversation(
        user_id=broker.id,
        contact='+13125550103',
        lead_status='ready_to_sell',
        meeting_booked=True,
    )
    realtor = Conversation(
        user_id=broker.id,
        contact='+13125550105',
        lead_status='realtor',
        dnc_alert=True,
    )
    other_thread = Conversation(
        user_id=other.id,
        contact='+13125550104',
        lead_status='interested',
    )
    session.add_all([positive, opt_out, booked, realtor, other_thread])
    session.flush()

    session.add_all([
        Message(conversation_id=positive.id, direction='inbound', text='Yes'),
        Message(conversation_id=positive.id, direction='inbound', text='Tomorrow works'),
        Message(conversation_id=positive.id, direction='outbound', text='Great', status='queued'),
        Message(conversation_id=positive.id, direction='outbound', text='Follow-up', status='queued'),
        Message(conversation_id=positive.id, direction='outbound', text='Retry', status='submission_retry'),
        Message(conversation_id=opt_out.id, direction='inbound', text='STOP'),
        Message(conversation_id=booked.id, direction='inbound', text='Confirmed'),
        Message(conversation_id=realtor.id, direction='inbound', text='I am a realtor'),
        Message(conversation_id=other_thread.id, direction='inbound', text='Count me elsewhere'),
        Message(conversation_id=other_thread.id, direction='outbound', text='Elsewhere', status='queued'),
        LoftyExport(conversation_id=positive.id, status='completed'),
        LoftyExport(conversation_id=other_thread.id, status='completed'),
    ])
    session.commit()

    assert communication_totals(session, [broker.id]) == {
        'total_campaigns': 2,
        'total_leads_outreached': 1,
        'total_leads_replied': 4,
        'total_leads_opted_out': 1,
        'total_positive_leads': 2,
        'total_other_reply_leads': 1,
        'total_sent_to_lofty': 1,
        'total_appointments_to_confirm': 1,
    }
