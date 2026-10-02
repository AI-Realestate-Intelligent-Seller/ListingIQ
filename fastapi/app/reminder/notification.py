from datetime import datetime

from ..models import Notification
from .push import send_push_to_user
from .actions import notification_action, dashboard_url, action_for_notification
from ..routes.websocket import broadcast_notification_event_sync


def create_notification(
    session,
    user_id: int,
    notification_type: str,
    title: str,
    message: str,
    booking_id: int | None = None,
    reminder_id: int | None = None,
    action_url: str | None = None,
    send_push: bool = True,
):

    action_url = dashboard_url(notification_action(notification_type, action_url, booking_id=booking_id))
    notification = Notification(
        user_id=user_id,
        booking_id=booking_id,
        reminder_id=reminder_id,
        type=notification_type,
        title=title,
        message=message,
        is_read=False,
        action_url=action_url,
        created_at=datetime.utcnow(),
    )

    session.add(notification)
    session.commit()
    session.refresh(notification)

    if send_push:
        try:
            send_push_to_user(
                db=session,
                user_id=user_id,
                title=title,
                body=message,
                url=action_url or "/calendar",
            )

        except Exception:
            session.rollback()

            # IMPORTANT:
            # don't raise here if you want notification DB + booking
            # to remain successful even when push fails.

    return notification




def list_notifications(
    Session,
    user_id:int):

      if(user_id is None):
          raise ValueError("user id is required")

      notifications=(Session.query(Notification).filter(Notification.user_id==user_id).order_by(Notification.created_at.desc()).all())

      return notifications

def update_notification_read_status(
    session,
    user_id: int,
    notification_id: int,
):
    if user_id is None:
        raise ValueError("user id is required")

    if notification_id is None:
        raise ValueError("notification id is required")

    notification = (
        session.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.id == notification_id,
        )
        .first()
    )

    if notification is None:
        raise ValueError("notification not found")

    notification.is_read = True
    notification.read_at = datetime.utcnow()

    session.commit()
    session.refresh(notification)

    return {
        "id": notification.id,
        "is_read": notification.is_read,
        "read_at": (
            notification.read_at.isoformat()
            if notification.read_at
            else None
        ),
    }


def mark_conversation_notifications_read(
    session,
    conversation_id: int,
    current_user:int,

):
    notifications = (
        session.query(Notification)
        .filter(
            Notification.user_id
            == current_user.id,

            Notification.conversation_id
            == conversation_id,

            Notification.type
            == "CUSTOMER_REPLY",

            Notification.is_read
            == False,
        )
        .all()
    )

    now = datetime.utcnow()

    for notification in notifications:
        notification.is_read = True
        notification.read_at = now

    session.commit()

    return {

        "marked_read":
            len(notifications),
    }

def notify_user(
    session,
    user_id: int,
    notification_type: str,
    title: str,
    message: str,
    action_url: str | None = None,
    booking_id: int | None = None,
    send_push: bool = True,
):
    """
    Create an in-app notification, optionally send Web Push,
    and broadcast the notification over WebSocket.
    """

    notification = create_notification(
        session=session,
        user_id=user_id,
        booking_id=booking_id,
        notification_type=notification_type,
        title=title,
        message=message,
        action_url=action_url,
        send_push=send_push,
    )

    notification_data = {
        "id": notification.id,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "is_read": notification.is_read,
        "action_url": notification.action_url,
        "action": action_for_notification(notification),
        "booking_id": notification.booking_id,
        "created_at": (
            notification.created_at.isoformat()
            if notification.created_at
            else None
        ),
    }

    broadcast_notification_event_sync(
        user_id=user_id,
        notification=notification_data,
    )

    return notification




def notify_conversation_reply(
    session,
    user_id: int,
    conversation_id: int,
    message_text: str,
    sender_name: str | None = None,
):
    display_name = (
        sender_name
        or "Customer"
    )

    existing = (
        session.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.conversation_id == conversation_id,
            Notification.type == "CUSTOMER_REPLY",
            Notification.is_read.is_(False),
        )
        .order_by(
            Notification.created_at.desc()
        )
        .first()
    )



    if existing:

        existing.unread_count = (
            existing.unread_count or 1
        ) + 1

        existing.title = (
            f"{existing.unread_count} new messages"
        )

        existing.message = (
            message_text[:150]
        )

        existing.created_at = (
            datetime.utcnow()
        )

        notification = existing


    else:

        notification = Notification(
            user_id=user_id,
            conversation_id=conversation_id,
            type="CUSTOMER_REPLY",
            title=f"{display_name} replied",
            message=message_text[:150],
            action_url=dashboard_url({"view": "followups", "conversation_id": conversation_id}),
            unread_count=1,
            is_read=False,
            created_at=datetime.utcnow(),
        )

        session.add(
            notification
        )

    session.commit()

    session.refresh(
        notification
    )


    notification_data = {
        "id": notification.id,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "is_read": notification.is_read,
        "action_url": notification.action_url,
        "conversation_id": notification.conversation_id,
        "action": action_for_notification(notification),
        "unread_count": notification.unread_count,
        "created_at": (
            notification.created_at.isoformat()
            if notification.created_at
            else None
        ),
    }

    broadcast_notification_event_sync(
        user_id=user_id,
        notification=notification_data,
    )


    try:
        send_push_to_user(
            db=session,
            user_id=user_id,
            title=notification.title,
            body=notification.message,
            url=dashboard_url(action_for_notification(notification)),
            conversation_id=conversation_id,
        )

    except Exception as error:
        print(
            "[CUSTOMER REPLY PUSH FAILED]",
            error,
        )

    return notification


def read_all_notifications(
    session,
    user_id: int,
):
    if user_id is None:
        raise ValueError("user id is required")

    notifications = (
        session.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        .all()
    )

    now = datetime.utcnow()

    for notification in notifications:
        notification.is_read = True
        notification.read_at = now

    session.commit()

    return {
        "marked_read": len(notifications),
         "status": "ok"
    }