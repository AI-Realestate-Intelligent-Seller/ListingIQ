from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import PushSubscription
from ..reminder.push import send_push_to_user

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