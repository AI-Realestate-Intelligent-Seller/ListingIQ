"""Booking reminders: scheduled 30 and 10 minutes ahead, delivered in-app and by push."""

from datetime import datetime, timedelta, timezone

import pytest

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


def test_reminders_already_past_are_not_created(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(minutes=20))

    assert set(_reminders(session, booking['id'])) == {'10_minutes'}


def test_due_reminder_notifies_in_app_and_by_push_in_user_timezone(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    session.query(User).filter(User.id == user.id).update({'timezone': 'Asia/Karachi'})
    session.commit()
    booking = _book(session, user.id, timedelta(hours=2))
    reminder = _reminders(session, booking['id'])['30_minutes']
    _make_due(session, reminder)

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
    assert pushes_list == [{'user_id': user.id, 'title': 'Meeting in 30 minutes',
                            'body': notification.message, 'url': notification.action_url}]
    assert _reminders(session, booking['id'])['10_minutes'].status == 'pending'


def test_sent_reminder_is_not_sent_again(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(hours=2))
    _make_due(session, _reminders(session, booking['id'])['30_minutes'])

    jobs.process_due_booking_reminders()
    jobs.process_due_booking_reminders()

    assert len(pushes_list) == 1


def test_without_push_subscription_the_bell_still_gets_one_notification(session, make_user, pushes_list):
    user = make_user('broker@x.net', role='broker')
    booking = _book(session, user.id, timedelta(hours=2))
    reminder = _reminders(session, booking['id'])['30_minutes']
    _make_due(session, reminder)
    pushes_list.fail = True

    jobs.process_due_booking_reminders()
    jobs.process_due_booking_reminders()

    assert session.query(Notification).filter(Notification.reminder_id == reminder.id).count() == 1


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
