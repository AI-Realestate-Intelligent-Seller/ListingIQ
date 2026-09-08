from app.models import FeatureFlag, PlatformAuditLog


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


def test_suspended_user_cannot_log_in(client, make_user):
    make_user('suspended@linchpin.test', is_active=False)
    response = client.post('/api/v1/auth/login', json={
        'email': 'suspended@linchpin.test', 'password': 'Password123!', 'full_name': None,
    })
    assert response.status_code == 403
