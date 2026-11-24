"use client";

import { clearAuthSession, readAuthSession } from "./auth-storage";
import { ensureStoredSessionFresh } from "./session-refresh";
import {
  clearActivity,
  IDLE_TIMEOUT_MS,
  isIdleExpired,
  lastActivityAt,
  markActivity,
} from "./session-lifetime";

export { IDLE_TIMEOUT_MS, isIdleExpired, lastActivityAt, markActivity };

const ACTIVITY_EVENTS = ["mousedown", "keydown", "touchstart", "scroll", "focus"] as const;

export function endSession(): void {
  clearAuthSession();
  clearActivity();
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

  try {
    return (await ensureStoredSessionFresh()) !== null;
  } catch {
    // A temporary network failure is not inactivity and must not log the user
    // out. The next check or authenticated request will retry the refresh.
    return readAuthSession() !== null;
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
