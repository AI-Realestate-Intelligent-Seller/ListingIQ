import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import PushSubscription

from ..logger import get_logger, log_event
from .auth import get_current_user

router = APIRouter(
    prefix="/api/v1/push",
    tags=["Push Notifications"],
)
logger = get_logger(__name__)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


class UnsubscribeRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=100)


@router.post("/unsubscribe")
def unsubscribe_from_push(
    data: UnsubscribeRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    subscription_ids = [
        row_id
        for (row_id,) in db.query(PushSubscription.id)
        .filter(
            PushSubscription.user_id == current_user.id,
            PushSubscription.device_id == data.device_id,
            PushSubscription.is_active.is_(True),
        )
        .all()
    ]
    db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id,
        PushSubscription.device_id == data.device_id,
    ).update({PushSubscription.is_active: False}, synchronize_session=False)
    db.commit()
    for subscription_id in subscription_ids:
        log_event(
            logger,
            "push.subscription.disabled",
            level=logging.INFO,
            subscription_id=subscription_id,
            user_id=current_user.id,
            reason="browser_unsubscribed",
        )
    return {"message": "Push subscription deactivated"}


@router.post("/subscribe")
def subscribe_to_push(
    data: dict,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = current_user.id
    if data.get("user_id") != user_id:
        raise HTTPException(
            status_code=403, detail="Push subscription user does not match session."
        )
    device_id = data["device_id"]

    subscription = data["subscription"]

    endpoint = subscription["endpoint"]
    p256dh = subscription["keys"]["p256dh"]
    auth = subscription["keys"]["auth"]

    # Browser storage can be cleared while PushManager keeps the subscription.
    # Match the globally unique endpoint first so re-login refreshes/reactivates
    # that exact row even when this browser generated a new device ID.
    endpoint_match = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.endpoint == endpoint,
        )
        .first()
    )
    device_match = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.device_id == device_id,
        )
        .order_by(PushSubscription.is_active.desc(), PushSubscription.id.desc())
        .first()
    )
    existing = endpoint_match or device_match

    if existing:
        was_active = existing.is_active
        previous_user_id = existing.user_id
        if endpoint_match and device_match and endpoint_match.id != device_match.id:
            device_match.is_active = False
            log_event(
                logger,
                "push.subscription.disabled",
                level=logging.INFO,
                subscription_id=device_match.id,
                user_id=device_match.user_id,
                reason="superseded_by_endpoint_refresh",
            )
        existing.user_id = user_id
        existing.device_id = device_id
        existing.endpoint = endpoint
        existing.p256dh = p256dh
        existing.auth = auth
        existing.is_active = True

        db.commit()
        db.refresh(existing)

        log_event(
            logger,
            "push.subscription.refreshed",
            subscription_id=existing.id,
            user_id=existing.user_id,
            previous_user_id=previous_user_id,
            reactivated=not was_active,
            matched_by="endpoint" if endpoint_match else "device_id",
        )

        return {
            "message": "Push subscription refreshed",
            "subscription_id": existing.id,
            "user_id": existing.user_id,
        }

    push_subscription = PushSubscription(
        user_id=user_id,
        device_id=device_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        is_active=True,
    )

    db.add(push_subscription)
    db.commit()
    db.refresh(push_subscription)

    log_event(
        logger,
        "push.subscription.created",
        subscription_id=push_subscription.id,
        user_id=push_subscription.user_id,
    )

    return {
        "message": "Push subscription created",
        "subscription_id": push_subscription.id,
        "user_id": push_subscription.user_id,
    }
