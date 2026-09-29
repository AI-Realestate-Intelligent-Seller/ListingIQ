"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { publicEnv } from "@/config/public-env";
import { requestJson } from "@/lib/api/http-client";

type RealtimeMessage = {
  type: string;
  user_id?: number;
  booking?: unknown;
  notification?: {
    id: number;
    type: string;
    title: string;
    message: string;
    is_read: boolean;
    action_url?: string | null;
    created_at: string;
    conversation_id?: number | null;
    unread_count?: number;
  };
};

type RealtimeContextValue = {
  socket: WebSocket | null;
};

type PushStatus =
  | "subscribed"
  | "needs_permission"
  | "needs_subscription"
  | "denied"
  | "unsupported";

const RealtimeContext = createContext<RealtimeContextValue>({
  socket: null,
});

// PUSH DEVICE ID

function getPushDeviceId(): string {
  let deviceId = localStorage.getItem("push_device_id");

  if (!deviceId) {
    deviceId = crypto.randomUUID();
    localStorage.setItem("push_device_id", deviceId);
  }

  return deviceId;
}

// VAPID KEY CONVERTER

function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, "+")
    .replace(/_/g, "/");

  const rawData = window.atob(base64);

  return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
}

// SAVE PUSH SUBSCRIPTION

async function savePushSubscription(
  userId: number,
  subscription: PushSubscription,
) {
  const deviceId = getPushDeviceId();

  await requestJson("/push/subscribe", {
    method: "POST",
    accessToken: readAuthSession()?.access_token,
    payload: {
      user_id: userId,
      device_id: deviceId,
      subscription: subscription.toJSON(),
    },
  });
}

// CHECK CURRENT PUSH STATUS

async function deactivatePushSubscription() {
  const deviceId = localStorage.getItem("push_device_id");
  if (!deviceId) return;

  await requestJson("/push/unsubscribe", {
    method: "POST",
    accessToken: readAuthSession()?.access_token,
    payload: { device_id: deviceId },
  });
}

async function checkPushStatus(): Promise<{
  status: PushStatus;
  subscription: PushSubscription | null;
}> {
  if ("Notification" in window && Notification.permission === "denied") {
    return { status: "denied", subscription: null };
  }

  if (
    !("serviceWorker" in navigator) ||
    !("PushManager" in window) ||
    !("Notification" in window)
  ) {
    return {
      status: "unsupported",
      subscription: null,
    };
  }

  const registration = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;

  const subscription = await registration.pushManager.getSubscription();
  const permission = Notification.permission;

  if (permission === "granted" && subscription) {
    return {
      status: "subscribed",
      subscription,
    };
  }

  if (permission === "denied") {
    return {
      status: "denied",
      subscription: null,
    };
  }

  if (permission === "granted" && !subscription) {
    return {
      status: "needs_subscription",
      subscription: null,
    };
  }

  return {
    status: "needs_permission",
    subscription: null,
  };
}

async function enablePushNotifications(userId: number) {
  if (!("serviceWorker" in navigator)) {
    throw new Error("Service workers are not supported by this browser.");
  }

  if (!("PushManager" in window)) {
    throw new Error("Push notifications are not supported by this browser.");
  }

  let permission = Notification.permission;

  if (permission === "default") {
    permission = await Notification.requestPermission();
  }

  if (permission !== "granted") {
    await deactivatePushSubscription();
    throw new Error("Notification permission was not granted.");
  }

  const registration = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;

  const vapidPublicKey = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;

  if (!vapidPublicKey) {
    throw new Error("NEXT_PUBLIC_VAPID_PUBLIC_KEY is not configured.");
  }

  const applicationServerKey = urlBase64ToUint8Array(vapidPublicKey);

  let subscription = await registration.pushManager.getSubscription();

  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });
  }

  await savePushSubscription(userId, subscription);

  return subscription;
}

// REALTIME PROVIDER

export function RealtimeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const websocketRef = useRef<WebSocket | null>(null);
  const [socket, setSocket] = useState<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let reconnectTimer: number | null = null;
    let heartbeatTimer: number | null = null;

    const session = readAuthSession();

    if (!session?.access_token) {
      console.log("[Realtime] No authenticated session.");
      return;
    }

    const userId = session.user?.id;

    if (!userId) {
      console.error("[Realtime] Unable to determine authenticated user.");
      return;
    }

    let syncingPush = false;
    async function setupPushNotifications(allowPrompt = false) {
      if (cancelled || syncingPush) return;
      syncingPush = true;
      try {
        const result = await checkPushStatus();

        if (cancelled) {
          return;
        }

        if (result.status === "subscribed" && result.subscription) {
          await savePushSubscription(userId, result.subscription);
          console.log("[PUSH] Existing subscription registered for user:", userId);
          return;
        }

        if (result.status === "unsupported") {
          console.log("[PUSH] Push notifications unsupported.");
          return;
        }

        if (result.status === "denied") {
          await deactivatePushSubscription();
          console.log("[PUSH] Notification permission denied.");
          return;
        }

        if (result.status === "needs_permission") {
          await deactivatePushSubscription();
          if (!allowPrompt) return;
        }

        await enablePushNotifications(userId);
        console.log("[PUSH] Push notifications enabled for user:", userId);
      } catch (error) {
        console.error("[PUSH] Failed to enable notifications:", error);
      } finally {
        syncingPush = false;
      }
    }

    void setupPushNotifications(true);
    function refreshPushStatus() {
      if (document.visibilityState === "visible") {
        void setupPushNotifications();
      }
    }
    window.addEventListener("focus", refreshPushStatus);
    document.addEventListener("visibilitychange", refreshPushStatus);

    const wsBaseUrl = publicEnv.apiBaseUrl
      .replace(/^http:\/\//, "ws://")
      .replace(/^https:\/\//, "wss://")
      .replace(/\/api\/v1\/?$/, "")
      .replace(/\/$/, "");

    const wsUrl = `${wsBaseUrl}/ws/calendar`;

    function stopHeartbeat() {
      if (heartbeatTimer !== null) {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = null;
      }
    }

    function startHeartbeat(websocket: WebSocket) {
      stopHeartbeat();

      heartbeatTimer = window.setInterval(() => {
        if (websocket.readyState === WebSocket.OPEN) {
          websocket.send(
            JSON.stringify({
              type: "ping",
            }),
          );
        }
      }, 30000);
    }

    function connectWebSocket() {
      if (cancelled) {
        return;
      }

      /*
       * CRITICAL:
       * Read the CURRENT session every time we connect.
       *
       * Do not reuse the session that existed when
       * RealtimeProvider originally mounted.
       */
      const currentSession = readAuthSession();

      if (!currentSession?.access_token) {
        console.log("[Realtime] No current authenticated session.");
        return;
      }

      const currentUserId = currentSession.user?.id;

      if (!currentUserId) {
        console.error("[Realtime] Current user ID missing.");
        return;
      }

      const existing = websocketRef.current;

      if (
        existing &&
        (existing.readyState === WebSocket.OPEN ||
          existing.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }

      console.log("[Realtime] Connecting WebSocket:", {
        wsUrl,
        currentUserId,
      });

      const websocket = new WebSocket(wsUrl);
      websocketRef.current = websocket;
      setSocket(websocket);

      websocket.onopen = () => {
        if (cancelled) {
          websocket.close();
          return;
        }

        /*
         * Read session AGAIN just before
         * authenticating.
         */
        const latestSession = readAuthSession();

        if (!latestSession?.access_token) {
          console.log("[Realtime] Login disappeared before WS auth.");
          websocket.close();
          return;
        }

        console.log("[Realtime] Sending WebSocket auth:", {
          userId: latestSession.user?.id,
        });

        websocket.send(
          JSON.stringify({
            type: "auth",
            token: latestSession.access_token,
          }),
        );
      };

      websocket.onmessage = (event) => {
        try {
          const message: RealtimeMessage = JSON.parse(event.data);

          console.log("[Realtime] Message:", message);

          if (message.type === "authenticated") {
            console.log("[Realtime] WebSocket authenticated:", message);
            startHeartbeat(websocket);
            return;
          }

          if (message.type === "pong") {
            return;
          }

          if (
            message.type === "booking_created" ||
            message.type === "booking_updated" ||
            message.type === "booking_deleted"
          ) {
            window.dispatchEvent(
              new CustomEvent("listingiq-calendar-event", {
                detail: message,
              }),
            );
            return;
          }

          if (
            message.type === "notification_created" &&
            message.notification
          ) {
            console.log(
              "[Realtime] Notification received:",
              message.notification,
            );

            window.dispatchEvent(
              new CustomEvent("listingiq-notification", {
                detail: message.notification,
              }),
            );

            return;
          }
        } catch (error) {
          console.error("[Realtime] WebSocket message error:", error);
        }
      };

      websocket.onerror = (event) => {
        if (cancelled) {
          return;
        }

        console.warn("[Realtime] WebSocket error:", {
          currentUserId,
          readyState: websocket.readyState,
          eventType: event.type,
        });
      };

      websocket.onclose = (event) => {
        stopHeartbeat();

        if (websocketRef.current === websocket) {
          websocketRef.current = null;
          setSocket(null);
        }

        console.log("[Realtime] WebSocket closed:", {
          currentUserId,
          code: event.code,
          reason: event.reason,
        });

        if (cancelled) {
          return;
        }

        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = null;

          /*
           * This now reads the NEW/current
           * authenticated user.
           */
          connectWebSocket();
        }, 2000);
      };
    }

    connectWebSocket();

    return () => {
      cancelled = true;
      window.removeEventListener("focus", refreshPushStatus);
      document.removeEventListener("visibilitychange", refreshPushStatus);
      stopHeartbeat();

      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }

      const websocket = websocketRef.current;

      if (
        websocket &&
        (websocket.readyState === WebSocket.OPEN ||
          websocket.readyState === WebSocket.CONNECTING)
      ) {
        websocket.close(1000, "RealtimeProvider cleanup");
      }

      websocketRef.current = null;
      setSocket(null);
    };
  }, []);

  return (
    <RealtimeContext.Provider value={{ socket }}>
      {children}
    </RealtimeContext.Provider>
  );
}

export function useRealtime() {
  return useContext(RealtimeContext);
}
