"use client";

import {
  useEffect,
  useRef,
  useState,
} from "react";

import "../../../styles/dashboard.css";

import {
  NotificationItem,
  useNotifications,
} from "./notification-provider";


export function NotificationBell() {
  const [readAllError, setReadAllError] = useState("");
  const [
    open,
    setOpen,
  ] = useState(false);

  const rootRef =
    useRef<HTMLDivElement>(
      null,
    );


  const {
    notifications,
    unreadCount,
    isLoading,
    error,
    markAsRead,
    markAllAsRead,
    isMarkingAllRead,
    navigateNotification,
  } =
    useNotifications();



  useEffect(() => {
    if (!open) {
      return;
    }

    function handleClickOutside(
      event: PointerEvent,
    ) {
      if (
        rootRef.current &&
        !rootRef.current.contains(
          event.target as Node,
        )
      ) {
        setOpen(false);
      }
    }

    window.addEventListener(
      "pointerdown",
      handleClickOutside,
    );

    return () => {
      window.removeEventListener(
        "pointerdown",
        handleClickOutside,
      );
    };
  }, [open]);


  async function handleReadAll() {
    setReadAllError("");
    try {
      await markAllAsRead();
    } catch (error) {
      setReadAllError(error instanceof Error ? error.message : "Could not mark notifications as read. Try again.");
    }
  }

  async function handleNotificationClick(
    notification: NotificationItem,
  ) {
    await markAsRead(
      notification.id,
    );

    setOpen(false);

    navigateNotification(notification);
  }


  return (
    <div
      className="notification-bell"
      ref={rootRef}
    >
      <button
        type="button"
        className="notification-bell-button"
        aria-label="Notifications"
        aria-expanded={open}
        onClick={() =>
          setOpen(
            (current) =>
              !current,
          )
        }
      >
        <svg
          viewBox="0 0 24 24"
          aria-hidden="true"
          className="notification-bell-icon"
        >
          <path
            d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          <path
            d="M10 21h4"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </svg>

        {unreadCount > 0 ? (
          <span className="notification-bell-badge">
            {unreadCount > 99
              ? "99+"
              : unreadCount}
          </span>
        ) : null}
      </button>


      {open ? (
        <div className="notification-dropdown">

          <div className="notification-dropdown-header">
            <div>
              <span>
                Notifications
              </span>

              <small>
                {unreadCount} unread
              </small>
            </div>
            <button
              type="button"
              disabled={isLoading || isMarkingAllRead || unreadCount === 0}
              onClick={() => void handleReadAll()}
            >
              {isMarkingAllRead ? "Marking…" : "Mark all as read"}
            </button>
          </div>
          {readAllError ? <p className="notification-read-error" role="alert">{readAllError}</p> : null}


          <div className="notification-list">

            {isLoading ? (
              <div className="notification-empty">
                Loading notifications...
              </div>
            ) : null}


            {!isLoading &&
            error ? (
              <div className="notification-empty">
                {error}
              </div>
            ) : null}


            {!isLoading &&
            !error &&
            notifications.length ===
              0 ? (
              <div className="notification-empty">
                <strong>
                  No notifications
                </strong>

                <span>
                  You&apos;re all caught up.
                </span>
              </div>
            ) : null}


            {!isLoading &&
            !error &&
            notifications.map(
              (
                notification,
              ) => (
                <button
                  key={
                    notification.id
                  }
                  type="button"
                  className={`notification-item${
                    notification.isRead
                      ? ""
                      : " unread"
                  }`}
                  onClick={() =>
                    handleNotificationClick(
                      notification,
                    )
                  }
                >
                  <span className="notification-item-dot" />

                  <span className="notification-item-content">

                    <strong>
                      {
                        notification.title
                      }
                    </strong>

                    <span>
                      {
                        notification.message
                      }
                    </span>

                    <small>
                      {
                        notification.createdAt
                      }
                    </small>

                  </span>
                </button>
              ),
            )}

          </div>

        </div>
      ) : null}
    </div>
  );
}
