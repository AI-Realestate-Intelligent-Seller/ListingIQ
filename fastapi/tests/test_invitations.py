"""Tests for the HOB team invitation flow."""

from datetime import datetime, timedelta

import pytest

from app import db
from app.models import Invitation, User
from app.routes.team import hash_token

HOB_EMAIL = 'hob@linchpinglobal.net'
INVITE_URL = '/api/v1/team/invitations'


def token_from(outbox):
    """Extract the raw token from the captured invitation email."""
    assert outbox, 'no invitation email was sent'
    return outbox[-1]['invitation_url'].split('token=')[1]


def invite(client, headers, email, role='broker'):
    return client.post(INVITE_URL, json={'email': email, 'role': role}, headers=headers)


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------

def test_hob_can_invite(client, make_user, auth_header, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    response = invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    assert response.status_code == 200
    assert response.json() == {'message': 'Invitation sent successfully.'}
    # The raw token must never be part of the API response.
    assert 'token' not in response.text
    assert len(sent_emails) == 1


@pytest.mark.parametrize('role', ['agent', 'broker'])
def test_non_hob_cannot_invite(client, make_user, auth_header, role):
    email = f'{role}@linchpinglobal.net'
    make_user(email, role=role)
    response = invite(client, auth_header(email), 'john@linchpinglobal.net')
    assert response.status_code == 403


def test_unauthenticated_cannot_invite(client):
    response = client.post(INVITE_URL, json={'email': 'john@linchpinglobal.net', 'role': 'broker'})
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

@pytest.mark.parametrize('role', ['broker', 'agent'])
def test_allowed_roles(client, make_user, auth_header, session, role):
    make_user(HOB_EMAIL, role='hob')
    assert invite(client, auth_header(HOB_EMAIL), f'{role}.invitee@linchpinglobal.net', role).status_code == 200
    stored = session.query(Invitation).filter(Invitation.role == role).one()
    assert stored.status == 'pending'


@pytest.mark.parametrize('role', ['hob', 'admin', 'developer', 'HOB', ''])
def test_forbidden_roles_are_rejected(client, make_user, auth_header, role):
    make_user(HOB_EMAIL, role='hob')
    response = invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net', role)
    assert response.status_code == 422


def test_role_is_case_insensitive(client, make_user, auth_header, session):
    make_user(HOB_EMAIL, role='hob')
    assert invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net', 'Broker').status_code == 200
    assert session.query(Invitation).one().role == 'broker'


# --------------------------------------------------------------------------
# Brokerage domain
# --------------------------------------------------------------------------

def test_matching_domain_is_allowed(client, make_user, auth_header):
    make_user(HOB_EMAIL, role='hob')
    assert invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net').status_code == 200


@pytest.mark.parametrize('email', [
    'john@gmail.com',
    'john@otherdomain.com',
    'john@sub.linchpinglobal.net',
    'john@evil.net',
])
def test_foreign_domains_are_rejected(client, make_user, auth_header, email, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    response = invite(client, auth_header(HOB_EMAIL), email)
    assert response.status_code == 400
    assert 'brokerage domain' in response.json()['detail']
    assert sent_emails == []


def test_email_is_normalized(client, make_user, auth_header, session):
    make_user(HOB_EMAIL, role='hob')
    assert invite(client, auth_header(HOB_EMAIL), '  John@LinchpinGlobal.NET  ').status_code == 200
    assert session.query(Invitation).one().email == 'john@linchpinglobal.net'


# --------------------------------------------------------------------------
# Invalid invitations
# --------------------------------------------------------------------------

def test_existing_member_of_same_brokerage(client, make_user, auth_header):
    make_user(HOB_EMAIL, role='hob')
    make_user('john@linchpinglobal.net', role='agent', brokerage_id='brokerage-1')
    response = invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    assert response.status_code == 400
    assert response.json()['detail'] == 'This user is already a member of your brokerage.'


def test_existing_user_of_another_brokerage_is_not_moved(client, make_user, auth_header, session):
    make_user(HOB_EMAIL, role='hob')
    make_user('john@linchpinglobal.net', role='agent', brokerage_id='brokerage-2')
    response = invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    assert response.status_code == 400
    assert session.query(User).filter(User.email == 'john@linchpinglobal.net').one().brokerage_id == 'brokerage-2'


def test_hob_cannot_invite_self(client, make_user, auth_header):
    make_user(HOB_EMAIL, role='hob')
    response = invite(client, auth_header(HOB_EMAIL), HOB_EMAIL)
    assert response.status_code == 400


def test_resend_is_throttled(client, make_user, auth_header):
    make_user(HOB_EMAIL, role='hob')
    assert invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net').status_code == 200
    response = invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    assert response.status_code == 429


def test_resend_after_cooldown_supersedes_previous_invitation(client, make_user, auth_header, session, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    assert invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net').status_code == 200
    first_token = token_from(sent_emails)

    # Age the first invitation past the resend cooldown.
    first = session.query(Invitation).one()
    first.created_at = datetime.utcnow() - timedelta(minutes=5)
    session.commit()

    assert invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net', 'agent').status_code == 200
    second_token = token_from(sent_emails)
    assert first_token != second_token

    assert client.get(f'{INVITE_URL}/{first_token}').json()['valid'] is False
    assert client.get(f'{INVITE_URL}/{second_token}').json()['valid'] is True


def test_email_failure_does_not_persist_invitation(client, make_user, auth_header, session, monkeypatch):
    from app.core.email import EmailDeliveryError

    make_user(HOB_EMAIL, role='hob')

    def boom(**kwargs):
        raise EmailDeliveryError('smtp down')

    monkeypatch.setattr('app.routes.team.send_invitation_email', boom)
    response = invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    assert response.status_code == 502
    assert session.query(Invitation).count() == 0


# --------------------------------------------------------------------------
# Validation endpoint
# --------------------------------------------------------------------------

def test_validate_returns_safe_details(client, make_user, auth_header, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net', 'broker')
    body = client.get(f'{INVITE_URL}/{token_from(sent_emails)}').json()

    assert body['valid'] is True
    assert body['email'] == 'john@linchpinglobal.net'
    assert body['role'] == 'broker'
    assert body['role_label'] == 'Area Broker'
    assert body['brokerage_name'] == 'Linchpin Global'
    assert body['expires_at']
    # No internal identifiers or hashes leak to the invitee.
    assert 'token_hash' not in body
    assert 'brokerage_id' not in body
    assert 'invited_by' not in body


@pytest.mark.parametrize('token', ['not-a-real-token', 'x' * 43])
def test_validate_rejects_unknown_token(client, token):
    body = client.get(f'{INVITE_URL}/{token}').json()
    assert body == {
        'valid': False,
        'email': None,
        'role': None,
        'role_label': None,
        'brokerage_name': None,
        'expires_at': None,
        'message': 'This invitation is invalid or has expired.',
    }


def test_validate_marks_lapsed_invitation_expired(client, make_user, auth_header, session, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    token = token_from(sent_emails)

    invitation = session.query(Invitation).one()
    invitation.expires_at = datetime.utcnow() - timedelta(hours=1)
    session.commit()

    assert client.get(f'{INVITE_URL}/{token}').json()['valid'] is False
    session.expire_all()
    assert session.query(Invitation).one().status == 'expired'


# --------------------------------------------------------------------------
# Acceptance
# --------------------------------------------------------------------------

REGISTRATION = {'first_name': 'John', 'last_name': 'Doe', 'password': 'Password123!'}


def accept(client, token, **overrides):
    payload = {**REGISTRATION, **overrides}
    return client.post(f'{INVITE_URL}/{token}/accept', json=payload)


def test_accept_creates_user_with_invited_role_and_brokerage(client, make_user, auth_header, session, sent_emails):
    hob = make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net', 'broker')
    token = token_from(sent_emails)

    response = accept(client, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['user']['role'] == 'broker'
    assert body['user']['email'] == 'john@linchpinglobal.net'
    assert body['access_token']

    invitation = session.query(Invitation).one()
    created = session.query(User).filter(User.email == 'john@linchpinglobal.net').one()
    assert created.brokerage_id == invitation.brokerage_id == hob.brokerage_id
    assert created.brokerage_name == 'Linchpin Global'
    assert created.role == 'broker'
    assert created.is_head_or_owner is False
    assert invitation.status == 'accepted'
    assert invitation.accepted_at is not None


def test_accepted_user_can_log_in(client, make_user, auth_header, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'sara@linchpinglobal.net', 'agent')
    accept(client, token_from(sent_emails))

    login = client.post(
        '/api/v1/auth/login',
        json={'email': 'sara@linchpinglobal.net', 'password': REGISTRATION['password'], 'full_name': None},
    )
    assert login.status_code == 200
    assert login.json()['user']['role'] == 'agent'


def test_accept_ignores_client_supplied_role_and_brokerage(client, make_user, auth_header, session, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net', 'agent')
    token = token_from(sent_emails)

    response = client.post(
        f'{INVITE_URL}/{token}/accept',
        json={
            **REGISTRATION,
            'role': 'hob',
            'brokerage_id': '999',
            'invited_by': 999,
            'email': 'attacker@linchpinglobal.net',
            'is_head_or_owner': True,
            'is_verified': True,
        },
    )
    assert response.status_code == 200

    created = session.query(User).filter(User.role != 'hob').one()
    assert created.email == 'john@linchpinglobal.net'
    assert created.role == 'agent'
    assert created.brokerage_id == 'brokerage-1'
    assert created.is_head_or_owner is False
    assert session.query(User).filter(User.email == 'attacker@linchpinglobal.net').count() == 0


def test_accept_rejects_invalid_token(client):
    response = accept(client, 'totally-invalid')
    assert response.status_code == 400
    assert response.json()['detail'] == 'This invitation is invalid or has expired.'


def test_accept_rejects_expired_token(client, make_user, auth_header, session, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    token = token_from(sent_emails)

    invitation = session.query(Invitation).one()
    invitation.expires_at = datetime.utcnow() - timedelta(seconds=1)
    session.commit()

    assert accept(client, token).status_code == 400
    assert session.query(User).filter(User.email == 'john@linchpinglobal.net').count() == 0


def test_invitation_cannot_be_reused(client, make_user, auth_header, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    token = token_from(sent_emails)

    assert accept(client, token).status_code == 200
    second = accept(client, token)
    assert second.status_code == 400
    assert second.json()['detail'] in (
        'This invitation is invalid or has expired.',
        'An account already exists for this email address. Please log in instead.',
    )


def test_accept_requires_a_strong_enough_password(client, make_user, auth_header, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    assert accept(client, token_from(sent_emails), password='short').status_code == 422


# --------------------------------------------------------------------------
# Token storage
# --------------------------------------------------------------------------

def test_raw_token_is_never_stored(client, make_user, auth_header, session, sent_emails):
    make_user(HOB_EMAIL, role='hob')
    invite(client, auth_header(HOB_EMAIL), 'john@linchpinglobal.net')
    token = token_from(sent_emails)

    invitation = session.query(Invitation).one()
    assert invitation.token_hash != token
    assert invitation.token_hash == hash_token(token)
    assert len(invitation.token_hash) == 64

    raw_rows = db.engine.connect().exec_driver_sql('select * from invitations').fetchall()
    assert token not in str(raw_rows)
