
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Booking
from app.models import BookingReminder
from app.models import Notification,User
from .push import send_push_to_user

BATCH_SIZE = 500
from zoneinfo import ZoneInfo


def utc_to_user_timezone(
    utc_naive_datetime,
    timezone_name: str,
):
    utc_aware = utc_naive_datetime.replace(
        tzinfo=timezone.utc
    )

    user_zone = ZoneInfo(
        timezone_name
    )

    return utc_aware.astimezone(
        user_zone
    )

def utc_now_naive():
    """
    Database stores UTC as naive datetime.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def process_due_booking_reminders():

    now = utc_now_naive()

    with SessionLocal() as db:
        # Get due reminders
       
        stmt = (
    select(BookingReminder)
    .where(
        BookingReminder.status == "pending",
        BookingReminder.scheduled_for <= now,
    )
    .order_by(
        BookingReminder.scheduled_for
    )
    .limit(BATCH_SIZE)
)

        reminders = db.scalars(stmt).all()

       
        # Process reminders

        for reminder in reminders:
            reminder_id = reminder.id
            try:
                process_single_reminder(
                    db=db,
                    reminder=reminder,
                    now=now,
                )

            except Exception as exc:

                db.rollback()

                failed_reminder = db.get(
                    BookingReminder,
                    reminder_id,
                )

                if failed_reminder:

                    failed_reminder.status = "failed"

                    db.commit()

                print(
                    f"[REMINDER ERROR] "
                    f"id={reminder_id} | "
                    f"error={exc}"
                )


def process_single_reminder(
    db,
    reminder: BookingReminder,
    now: datetime,
):
    # Claim reminder
    reminder.status = "processing"
    db.commit()

    # Get booking
    

    booking = db.get(
        Booking,
        reminder.booking_id,
    )

    if booking is None:

        reminder.status = "cancelled"
        db.commit()

        print(
            f"[REMINDER CANCELLED] "
            f"id={reminder.id} | "
            f"booking not found"
        )

        return

    # Booking already started

    if booking.start_at <= now:

        reminder.status = "cancelled"
        db.commit()

        print(
            f"[REMINDER CANCELLED] "
            f"id={reminder.id} | "
            f"booking already started"
        )

        return

    # Reminder title


    if reminder.reminder_type == "30_minutes":

        title = "Meeting in 30 minutes"

    elif reminder.reminder_type == "10_minutes":

        title = "Meeting in 10 minutes"

    else:

        title = "Upcoming meeting"
   
    user = db.get(User,reminder.user_id,)

    timezone_name = (user.timezone
    if user and user.timezone
    else "America/Chicago"
)

    local_start = utc_to_user_timezone(
    booking.start_at,
    timezone_name,
)
   
    # Notification message

    message = (
    f"Your meeting starts at "
    f"{local_start.strftime('%I:%M %p')}."
)
    # Create notification
   

    notification = Notification(
        user_id=reminder.user_id,
        booking_id=booking.id,
        reminder_id=reminder.id,
        type="BOOKING_REMINDER",
        title=title,
        message=message,
        is_read=False,
    )

    db.add(notification)
    db.commit()
    db.refresh(notification)


    try:

        send_push_to_user(
            db=db,
            user_id=reminder.user_id,
            title=title,
            body=message,
            url=f"/calendar/bookings/{booking.id}",
        )

    except Exception as error:

        print(
            f"[PUSH FAILED] "
            f"reminder_id={reminder.id} | "
            f"error={error}"
        )

        # Keep it pending so it can be retried.
        reminder.status = "pending"

        db.commit()

        return

    # Mark reminder sent
   

    reminder.status = "sent"
    reminder.sent_at = utc_now_naive()

    db.commit()

    print(
        f"[REMINDER SENT] "
        f"id={reminder.id}"
    )

