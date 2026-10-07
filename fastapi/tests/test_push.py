import logging

import pytest
from fastapi import HTTPException
from pywebpush import WebPushException

from app.models import PushSubscription
from app.reminder import push
from app.routes import push as push_routes


class PushResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


@pytest.fixture(autouse=True)
def clean_push_subscriptions(session):
    session.query(PushSubscription).delete()
    session.commit()
    yield
    session.query(PushSubscription).delete()
    session.commit()


def subscription(session, user_id: int, endpoint: str, device_id: str):
    row = PushSubscription(
        user_id=user_id,
        device_id=device_id,
        endpoint=endpoint,
        p256dh="test-p256dh",
        auth="test-auth",
        is_active=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.mark.parametrize(
    ("status_code", "response_text", "expected_reason"),
    [
        (
            403,
            "The VAPID credentials do not correspond to the credentials used to create the subscription.",
            "vapid_credentials_mismatch",
        ),
        (404, "Not Found", "endpoint_not_found"),
        (410, "Gone", "endpoint_gone"),
    ],
)
def test_permanent_failure_disables_only_stale_subscription_and_valid_push_continues(
    session, make_user, monkeypatch, caplog, status_code, response_text, expected_reason
):
    user = make_user("push-user@linchpinglobal.net", role="hob")
    stale = subscription(session, user.id, "https://push.example/stale", "stale-device")
    valid = subscription(session, user.id, "https://push.example/valid", "valid-device")
    attempted = []

    def fake_send(subscription, **_):
        attempted.append(subscription["endpoint"])
        if subscription["endpoint"].endswith("/stale"):
            raise WebPushException(
                "Push failed",
                response=PushResponse(status_code, response_text),
            )
        return PushResponse(201, "Created")

    monkeypatch.setattr(push, "send_web_push", fake_send)
    with caplog.at_level(logging.INFO, logger="app.reminder.push"):
        sent_count = push.send_push_to_user(
            session,
            user.id,
            "Customer replied",
            "Open the conversation",
        )

    session.refresh(stale)
    session.refresh(valid)
    assert attempted == ["https://push.example/stale", "https://push.example/valid"]
    assert sent_count == 1
    assert stale.is_active is False
    assert valid.is_active is True
    disabled_log = next(
        record.getMessage()
        for record in caplog.records
        if "event=push.subscription.disabled" in record.getMessage()
    )
    assert f"subscription_id={stale.id}" in disabled_log
    assert f'reason="{expected_reason}"' in disabled_log
    assert f"status_code={status_code}" in disabled_log


def test_transient_failure_does_not_disable_subscription_or_block_others(
    session, make_user, monkeypatch
):
    user = make_user("push-transient@linchpinglobal.net", role="hob")
    transient = subscription(
        session, user.id, "https://push.example/transient", "transient-device"
    )
    valid = subscription(
        session, user.id, "https://push.example/valid-2", "valid-device"
    )

    def fake_send(subscription, **_):
        if subscription["endpoint"].endswith("/transient"):
            raise WebPushException(
                "Temporary push failure",
                response=PushResponse(503, "Unavailable"),
            )
        return PushResponse(201, "Created")

    monkeypatch.setattr(push, "send_web_push", fake_send)
    sent_count = push.send_push_to_user(
        session,
        user.id,
        "Customer replied",
        "Open the conversation",
    )

    session.refresh(transient)
    session.refresh(valid)
    assert sent_count == 1
    assert transient.is_active is True
    assert valid.is_active is True


def test_existing_browser_endpoint_is_reactivated_when_device_id_changes(
    session, make_user, caplog
):
    user = make_user("returning-user@linchpinglobal.net", role="hob")
    existing = subscription(
        session,
        user.id,
        "https://push.example/existing-browser-endpoint",
        "old-device-id",
    )
    existing.is_active = False
    session.commit()

    with caplog.at_level(logging.INFO, logger="app.routes.push"):
        result = push_routes.subscribe_to_push(
            {
                "user_id": user.id,
                "device_id": "new-device-id",
                "subscription": {
                    "endpoint": existing.endpoint,
                    "keys": {"p256dh": "new-p256dh", "auth": "new-auth"},
                },
            },
            user,
            session,
        )

    session.refresh(existing)
    assert result["subscription_id"] == existing.id
    assert existing.is_active is True
    assert existing.device_id == "new-device-id"
    assert existing.p256dh == "new-p256dh"
    assert existing.auth == "new-auth"
    refreshed_log = next(
        record.getMessage()
        for record in caplog.records
        if "event=push.subscription.refreshed" in record.getMessage()
    )
    assert "reactivated=true" in refreshed_log
    assert 'matched_by="endpoint"' in refreshed_log


def test_existing_device_is_reactivated_and_updated_with_new_endpoint(
    session, make_user
):
    user = make_user("resubscribed-user@linchpinglobal.net", role="agent")
    existing = subscription(
        session,
        user.id,
        "https://push.example/old-endpoint",
        "stable-device-id",
    )
    existing.is_active = False
    session.commit()

    result = push_routes.subscribe_to_push(
        {
            "user_id": user.id,
            "device_id": existing.device_id,
            "subscription": {
                "endpoint": "https://push.example/new-endpoint",
                "keys": {"p256dh": "replacement-key", "auth": "replacement-auth"},
            },
        },
        user,
        session,
    )

    session.refresh(existing)
    assert result["subscription_id"] == existing.id
    assert existing.is_active is True
    assert existing.endpoint == "https://push.example/new-endpoint"


def test_subscription_payload_cannot_register_for_another_user(session, make_user):
    user = make_user("push-owner@linchpinglobal.net", role="hob")
    other = make_user("different-user@linchpinglobal.net", role="agent")

    with pytest.raises(HTTPException) as error:
        push_routes.subscribe_to_push(
            {
                "user_id": other.id,
                "device_id": "device-id",
                "subscription": {
                    "endpoint": "https://push.example/forged",
                    "keys": {"p256dh": "key", "auth": "auth"},
                },
            },
            user,
            session,
        )

    assert error.value.status_code == 403
    assert session.query(PushSubscription).count() == 0
