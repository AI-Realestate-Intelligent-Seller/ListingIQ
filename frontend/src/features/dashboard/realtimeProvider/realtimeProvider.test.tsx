import { beforeEach, describe, expect, it, vi } from "vitest";

import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { requestJson } from "@/lib/api/http-client";
import { syncPushSubscription } from "./realtimeProvider";

vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: vi.fn(),
}));

vi.mock("@/lib/api/http-client", () => ({
  requestJson: vi.fn(() => Promise.resolve({})),
}));

const readSession = vi.mocked(readAuthSession);
const request = vi.mocked(requestJson);

describe("push subscription login sync", () => {
  beforeEach(() => {
    localStorage.clear();
    request.mockClear();
    readSession.mockReturnValue({
      access_token: "access-token",
      refresh_token: "refresh-token",
      token_type: "bearer",
      expires_in: 3600,
      user: { id: 3 },
    } as ReturnType<typeof readAuthSession>);
    process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY = "AQID";
    Object.defineProperty(window, "Notification", {
      configurable: true,
      value: { permission: "granted" },
    });
  });

  it("sends an existing browser subscription to the backend again on page load", async () => {
    const existingSubscription = {
      endpoint: "https://push.example/existing",
      options: { applicationServerKey: new Uint8Array([1, 2, 3]).buffer },
      toJSON: vi.fn(() => ({
        endpoint: "https://push.example/existing",
        keys: { p256dh: "existing-key", auth: "existing-auth" },
      })),
      unsubscribe: vi.fn(),
    };
    const subscribe = vi.fn();
    const registration = {
      pushManager: {
        getSubscription: vi.fn(() => Promise.resolve(existingSubscription)),
        subscribe,
      },
    };
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        register: vi.fn(() => Promise.resolve(registration)),
        ready: Promise.resolve(registration),
      },
    });
    Object.defineProperty(window, "PushManager", {
      configurable: true,
      value: class PushManager {},
    });
    localStorage.setItem("push_device_id", "returning-device");

    const result = await syncPushSubscription(3, () => false);

    expect(result).toBe(existingSubscription);
    expect(subscribe).not.toHaveBeenCalled();
    expect(request).toHaveBeenCalledWith("/push/subscribe", {
      method: "POST",
      accessToken: "access-token",
      payload: {
        user_id: 3,
        device_id: "returning-device",
        subscription: {
          endpoint: "https://push.example/existing",
          keys: { p256dh: "existing-key", auth: "existing-auth" },
        },
      },
    });
  });
});
