import type { AuthResponse } from "../types/auth.types";

const AUTH_STORAGE_KEY = "ListingIQ.auth";
const AUTH_PERSISTENCE_KEY = "ListingIQ.authPersistence";

type SessionPersistence = "local" | "session";

export function saveAuthSession(
  auth: AuthResponse,
  persistence: SessionPersistence,
): void {
  // Dashboard deep links are designed to open in another tab. localStorage is
  // the browser storage shared by tabs; the inactivity guard still expires a
  // non-remembered session after eight idle hours and logout clears it here.
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(auth));
  window.localStorage.setItem(AUTH_PERSISTENCE_KEY, persistence);
  window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
}

export function readAuthSession(): AuthResponse | null {
  let serializedSession = window.localStorage.getItem(AUTH_STORAGE_KEY);
  if (!serializedSession) {
    // Migrate sessions created before cross-tab deep links were supported.
    serializedSession = window.sessionStorage.getItem(AUTH_STORAGE_KEY);
    if (serializedSession) {
      window.localStorage.setItem(AUTH_STORAGE_KEY, serializedSession);
      window.localStorage.setItem(AUTH_PERSISTENCE_KEY, "session");
      window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
    }
  }

  if (!serializedSession) {
    return null;
  }

  try {
    return JSON.parse(serializedSession) as AuthResponse;
  } catch {
    clearAuthSession();
    return null;
  }
}

export function clearAuthSession(): void {
  window.localStorage.removeItem(AUTH_STORAGE_KEY);
  window.localStorage.removeItem(AUTH_PERSISTENCE_KEY);
  window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
}

export function authSessionPersistence(): SessionPersistence {
  return window.localStorage.getItem(AUTH_PERSISTENCE_KEY) === "local" ? "local" : "session";
}
