from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.db import SessionLocal
from app.models import PushSubscription
from ..reminder.push import send_push_to_user
from .auth import get_current_user

router = APIRouter(
    prefix="/api/v1/push",
    tags=["Push Notifications"],
)


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
    db.query(PushSubscription).filter(
        PushSubscription.user_id == current_user.id,
        PushSubscription.device_id == data.device_id,
    ).update({PushSubscription.is_active: False}, synchronize_session=False)
    db.commit()
    return {"message": "Push subscription deactivated"}


@router.post("/subscribe")
def subscribe_to_push(
    data: dict,
    db: Session = Depends(get_db),
):
    user_id = data["user_id"]
    device_id = data["device_id"]

    subscription = data["subscription"]

    endpoint = subscription["endpoint"]
    p256dh = subscription["keys"]["p256dh"]
    auth = subscription["keys"]["auth"]


    # First look for this DEVICE, regardless of which
    # ListingIQ user previously owned it.


    existing = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.device_id == device_id,
        )
        .first()
    )

    if existing:
        # Same physical browser/device.


        existing.user_id = user_id
        existing.endpoint = endpoint
        existing.p256dh = p256dh
        existing.auth = auth
        existing.is_active = True

        db.commit()
        db.refresh(existing)

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

    return {
        "message": "Push subscription created",
        "subscription_id": push_subscription.id,
        "user_id": push_subscription.user_id,
    }
