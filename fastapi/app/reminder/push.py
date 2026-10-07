import json
import logging
import os

from pywebpush import WebPushException, webpush

from ..logger import get_logger, log_event
from ..models import PushSubscription
from .actions import dashboard_url, notification_action

logger = get_logger(__name__)


def _permanent_subscription_error(
    exc: WebPushException,
) -> tuple[bool, str, int | None]:
    response = exc.response
    status_code = (
        getattr(response, "status_code", None) if response is not None else None
    )
    response_text = (
        (getattr(response, "text", "") or "").lower() if response is not None else ""
    )

    if status_code == 404:
        return True, "endpoint_not_found", status_code
    if status_code == 410:
        return True, "endpoint_gone", status_code
    if (
        status_code == 403
        and "vapid" in response_text
        and ("do not correspond" in response_text or "mismatch" in response_text)
    ):
        return True, "vapid_credentials_mismatch", status_code
    return False, "delivery_failed", status_code


def send_web_push(
    subscription: dict,
    title: str,
    body: str,
    url: str = "/calendar",
    conversation_id: int | None = None,
):
    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
    vapid_email = os.getenv("VAPID_EMAIL")

    if not vapid_private_key:
        raise RuntimeError("VAPID_PRIVATE_KEY is not configured")

    if not vapid_email:
        raise RuntimeError("VAPID_EMAIL is not configured")

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": dashboard_url(
                notification_action(action_url=url, conversation_id=conversation_id)
            ),
            "action": notification_action(
                action_url=url, conversation_id=conversation_id
            ),
            "conversation_id": conversation_id,
        }
    )

    print(
        "[WEB PUSH ATTEMPT]",
        {
            "title": title,
            "body": body,
            "url": url or "/calendar",
            "endpoint": subscription.get("endpoint", "")[:50],
        },
    )

    try:
        response = webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=vapid_private_key,
            vapid_claims={
                "sub": vapid_email,
            },
        )

        print(
            "[WEB PUSH SUCCESS]",
            response.status_code if response is not None else "no-response-object",
        )

        return response

    except WebPushException as exc:
        print(
            "[WEB PUSH ERROR]",
            repr(exc),
        )

        if exc.response is not None:
            print(
                "[WEB PUSH RESPONSE]",
                exc.response.status_code,
                exc.response.text,
            )

        raise


def send_push_to_user(
    db,
    user_id: int,
    title: str,
    body: str,
    url: str | None = None,
    conversation_id: int | None = None,
):
    subscriptions = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.user_id == user_id,
            PushSubscription.is_active == True,
        )
        .all()
    )

    if not subscriptions:
        raise RuntimeError(f"No active push subscription found for user {user_id}")

    sent_count = 0

    for subscription in subscriptions:
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh,
                "auth": subscription.auth,
            },
        }

        try:
            send_web_push(
                subscription=subscription_info,
                title=title,
                body=body,
                url=url or "/calendar",
                conversation_id=conversation_id,
            )

            sent_count += 1

        except WebPushException as exc:
            permanent, reason, status_code = _permanent_subscription_error(exc)
            if permanent:
                subscription.is_active = False
                log_event(
                    logger,
                    "push.subscription.disabled",
                    level=logging.WARNING,
                    subscription_id=subscription.id,
                    user_id=user_id,
                    reason=reason,
                    status_code=status_code,
                )
            else:
                log_event(
                    logger,
                    "push.subscription.delivery_failed",
                    level=logging.ERROR,
                    subscription_id=subscription.id,
                    user_id=user_id,
                    reason=reason,
                    status_code=status_code,
                    error=str(exc),
                )

    db.commit()

    print(f"[WEB PUSH] Sent to user={user_id} | subscriptions={sent_count} | ")

    return sent_count
