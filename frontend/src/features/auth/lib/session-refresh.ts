"use client";

import { publicEnv } from "@/config/public-env";

import type { AuthResponse } from "../types/auth.types";
import {
  authSessionPersistence,
  clearAuthSession,
  readAuthSession,
  saveAuthSession,
} from "./auth-storage";
import { clearActivity, isIdleExpired } from "./session-lifetime";

/** Refresh the access token this long before it expires. */
const REFRESH_MARGIN_MS = 5 * 60 * 1000;

let refreshInFlight: Promise<AuthResponse | null> | null = null;

/** Milliseconds remaining on an access token, from its JWT `exp` claim. */
function millisecondsUntilExpiry(accessToken: string): number {
  try {
    const [, payload] = accessToken.split(".");
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const claims = JSON.parse(atob(padded));
    return Number(claims.exp) * 1000 - Date.now();
  } catch {
    return 0;
  }
}

function clearExpiredSession(): void {
  clearAuthSession();
  clearActivity();
}

/**
 * Return the current stored session, refreshing its short-lived access token
 * when needed. The refresh token is never used after eight idle hours.
 *
 * Network failures are thrown without clearing browser state. A rejected
 * refresh clears it because that session can no longer be authenticated.
 */
export async function ensureStoredSessionFresh(force = false): Promise<AuthResponse | null> {
  const session = readAuthSession();
  if (!session) return null;
  if (isIdleExpired()) {
    clearExpiredSession();
    return null;
  }
  if (!force && millisecondsUntilExpiry(session.access_token) > REFRESH_MARGIN_MS) {
    return session;
  }
  if (refreshInFlight) return refreshInFlight;

  const persistence = authSessionPersistence();
  refreshInFlight = (async () => {
    const response = await fetch(`${publicEnv.apiBaseUrl}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: session.refresh_token }),
    });
    if (!response.ok) {
      clearExpiredSession();
      return null;
    }
    const refreshed = await response.json() as AuthResponse;
    saveAuthSession(refreshed, persistence);
    return refreshed;
  })().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}
