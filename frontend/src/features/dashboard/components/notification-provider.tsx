"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { getJson, postJson, patchJson } from "@/lib/api/http-client";
import { resolveNotificationAction, type NotificationAction } from "./notification-action";

export type ApiNotificationItem = {
  id: number;
  type: string;
  title: string;
  message: string;

  is_read: boolean;
  read_at?: string | null;

  action_url?: string | null;
  action?: NotificationAction | null;

  created_at: string;

  booking_id?: number | null;
  reminder_id?: number | null;

  conversation_id?: number | null;
  unread_count?: number;
};

export type NotificationItem = {
  id: number;
  type: string;
  title: string;
  message: string;

  createdAt: string;

  isRead: boolean;

  actionUrl?: string | null;
  action?: NotificationAction | null;
  bookingId?: number | null;

  conversationId?: number | null;

  unreadCount?: number;
};

type NotificationsResponse = {
  notifications: ApiNotificationItem[];
};

type UpdateNotificationStatusRequest = {
  notification_id: number;
};

type UpdateNotificationStatusResponse = {
  notification: {
    id: number;
    is_read: boolean;
    read_at: string | null;
  };
};

type MarkConversationReadResponse = {
  conversation_id?: number;
  marked_read: number;
};

type NotificationContextValue = {
  navigateNotification: (notification: NotificationItem) => void;
  notifications: NotificationItem[];

  unreadCount: number;

  isLoading: boolean;

  error: string;

  markAsRead: (id: number) => Promise<void>;
  markAllAsRead: () => Promise<void>;
  isMarkingAllRead: boolean;

  markConversationAsRead: (conversationId: number) => Promise<void>;
};

const NotificationContext = createContext<NotificationContextValue | undefined>(
  undefined,
);

function getAllNotifications(
  accessToken: string,
  signal?: AbortSignal,
): Promise<NotificationsResponse> {
  return getJson<NotificationsResponse>(
    "sms/notification/all-notifications",
    accessToken,
    signal,
  );
}

function updateNotificationStatus(
  accessToken: string,
  notificationId: number,
): Promise<UpdateNotificationStatusResponse> {
  return postJson<
    UpdateNotificationStatusResponse,
    UpdateNotificationStatusRequest
  >(
    "sms/notification/updateStatus",
    {
      notification_id: notificationId,
    },
    accessToken,
  );
}

export async function markConversationNotificationsRead(
  conversationId: number,
): Promise<MarkConversationReadResponse> {
  const session = readAuthSession();

  if (!session?.access_token) {
    throw new Error("No authenticated session.");
  }

  return patchJson<MarkConversationReadResponse, Record<string, never>>(
    `sms/notification/conversation/${conversationId}/read`,
    {},
    session.access_token,
  );
}

function formatNotificationTime(
  value: string,
): string {
  if (!value) {
    return "";
  }

  const hasTimezone =
    value.endsWith("Z") ||
    /[+-]\d{2}:\d{2}$/.test(value);

  const normalized =
    hasTimezone
      ? value
      : `${value}Z`;

  const date =
    new Date(normalized);

  if (
    Number.isNaN(
      date.getTime(),
    )
  ) {
    return "";
  }

  return date.toLocaleString(
    undefined,
    {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    },
  );
}
function mapNotification(
  notification: ApiNotificationItem,
): NotificationItem {
  console.log(
    "[NOTIFICATION TIME DEBUG]",
    {
      id: notification.id,
      raw_created_at:
        notification.created_at,

      parsed:
        new Date(
          notification.created_at,
        ).toString(),

      iso:
        new Date(
          notification.created_at,
        ).toISOString(),

      browserTimezone:
        Intl.DateTimeFormat()
          .resolvedOptions()
          .timeZone,
    },
  );

  return {
    id: notification.id,

    type: notification.type,

    title: notification.title,

    message: notification.message,

    createdAt: formatNotificationTime(notification.created_at),

    isRead: notification.is_read,

    actionUrl: notification.action_url,
    action: notification.action,
    bookingId: notification.booking_id,

    conversationId: notification.conversation_id,

    unreadCount: notification.unread_count ?? 1,
  };
}

export function NotificationProvider({
  children,
  onNavigate,
}: {
  onNavigate: (action: NotificationAction) => void;
  children: React.ReactNode;
}) {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);

  const [isLoading, setIsLoading] = useState(true);

  const [error, setError] = useState("");
  const [isMarkingAllRead, setIsMarkingAllRead] = useState(false);
  const markingAllReadRef = useRef(false);

  const activeConversationIdRef = useRef<number | null>(null);

  // ACTIVE CONVERSATION

  useEffect(() => {
    function handleActiveConversation(event: Event) {
      const customEvent = event as CustomEvent<{
        conversationId: number | null;
      }>;

      const conversationId = customEvent.detail?.conversationId ?? null;

      activeConversationIdRef.current = conversationId;

      console.log(
        "[NotificationProvider] Active conversation:",
        conversationId,
      );

      // Keep Service Worker informed about
      // which conversation is currently open.
      if ("serviceWorker" in navigator) {
        void navigator.serviceWorker.ready
          .then((registration) => {
            const worker = registration.active;

            if (!worker) {
              return;
            }

            worker.postMessage({
              type: "ACTIVE_CONVERSATION",
              conversation_id: conversationId,
            });

            console.log(
              "[NotificationProvider] Active conversation sent to SW:",
              conversationId,
            );
          })
          .catch((error) => {
            console.error(
              "[NotificationProvider] Failed to update SW active conversation:",
              error,
            );
          });
      }
    }

    window.addEventListener(
      "listingiq-active-conversation",
      handleActiveConversation,
    );

    return () => {
      window.removeEventListener(
        "listingiq-active-conversation",
        handleActiveConversation,
      );
    };
  }, []);

  // INITIAL FETCH

  useEffect(() => {
    const session = readAuthSession();

    if (!session?.access_token) {
      setIsLoading(false);
      return;
    }

    const accessToken = session.access_token;
    const controller = new AbortController();

    async function loadNotifications() {
      try {
        setIsLoading(true);
        setError("");

        console.log(
          "[NotificationProvider] Initial notification fetch",
        );

        const response = await getAllNotifications(
          accessToken,
          controller.signal,
        );

        console.log(
          "[NotificationProvider] Initial notifications:",
          response.notifications,
        );

        setNotifications(response.notifications.map(mapNotification));
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }

        console.error(
          "[NotificationProvider] Initial fetch failed:",
          error,
        );

        setError(
          error instanceof Error
            ? error.message
            : "Unable to load notifications",
        );
      } finally {
        setIsLoading(false);
      }
    }

    loadNotifications();

    return () => {
      controller.abort();
    };
  }, []);

  // MARK CONVERSATION NOTIFICATIONS AS READ

  const markConversationAsRead = useCallback(
    async (conversationId: number) => {
      try {
        const result = await markConversationNotificationsRead(
          conversationId,
        );

        console.log(
          "[NotificationProvider] Conversation notifications marked read:",
          {
            conversationId,

            markedRead: result.marked_read,
          },
        );

        /*
         * Immediately update local state.
         *
         * This prevents the bell badge
         * from waiting for another fetch.
         */
        setNotifications((current) =>
          current.map((item) =>
            item.conversationId === conversationId &&
            item.type === "CUSTOMER_REPLY"
              ? {
                  ...item,
                  isRead: true,
                }
              : item,
          ),
        );
      } catch (error) {
        console.error(
          "[NotificationProvider] Failed to mark conversation notifications read:",
          error,
        );
      }
    },
    [],
  );

  useEffect(() => {
    function handleRealtimeNotification(event: Event) {
      const customEvent = event as CustomEvent<ApiNotificationItem>;

      const incoming = customEvent.detail;

      if (!incoming) {
        return;
      }

      console.log(
        "[NotificationProvider] Realtime notification:",
        incoming,
      );

      if (
        incoming.type === "CUSTOMER_REPLY" &&
        incoming.conversation_id !== null &&
        incoming.conversation_id !== undefined &&
        activeConversationIdRef.current !== null &&
        Number(incoming.conversation_id) ===
          Number(activeConversationIdRef.current)
      ) {
        const conversationId = Number(incoming.conversation_id);

        console.log(
          "[NotificationProvider] Reply belongs to active conversation:",
          conversationId,
        );

        /*
         * Persist read state in backend.
         */
        void markConversationNotificationsRead(conversationId)
          .then((result) => {
            console.log(
              "[NotificationProvider] Active conversation marked read:",
              {
                conversationId,
                markedRead: result.marked_read,
              },
            );
          })
          .catch((error) => {
            console.error(
              "[NotificationProvider] Failed to mark active conversation read:",
              error,
            );
          });

       
        setNotifications((current) =>
          current.map((item) =>
            Number(item.conversationId) === conversationId &&
            item.type === "CUSTOMER_REPLY"
              ? {
                  ...item,
                  isRead: true,
                }
              : item,
          ),
        );

       
        return;
      }

      // NORMAL REALTIME NOTIFICATION

      const notification = mapNotification(incoming);

      setNotifications((current) => {
        const alreadyExists = current.some(
          (item) => item.id === notification.id,
        );

        /*
         * Grouped customer replies reuse
         * the same notification ID.
         *
         * Update existing notification
         * instead of creating duplicates.
         */
        if (alreadyExists) {
          console.log(
            "[NotificationProvider] Updating realtime notification:",
            notification.id,
          );

          return current.map((item) =>
            item.id === notification.id ? notification : item,
          );
        }

        console.log(
          "[NotificationProvider] Adding realtime notification:",
          notification.id,
        );

        return [notification, ...current];
      });
    }

    window.addEventListener(
      "listingiq-notification",
      handleRealtimeNotification,
    );

    return () => {
      window.removeEventListener(
        "listingiq-notification",
        handleRealtimeNotification,
      );
    };
  }, []);

  async function markAsRead(id: number) {
    const notification = notifications.find((item) => item.id === id);

    if (!notification || notification.isRead) {
      return;
    }

    const session = readAuthSession();

    if (!session?.access_token) {
      return;
    }

    try {
      await updateNotificationStatus(session.access_token, id);

      setNotifications((current) =>
        current.map((item) =>
          item.id === id
            ? {
                ...item,
                isRead: true,
              }
            : item,
        ),
      );
    } catch (error) {
      console.error(
        "[NotificationProvider] Failed to mark notification read:",
        error,
      );
    }
  }

  async function markAllAsRead() {
    if (markingAllReadRef.current) return;
    const session = readAuthSession();
    if (!session?.access_token) throw new Error("No authenticated session.");

    // Leave notifications received or replaced during the request unread.
    const pending = new Set(notifications.filter((item) => !item.isRead));
    markingAllReadRef.current = true;
    setIsMarkingAllRead(true);
    try {
      await patchJson<{ marked_read: number; status: string }, Record<string, never>>(
        "sms/notification/read-all",
        {},
        session.access_token,
      );
      setNotifications((current) => current.map((item) =>
        pending.has(item) ? { ...item, isRead: true } : item,
      ));
    } finally {
      markingAllReadRef.current = false;
      setIsMarkingAllRead(false);
    }
  }

  const unreadCount = notifications.filter(
    (notification) => !notification.isRead,
  ).length;

  return (
    <NotificationContext.Provider
      value={{
        navigateNotification: (notification) =>
          onNavigate(resolveNotificationAction(notification)),
        notifications,
        unreadCount,
        isLoading,
        error,
        markAsRead,
        markAllAsRead,
        isMarkingAllRead,
        markConversationAsRead,
      }}
    >
      {children}
    </NotificationContext.Provider>
  );
}

export function useNotifications() {
  const context = useContext(NotificationContext);

  if (!context) {
    throw new Error(
      "useNotifications must be used inside NotificationProvider",
    );
  }

  return context;
}
