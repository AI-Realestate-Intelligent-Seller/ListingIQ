"""Account notification emails also go, as a hidden copy, to the team inbox."""

import pytest

from app.core import email
from app.core.config import settings

TEAM = 'linchpinglobal.dev@gmail.com'


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return False

    def login(self, user, password):
        pass

    def sendmail(self, sender, recipients, raw):
        FakeSMTP.sent.append({'recipients': recipients, 'raw': raw})


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(email.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setitem(settings['smtp'], 'host', 'smtp.test')
    monkeypatch.setitem(settings['smtp'], 'from_address', 'noreply@listingiq.test')
    monkeypatch.setitem(settings['smtp'], 'use_ssl', False)
    monkeypatch.setitem(settings['smtp'], 'notification_copy_to', [TEAM])
    return FakeSMTP.sent


def test_default_copy_address(monkeypatch):
    import os
    if 'NOTIFICATION_COPY_EMAILS' not in os.environ:
        from app.core.config import load_config
        assert load_config()['smtp']['notification_copy_to'] == [TEAM]


NOTIFICATIONS = [
    lambda: email.send_reply_notification_email('agent@example.com', 'Ana', 'Spring', '12 Oak St', 'Hi', 7, True),
    lambda: email.send_lead_assigned_email('agent@example.com', 'Alex', 3, 'Ana', '12 Oak St'),
    lambda: email.send_booking_created_email('agent@example.com', 'Ana', 'Visit', 'Mon 9am', 4),
    lambda: email.send_booking_reminder_email('agent@example.com', 'Ana', 'Visit', 'Mon 9am', 4, 30),
    lambda: email.send_new_brokerage_leads_email('agent@example.com', 'Northstar', 2),
]


@pytest.mark.parametrize('send', NOTIFICATIONS)
def test_every_notification_is_copied_to_the_team_inbox(smtp, send):
    send()
    assert len(smtp) == 1
    assert smtp[0]['recipients'] == ['agent@example.com', TEAM]
    # Hidden copy: the account owner does not see the team address.
    assert TEAM not in smtp[0]['raw']
    assert 'To: agent@example.com' in smtp[0]['raw']


def test_password_reset_and_invitations_are_not_copied(smtp):
    email.send_password_reset_email('agent@example.com', 'https://app.test/reset?token=secret')
    email.send_invitation_email('new@example.com', 'Northstar', 'agent', 'https://app.test/join?token=x', 'Mon')
    assert [item['recipients'] for item in smtp] == [['agent@example.com'], ['new@example.com']]


def test_no_duplicate_when_the_account_is_the_team_inbox(smtp):
    email.send_new_brokerage_leads_email(TEAM.upper(), 'Northstar', 1)
    assert smtp[0]['recipients'] == [TEAM.upper()]


def test_copy_can_be_turned_off(smtp, monkeypatch):
    monkeypatch.setitem(settings['smtp'], 'notification_copy_to', [])
    email.send_new_brokerage_leads_email('agent@example.com', 'Northstar', 1)
    assert smtp[0]['recipients'] == ['agent@example.com']
