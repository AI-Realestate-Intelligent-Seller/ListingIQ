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

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  return a.length === b.length && a.every((byte, index) => byte === b[index]);
}

// SAVE PUSH SUBSCRIPTION

function hasPushPermission(): boolean {
  return "Notification" in window && Notification.permission === "granted";
}

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
  // Clean up both sides independently so a network failure cannot keep this
  // browser subscribed. Do not register a worker just to turn notifications off.
  const results = await Promise.allSettled([
    (async () => {
      if (!("serviceWorker" in navigator)) return;
      const registration = await navigator.serviceWorker.getRegistration("/");
      if (!registration) return;
      await Promise.all([
        (async () => {
          const subscription = await registration.pushManager?.getSubscription();
          await subscription?.unsubscribe();
        })(),
        (async () => {
          const notifications = await registration.getNotifications();
          notifications.forEach((notification) => notification.close());
        })(),
      ]);
    })(),
    deviceId
      ? requestJson("/push/unsubscribe", {
          method: "POST",
          accessToken: readAuthSession()?.access_token,
          payload: { device_id: deviceId },
        })
      : Promise.resolve(),
  ]);
  for (const result of results) {
    if (result.status === "rejected") throw result.reason;
  }
}

async function enablePushNotifications(userId: number, isCancelled: () => boolean) {
  const registration = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  let subscription = await registration.pushManager.getSubscription();
  if (isCancelled() || !hasPushPermission()) return null;

  const vapidPublicKey = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;
  if (!vapidPublicKey) {
    throw new Error("NEXT_PUBLIC_VAPID_PUBLIC_KEY is not configured.");
  }
  const applicationServerKey = urlBase64ToUint8Array(vapidPublicKey);

  // A subscription made under a previous VAPID key is rejected by the push
  // service on every send, so replace it rather than keep saving it.
  const currentKey = subscription?.options.applicationServerKey;
  if (
    subscription &&
    currentKey &&
    !sameBytes(new Uint8Array(currentKey), applicationServerKey)
  ) {
    await subscription.unsubscribe();
    subscription = null;
  }

  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey,
    });
  }
  if (isCancelled() || !hasPushPermission()) return null;
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
    let retryPush = false;
    let syncedPermission: NotificationPermission | undefined;
    async function setupPushNotifications(allowPrompt = false) {
      if (cancelled || syncingPush) return;
      if (!("Notification" in window)) return;
      syncingPush = true;
      let attemptedPermission = Notification.permission;
      try {
        // Sync once on login. Afterward, only a changed permission or a failed
        // request needs another write. Re-read after awaits to handle Block
        // during an in-flight subscribe without queueing duplicate saves.
        while (!cancelled) {
          const permission = Notification.permission;
          attemptedPermission = permission;
          if (!retryPush && permission === syncedPermission) return;
          retryPush = false;

          if (permission === "default" && allowPrompt) {
            allowPrompt = false;
            if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
            await Notification.requestPermission();
            continue;
          }
          if (permission === "granted") {
            if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
            if (await enablePushNotifications(userId, () => cancelled)) {
              syncedPermission = permission;
            }
          } else {
            await deactivatePushSubscription();
            syncedPermission = permission;
          }
        }
      } catch (error) {
        retryPush = true;
        console.error("[PUSH] Failed to enable notifications:", error);
      } finally {
        syncingPush = false;
        // A permission change may also have caused the request to fail. Apply
        // that new state immediately; retry unchanged failures only on online.
        if (!cancelled && Notification.permission !== attemptedPermission) {
          void setupPushNotifications();
        }
      }
    }

    // Let an immediately cancelled mount finish before starting network work
    // (React Strict Mode mounts effects twice during development).
    queueMicrotask(() => { void setupPushNotifications(true); });
    function retryPushSync() {
      if (retryPush) void setupPushNotifications();
    }
    window.addEventListener("online", retryPushSync);

    // React to Block immediately, including while this tab is in the background.
    let notificationPermission: PermissionStatus | undefined;
    function onPermissionChange() {
      void setupPushNotifications();
    }
    if (navigator.permissions?.query) {
      void navigator.permissions.query({ name: "notifications" })
        .then((permission) => {
          if (cancelled) return;
          notificationPermission = permission;
          permission.addEventListener("change", onPermissionChange);
        })
        .catch(() => { /* Permission observation is not supported everywhere. */ });
    }

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
      window.removeEventListener("online", retryPushSync);
      notificationPermission?.removeEventListener("change", onPermissionChange);
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
