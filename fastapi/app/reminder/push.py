import json
import os

from pywebpush import webpush, WebPushException
from sqlalchemy.orm import Session

from ..models import PushSubscription
from .actions import notification_action, dashboard_url


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
        raise RuntimeError(
            "VAPID_PRIVATE_KEY is not configured"
        )

    if not vapid_email:
        raise RuntimeError(
            "VAPID_EMAIL is not configured"
        )

    payload = json.dumps(
    {
        "title": title,
        "body": body,
        "url": dashboard_url(notification_action(action_url=url, conversation_id=conversation_id)),
        "action": notification_action(action_url=url, conversation_id=conversation_id),
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
            response.status_code
            if response is not None
            else "no-response-object",
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
        raise RuntimeError(
            f"No active push subscription found for user {user_id}"
        )

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

            if (
                exc.response is not None
                and exc.response.status_code == 410
            ):
                print(
                    "[WEB PUSH] "
                    f"Subscription expired. "
                    f"Deactivating subscription id={subscription.id}"
                )

                subscription.is_active = False

            else:
                print(
                    "[WEB PUSH ERROR] "
                    f"Subscription id={subscription.id}: "
                    f"{exc}"
                )

    db.commit()

    print(
        f"[WEB PUSH] "
        f"Sent to user={user_id} | "
        f"subscriptions={sent_count} | "
       
    )

    return sent_count
