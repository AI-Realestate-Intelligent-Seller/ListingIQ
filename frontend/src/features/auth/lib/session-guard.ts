"use client";

import { requestJson } from "@/lib/api/http-client";

import { clearAuthSession, readAuthSession, saveAuthSession } from "./auth-storage";
import type { AuthResponse } from "../types/auth.types";

/** Sessions end after this much inactivity, regardless of token lifetime. */
export const IDLE_TIMEOUT_MS = 8 * 60 * 60 * 1000;

/** Refresh the access token this long before it expires. */
const REFRESH_MARGIN_MS = 5 * 60 * 1000;

const ACTIVITY_KEY = "ListingIQ.lastActivity";
const ACTIVITY_EVENTS = ["mousedown", "keydown", "touchstart", "scroll", "focus"] as const;

export function markActivity(): void {
  try {
    window.localStorage.setItem(ACTIVITY_KEY, String(Date.now()));
  } catch {
    // Private mode / storage disabled — the in-memory session still works.
  }
}

export function lastActivityAt(): number {
  const stored = Number(window.localStorage.getItem(ACTIVITY_KEY));
  if (Number.isFinite(stored) && stored > 0) return stored;
  // No record yet (first load after login): treat now as the start.
  markActivity();
  return Date.now();
}

export function isIdleExpired(): boolean {
  return Date.now() - lastActivityAt() > IDLE_TIMEOUT_MS;
}

export function endSession(): void {
  clearAuthSession();
  try {
    window.localStorage.removeItem(ACTIVITY_KEY);
  } catch {
    // ignore
  }
}

/** Seconds remaining on the stored access token, from its JWT `exp` claim. */
function secondsUntilExpiry(accessToken: string): number {
  try {
    const [, payload] = accessToken.split(".");
    const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return Number(claims.exp) * 1000 - Date.now();
  } catch {
    return 0;
  }
}

/**
 * Keep the session alive while the user is active.
 *
 * Access tokens are short-lived, so an active session is silently refreshed;
 * only genuine inactivity ends it. Returns false when the session is over.
 */
export async function ensureFreshSession(): Promise<boolean> {
  const session = readAuthSession();
  if (!session) return false;

  if (isIdleExpired()) {
    endSession();
    return false;
  }

  if (secondsUntilExpiry(session.access_token) > REFRESH_MARGIN_MS) {
    return true;
  }

  try {
    const refreshed = await requestJson<AuthResponse>("/auth/refresh", {
      method: "POST",
      payload: { refresh_token: session.refresh_token },
    });
    saveAuthSession(refreshed, window.localStorage.getItem("ListingIQ.auth") ? "local" : "session");
    return true;
  } catch {
    // Refresh token rejected or expired: the session is genuinely over.
    endSession();
    return false;
  }
}

/**
 * Watch for inactivity and expiry, calling `onExpired` once the session ends.
 * Returns a cleanup function.
 */
export function watchSession(onExpired: () => void): () => void {
  const handleActivity = () => markActivity();
  ACTIVITY_EVENTS.forEach((event) =>
    window.addEventListener(event, handleActivity, { passive: true }),
  );

  let stopped = false;
  const check = async () => {
    if (stopped) return;
    const alive = await ensureFreshSession();
    if (!alive && !stopped) {
      stopped = true;
      onExpired();
    }
  };

  void check();
  const timer = window.setInterval(check, 60_000);

  return () => {
    stopped = true;
    window.clearInterval(timer);
    ACTIVITY_EVENTS.forEach((event) => window.removeEventListener(event, handleActivity));

  };
}
