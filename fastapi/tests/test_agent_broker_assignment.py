import pytest
from fastapi import HTTPException

from app.models import Invitation, User
from app.routes.team import accept_invitation, create_invitation
from app.schemas import InvitationAcceptRequest, InvitationCreate


def test_agent_invitation_requires_broker(make_user, session):
    hob = make_user('hob@linchpinglobal.net', role='hob')

    with pytest.raises(HTTPException) as error:
        create_invitation(
            InvitationCreate(email='agent@linchpinglobal.net', role='agent'),
            hob,
            session,
        )

    assert error.value.status_code == 400
    assert error.value.detail == 'Select an Area Broker for this agent.'


def test_agent_inherits_assigned_broker_when_invitation_is_accepted(
    make_user, session, sent_emails
):
    hob = make_user('hob@linchpinglobal.net', role='hob')
    broker = make_user('broker@linchpinglobal.net', role='broker')

    create_invitation(
        InvitationCreate(
            email='agent@linchpinglobal.net',
            role='agent',
            broker_id=broker.id,
        ),
        hob,
        session,
    )
    token = sent_emails[-1]['invitation_url'].split('token=')[1]

    accept_invitation(
        token,
        InvitationAcceptRequest(
            first_name='Assigned',
            last_name='Agent',
            password='Password123!',
        ),
        session,
    )

    invitation = session.query(Invitation).one()
    agent = session.query(User).filter(User.email == 'agent@linchpinglobal.net').one()
    assert invitation.assigned_broker_id == broker.id
    assert agent.assigned_broker_id == broker.id


def test_hob_cannot_assign_agent_to_another_brokerage(make_user, session):
    hob = make_user('hob@linchpinglobal.net', role='hob')
    outside_broker = make_user(
        'broker@anotherbrokerage.net', role='broker', brokerage_id='brokerage-2'
    )

    with pytest.raises(HTTPException) as error:
        create_invitation(
            InvitationCreate(
                email='agent@linchpinglobal.net',
                role='agent',
                broker_id=outside_broker.id,
            ),
            hob,
            session,
        )

    assert error.value.status_code == 400
    assert error.value.detail == 'Select an active Area Broker from your brokerage.'
