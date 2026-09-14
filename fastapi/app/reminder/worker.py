import time
import threading

from app.reminder.jobs import process_due_booking_reminders


WORKER_INTERVAL_SECONDS = 60


def reminder_worker():
    print("[REMINDER WORKER] Started")

    while True:
        try:
            process_due_booking_reminders()

        except Exception as exc:
            print(
                f"[REMINDER WORKER ERROR] {exc}"
            )

        time.sleep(WORKER_INTERVAL_SECONDS)


def start_reminder_worker():
    thread = threading.Thread(
        target=reminder_worker,
        daemon=True,
        name="reminder-worker",
    )

    thread.start()

    return thread