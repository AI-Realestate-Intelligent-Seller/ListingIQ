import type { AuthResponse } from "../types/auth.types";

const AUTH_STORAGE_KEY = "ListingIQ.auth";

type SessionPersistence = "local" | "session";

export function saveAuthSession(
  auth: AuthResponse,
  persistence: SessionPersistence,
): void {
  const primaryStorage = getStorage(persistence);
  const secondaryStorage = getStorage(
    persistence === "local" ? "session" : "local",
  );

  secondaryStorage.removeItem(AUTH_STORAGE_KEY);
  primaryStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(auth));
}

export function readAuthSession(): AuthResponse | null {
  const serializedSession =
    window.localStorage.getItem(AUTH_STORAGE_KEY) ??
    window.sessionStorage.getItem(AUTH_STORAGE_KEY);

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
  window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
}

function getStorage(persistence: SessionPersistence): Storage {
  return persistence === "local" ? window.localStorage : window.sessionStorage;
}
