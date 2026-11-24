"use client";

/** Sessions end after this much inactivity, regardless of token lifetime. */
export const IDLE_TIMEOUT_MS = 8 * 60 * 60 * 1000;

const ACTIVITY_KEY = "ListingIQ.lastActivity";

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
  return Date.now() - lastActivityAt() >= IDLE_TIMEOUT_MS;
}

export function clearActivity(): void {
  try {
    window.localStorage.removeItem(ACTIVITY_KEY);
  } catch {
    // ignore
  }
}
