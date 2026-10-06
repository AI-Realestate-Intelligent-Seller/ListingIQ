from urllib.parse import parse_qs, urlparse

from app.models import FeatureFlag, Invitation, PlatformAuditLog, User
from app.routes.platform_admin import (
    BrokerageOnboardingCreate,
    ScreenAccessUpdate,
    create_organization,
    organization_rows,
    update_user_screen_access,
)
from app.routes.team import ROLE_LABELS, accept_invitation
from app.schemas import InvitationAcceptRequest


def test_customer_roles_cannot_access_platform_admin(client, make_user, auth_header):
    make_user('owner@linchpin.test', role='hob')
    response = client.get('/api/v1/platform-admin/overview', headers=auth_header('owner@linchpin.test'))
    assert response.status_code == 403


def test_platform_admin_can_view_and_suspend_customer(client, make_user, auth_header, session):
    admin = make_user('platform@listingiq.test', role='platform_admin', brokerage_id=None, brokerage_name=None)
    owner = make_user('owner@linchpin.test', role='hob')
    make_user('agent@linchpin.test', role='agent')
    headers = auth_header(admin.email)

    overview = client.get('/api/v1/platform-admin/overview', headers=headers)
    assert overview.status_code == 200
    assert overview.json()['organizations'] == 1
    assert overview.json()['users'] == 2

    response = client.patch('/api/v1/platform-admin/organizations/brokerage-1', headers=headers,
                            json={'is_active': False, 'reason': 'Requested by customer owner'})
    assert response.status_code == 200
    assert all(not user.is_active for user in session.query(type(owner)).filter_by(brokerage_id='brokerage-1').all())
    event = session.query(PlatformAuditLog).one()
    assert event.actor_id == admin.id
    assert event.action == 'organization.suspended'


def test_platform_admin_feature_flag_is_audited(client, make_user, auth_header, session):
    admin = make_user('platform@listingiq.test', role='platform_admin', brokerage_id=None, brokerage_name=None)
    response = client.post('/api/v1/platform-admin/feature-flags', headers=auth_header(admin.email), json={
        'key': 'campaigns.smart_send', 'description': 'Controlled rollout', 'enabled': True,
        'brokerage_id': None,
    })
    assert response.status_code == 200
    assert session.query(FeatureFlag).one().enabled is True
    assert session.query(PlatformAuditLog).one().action == 'feature_flag.updated'


def test_platform_admin_assigns_customer_screen_access(make_user, session, monkeypatch):
    monkeypatch.setattr('conftest.hash_password', lambda _password: 'unused')
    admin = make_user('platform@listingiq.test', role='platform_admin', brokerage_id=None, brokerage_name=None)
    member = make_user('member@linchpin.test', role='agent')
    member = session.get(User, member.id)
    member.assigned_broker_id = admin.id
    session.commit()

    result = update_user_screen_access(
        member.id,
        ScreenAccessUpdate(role='hob'),
        current=session.get(User, admin.id),
        session=session,
    )

    assert result['role'] == 'hob'
    session.refresh(member)
    assert member.role == 'hob'
    assert member.is_head_or_owner is True
    assert member.assigned_broker_id is None
    event = session.query(PlatformAuditLog).one()
    assert event.action == 'user.screen_access_updated'
    assert '"previous_role":"agent"' in event.detail
    assert '"role":"hob"' in event.detail


def test_platform_admin_onboards_brokerage_initial_user(
    make_user, session, sent_emails, monkeypatch
):
    monkeypatch.setattr('conftest.hash_password', lambda _password: 'unused')
    monkeypatch.setattr('app.routes.team.hash_password', lambda _password: 'unused')
    admin = make_user(
        'platform@listingiq.test',
        role='platform_admin',
        brokerage_id=None,
        brokerage_name=None,
    )
    current = session.get(User, admin.id)
    created = create_organization(
        BrokerageOnboardingCreate(
            brokerage_name='Northstar Realty',
            invite_email='broker@northstar.com',
            initial_role='broker',
        ),
        current=current,
        session=session,
    )
    brokerage_id = created['brokerage_id']
    invitation = session.query(Invitation).one()
    assert invitation.role == 'broker'
    assert invitation.brokerage_id == brokerage_id
    assert invitation.token_hash
    assert len(sent_emails) == 1
    assert sent_emails[0]['recipient_email'] == 'broker@northstar.com'
    assert sent_emails[0]['role'] == ROLE_LABELS['broker']

    organizations = organization_rows(session)
    assert organizations == [
        {
            'id': brokerage_id,
            'name': 'Northstar Realty',
            'owner_email': 'broker@northstar.com',
            'user_count': 0,
            'active_user_count': 0,
            'created_at': invitation.created_at,
            'is_active': False,
            'onboarding_status': 'invited',
            'invitation_role': 'broker',
        }
    ]

    token = parse_qs(urlparse(sent_emails[0]['invitation_url']).query)['token'][0]
    accepted = accept_invitation(
        token,
        InvitationAcceptRequest(
            first_name='Nora',
            last_name='Owner',
            password='Password123!',
        ),
        session,
    )
    assert accepted['user'].role == 'broker'
    assert accepted['user'].is_head_or_owner is False
    session.expire_all()
    broker = session.query(User).filter_by(email='broker@northstar.com').one()
    assert broker.brokerage_id == brokerage_id
    assert broker.role == 'broker'
    assert broker.is_head_or_owner is False
    assert broker.is_verified is True
    assert session.query(PlatformAuditLog).one().action == 'organization.onboarding_invited'


def test_suspended_user_cannot_log_in(client, make_user):
    make_user('suspended@linchpin.test', is_active=False)
    response = client.post('/api/v1/auth/login', json={
        'email': 'suspended@linchpin.test', 'password': 'Password123!', 'full_name': None,
    })
    assert response.status_code == 403
