"""Booking reminders: scheduled 30 and 10 minutes ahead, delivered in-app and by push."""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.core.email import EmailDeliveryError
from app.models import Booking, BookingReminder, Notification, User
from app.reminder import jobs
from app.sms import calendar_service


@pytest.fixture(autouse=True)
def clean_reminders(session):
    yield
    session.query(Notification).delete()
    session.query(BookingReminder).delete()
    session.commit()


class _Pushes(list):
    fail = False


@pytest.fixture
def pushes_list(monkeypatch):
    """Capture reminder pushes; set `fail = True` to simulate no subscription."""
    sent = _Pushes()

    def fake_push(db, user_id, title, body, url=None, conversation_id=None):
        if sent.fail:
            raise RuntimeError(f'No active push subscription found for user {user_id}')
        sent.append({'user_id': user_id, 'title': title, 'body': body, 'url': url})
        return 1

    monkeypatch.setattr(jobs, 'send_push_to_user', fake_push)
    monkeypatch.setattr('app.reminder.notification.send_push_to_user', lambda **kwargs: 1)
    return sent


@pytest.fixture(autouse=True)
def calendar_email_outbox(monkeypatch):
    outbox = []

    def capture(recipient_email, subject, html_body, text_body):
        outbox.append({
            'recipient_email': recipient_email,
            'subject': subject,
            'html_body': html_body,
            'text_body': text_body,
        })

    monkeypatch.setattr(
        'app.core.email.send_email',
        capture,
    )
    return outbox


def _book(session, user_id, starts_in):
    start = datetime.now(timezone.utc).replace(microsecond=0) + starts_in
    return calendar_service.create_booking(
        session, user_id, '+13125550100', 'Marcus Webb', 'Property consultation',
        start, start + timedelta(minutes=30),
    )


def _reminders(session, booking_id):
    session.expire_all()
    return {r.reminder_type: r for r in
            session.query(BookingReminder).filter(BookingReminder.booking_id == booking_id).all()}


def _make_due(session, reminder):
    reminder.scheduled_for = jobs.utc_now_naive() - timedelta(seconds=1)
    session.commit()


def test_booking_schedules_30_and_10_minute_reminders_in_utc(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(hours=2))
    start = session.get(Booking, booking['id']).start_at

    reminders = _reminders(session, booking['id'])
    assert set(reminders) == {'30_minutes', '10_minutes'}
    assert reminders['30_minutes'].scheduled_for == start - timedelta(minutes=30)
    assert reminders['10_minutes'].scheduled_for == start - timedelta(minutes=10)
    assert all(r.status == 'pending' for r in reminders.values())


def test_booking_created_emails_verified_active_owner(
        session, make_user, pushes_list, calendar_email_outbox):
    owner = make_user('owner@x.net', role='broker')
    make_user('other@x.net', role='broker')
    booking = _book(session, owner.id, timedelta(hours=2))

    assert len(calendar_email_outbox) == 1
    email = calendar_email_outbox[0]
    assert email['recipient_email'] == owner.email
    assert email['subject'] == 'Meeting created: Property consultation'
    assert 'Your meeting with Marcus Webb is scheduled for' in email['text_body']
    assert '/meeting/' not in email['text_body']
    assert 'Join meeting' not in email['text_body']
    assert f"booking_id={booking['id']}" in email['text_body']
    assert 'View meeting' in email['text_body']


def test_in_person_calendar_email_uses_google_maps_link(
        session, make_user, pushes_list, calendar_email_outbox):
    owner = make_user('owner@x.net', role='broker')
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)
    calendar_service.create_booking(
        session, owner.id, '+13125550100', 'Marcus Webb', 'Property consultation',
        start, start + timedelta(minutes=30), location_address='10 Main Street, Chicago, IL',
    )

    email = calendar_email_outbox[0]
    assert 'Location: 10 Main Street, Chicago, IL.' in email['text_body']
    assert 'https://www.google.com/maps/search/?api=1&query=10+Main+Street%2C+Chicago%2C+IL' in email['text_body']
    assert 'Open map' in email['text_body']
    assert '/meeting/' not in email['text_body']


def test_reminders_already_past_are_not_created(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(minutes=20))

    assert set(_reminders(session, booking['id'])) == {'10_minutes'}


def test_30_and_10_minute_emails_use_user_localized_meeting_time(
        session, make_user, pushes_list, calendar_email_outbox):
    user = make_user('broker@x.net', role='broker')
    session.query(User).filter(User.id == user.id).update({'timezone': 'Asia/Karachi'})
    session.commit()
    booking = _book(session, user.id, timedelta(hours=2))
    reminder = _reminders(session, booking['id'])['30_minutes']
    _make_due(session, reminder)

    jobs.process_due_booking_reminders()
    _make_due(session, _reminders(session, booking['id'])['10_minutes'])
    jobs.process_due_booking_reminders()

    reminder = _reminders(session, booking['id'])['30_minutes']
    assert reminder.status == 'sent' and reminder.sent_at is not None
    notification = session.query(Notification).filter(Notification.reminder_id == reminder.id).one()
    assert notification.type == 'BOOKING_REMINDER'
    assert notification.title == 'Meeting in 30 minutes'
    assert notification.user_id == user.id
    local = session.get(Booking, booking['id']).start_at.replace(tzinfo=timezone.utc).astimezone(
        jobs.ZoneInfo('Asia/Karachi'))
    assert local.strftime('%I:%M %p PKT') in notification.message
    assert [push['title'] for push in pushes_list] == ['Meeting in 30 minutes', 'Meeting in 10 minutes']
    assert pushes_list[0] == {'user_id': user.id, 'title': 'Meeting in 30 minutes',
                              'body': notification.message, 'url': notification.action_url}
    assert _reminders(session, booking['id'])['10_minutes'].status == 'sent'
    reminder_emails = [email for email in calendar_email_outbox if email['subject'].startswith('Meeting in')]
    assert [email['subject'] for email in reminder_emails] == [
        'Meeting in 30 minutes: Property consultation',
        'Meeting in 10 minutes: Property consultation',
    ]
    expected_time = local.strftime('%b %d, %Y at %I:%M %p PKT')
    assert all(expected_time in email['text_body'] for email in reminder_emails)
    assert all(email['recipient_email'] == user.email for email in reminder_emails)


def test_sent_reminder_is_not_sent_again(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(hours=2))
    _make_due(session, _reminders(session, booking['id'])['30_minutes'])

    jobs.process_due_booking_reminders()
    jobs.process_due_booking_reminders()

    assert len(pushes_list) == 1


def test_without_push_subscription_the_bell_and_email_are_created_once(
        session, make_user, pushes_list, calendar_email_outbox):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(hours=2))
    reminder = _reminders(session, booking['id'])['30_minutes']
    _make_due(session, reminder)
    pushes_list.fail = True

    jobs.process_due_booking_reminders()
    jobs.process_due_booking_reminders()

    assert session.query(Notification).filter(Notification.reminder_id == reminder.id).count() == 1
    assert len([email for email in calendar_email_outbox
                if email['subject'].startswith('Meeting in 30 minutes')]) == 1


def test_scheduler_restart_uses_persisted_notification_for_email_dedupe(tmp_path):
    database_path = tmp_path / 'restart-dedupe.db'
    environment = os.environ.copy()
    environment['POSTGRES_URL'] = f'sqlite:///{database_path}'
    setup = """
from datetime import datetime, timedelta
from app.db import Base, SessionLocal, engine
from app.models import Booking, BookingReminder, User
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    user = User(email='owner@example.com', hashed_password='unused', role='broker',
                is_active=True, is_verified=True)
    db.add(user)
    db.flush()
    booking = Booking(user_id=user.id, phone='+13125550100', name='Marcus Webb',
                      title='Property consultation', start_at=datetime.utcnow() + timedelta(hours=1),
                      end_at=datetime.utcnow() + timedelta(hours=2), join_token='restart-token')
    db.add(booking)
    db.flush()
    db.add(BookingReminder(booking_id=booking.id, user_id=user.id,
                           reminder_type='30_minutes', scheduled_for=datetime.utcnow() - timedelta(seconds=1),
                           status='pending'))
    db.commit()
"""
    # Enter through the application's import path, as the running worker does.
    run_scheduler = """
import app.main  # noqa: F401
from app.reminder import jobs
jobs.send_booking_reminder_email = lambda **kwargs: print('CALENDAR_EMAIL_SENT')
jobs.send_push_to_user = lambda **kwargs: 0
jobs.broadcast_notification_event_sync = lambda **kwargs: None
jobs.process_due_booking_reminders()
"""
    subprocess.run([sys.executable, '-c', setup], check=True, env=environment, text=True)
    first_run = subprocess.run(
        [sys.executable, '-c', run_scheduler], check=True, env=environment,
        text=True, capture_output=True,
    )
    second_run = subprocess.run(
        [sys.executable, '-c', run_scheduler], check=True, env=environment,
        text=True, capture_output=True,
    )

    assert first_run.stdout.count('CALENDAR_EMAIL_SENT') == 1
    assert second_run.stdout.count('CALENDAR_EMAIL_SENT') == 0


def test_inactive_and_unverified_calendar_email_skips_are_logged(
        session, make_user, pushes_list, calendar_email_outbox, caplog):
    inactive = make_user('inactive@x.net', role='broker', is_active=False)
    unverified = make_user('unverified@x.net', role='broker')
    session.query(User).filter(User.id == unverified.id).update({'is_verified': False})
    session.commit()

    inactive_booking = _book(session, inactive.id, timedelta(hours=2))
    _book(session, unverified.id, timedelta(hours=3))
    _make_due(session, _reminders(session, inactive_booking['id'])['30_minutes'])
    jobs.process_due_booking_reminders()

    assert calendar_email_outbox == []
    assert 'event=email.booking_created.skipped' in caplog.text
    assert 'reason="user_inactive"' in caplog.text
    assert 'reason="email_unverified"' in caplog.text
    assert 'event=email.booking_reminder.skipped' in caplog.text


def test_email_failures_do_not_affect_booking_notification_or_push(
        session, make_user, pushes_list, monkeypatch, caplog):
    owner = make_user('owner@x.net', role='broker')
    booking_pushes = []
    monkeypatch.setattr(
        'app.reminder.notification.send_push_to_user',
        lambda **kwargs: booking_pushes.append(kwargs) or 1,
    )
    monkeypatch.setattr(
        'app.core.email.send_email',
        lambda *args, **kwargs: (_ for _ in ()).throw(EmailDeliveryError('SMTP unavailable')),
    )

    booking = _book(session, owner.id, timedelta(hours=2))
    assert session.get(Booking, booking['id']) is not None
    assert session.query(Notification).filter(
        Notification.booking_id == booking['id'],
        Notification.type == 'booking_created',
    ).count() == 1
    assert len(booking_pushes) == 1

    _make_due(session, _reminders(session, booking['id'])['30_minutes'])
    jobs.process_due_booking_reminders()

    reminder = _reminders(session, booking['id'])['30_minutes']
    assert reminder.status == 'sent'
    assert len(pushes_list) == 1
    assert 'event=email.booking_created.failed' in caplog.text
    assert 'event=email.booking_reminder.failed' in caplog.text


def test_reminder_for_a_started_meeting_is_cancelled(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(hours=2))
    reminder = _reminders(session, booking['id'])['30_minutes']
    _make_due(session, reminder)
    row = session.get(Booking, booking['id'])
    row.start_at = jobs.utc_now_naive() - timedelta(minutes=1)
    session.commit()

    jobs.process_due_booking_reminders()

    assert _reminders(session, booking['id'])['30_minutes'].status == 'cancelled'
    assert pushes_list == []
