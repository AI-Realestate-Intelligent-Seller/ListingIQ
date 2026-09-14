

const API_URL = process.env.NEXT_PUBLIC_API_URL;


import { useEffect, useState } from 'react';


import { readAuthSession } from '../auth/lib/auth-storage'


function getPushDeviceId(): string {
  let deviceId =
    localStorage.getItem("push_device_id");

  if (!deviceId) {
    deviceId = crypto.randomUUID();

    localStorage.setItem(
      "push_device_id",
      deviceId
    );
  }

  return deviceId;
}


export async function enablePushNotifications(
  userId: number,
) {
  if (!("serviceWorker" in navigator)) {
    throw new Error(
      "Service workers are not supported by this browser."
    );
  }

  if (!("PushManager" in window)) {
    throw new Error(
      "Push notifications are not supported by this browser."
    );
  }

  // Ask for notification permission


  const permission =
    await Notification.requestPermission();

  if (permission !== "granted") {
    throw new Error(
      "Notification permission was not granted."
    );
  }


  // Register service worker


  const registration =
    await navigator.serviceWorker.register("/sw.js");

  await navigator.serviceWorker.ready;

  
  // Get VAPID public key


  const vapidPublicKey =
    process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY;

  if (!vapidPublicKey) {
    throw new Error(
      "NEXT_PUBLIC_VAPID_PUBLIC_KEY is not configured."
    );
  }
  // Convert VAPID key

  const applicationServerKey =
    urlBase64ToUint8Array(vapidPublicKey);


  // Create push subscription


  let subscription =
    await registration.pushManager.getSubscription();

  if (!subscription) {
    subscription =
      await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
  }

  // Send subscription to FastAPI

const deviceId = getPushDeviceId();

const response = await fetch(
  `${API_URL}/push/subscribe`,
  {
    method: "POST",

    headers: {
      "Content-Type": "application/json",
    },

    body: JSON.stringify({
      user_id: userId,
      device_id: deviceId,
      subscription: subscription.toJSON(),
    }),
  }
);

  if (!response.ok) {
    throw new Error(
      "Failed to save push subscription."
    );
  }

  const result = await response.json();



  return subscription;
}


// ----------------------------------------
// Base64 → Uint8Array
// ----------------------------------------

function urlBase64ToUint8Array(
  base64String: string,
): Uint8Array<ArrayBuffer> {

  const padding =
    "=".repeat(
      (4 - (base64String.length % 4)) % 4
    );

  const base64 =
    (
      base64String + padding
    )
      .replace(/-/g, "+")
      .replace(/_/g, "/");

  const rawData =
    window.atob(base64);

  return Uint8Array.from(
    [...rawData].map(
      (char) => char.charCodeAt(0)
    )
  );
}




// talking permission on calender load if no subscription exists

export async function checkPushStatus(): Promise<{
  status: Exclude<PushStatus, 'loading' | 'error'>;
  subscription: PushSubscription | null;
}> {
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

  // Registering SW itself does NOT ask notification permission
  const registration =
    await navigator.serviceWorker.register("/sw.js");

  await navigator.serviceWorker.ready;

  const subscription =
    await registration.pushManager.getSubscription();

  const permission = Notification.permission;

  if (
    permission === "granted" &&
    subscription
  ) {
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

  if (
    permission === "granted" &&
    !subscription
  ) {
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



type PushStatus =
  | 'loading'
  | 'subscribed'
  | 'needs_permission'
  | 'needs_subscription'
  | 'denied'
  | 'unsupported'
  | 'error';

export default function PushNotifications() {
  useEffect(() => {
    const setupPushNotifications = async () => {
      try {
        const result = await checkPushStatus();

        // Already subscribed → do nothing
        if (result.status === 'subscribed') {
          return;
        }

        // Browser doesn't support push → do nothing
        if (result.status === 'unsupported') {
          return;
        }

        // User previously blocked notifications → do nothing
        if (result.status === 'denied') {
          return;
        }

        // Get logged-in user
        const session = readAuthSession();

        if (!session) {
          console.error(
            '[PUSH] No authenticated session found.'
          );
          return;
        }

        const userId = session.user?.id;

        if (!userId) {
          console.error(
            '[PUSH] Unable to determine logged-in user.'
          );
          return;
        }
      
        // This will trigger the browser's permission popup
        await enablePushNotifications(userId);

     
      } catch (error) {
        console.error(
          '[PUSH] Failed to enable notifications:',
          error
        );
      }
    };

    setupPushNotifications();
  }, []);

  // No button, no UI
  return null;
}