import { afterEach, describe, expect, it } from "vitest";

import type { AuthResponse } from "../types/auth.types";
import { authSessionPersistence, clearAuthSession, readAuthSession, saveAuthSession } from "./auth-storage";

const auth: AuthResponse = {
  access_token: "access-token",
  refresh_token: "refresh-token",
  token_type: "bearer",
  expires_in: 3600,
  user: {
    id: 7,
    email: "broker@example.com",
    first_name: "Ava",
    last_name: "Broker",
    full_name: "Ava Broker",
    brokerage_id: "brokerage-1",
    brokerage_name: "Example Realty",
    role: "broker",
    is_verified: true,
    is_active: true,
    created_at: "2026-11-17T00:00:00Z",
  },
};

afterEach(() => {
  clearAuthSession();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("cross-tab auth storage", () => {
  it("stores a valid non-remembered session in shared browser storage", () => {
    saveAuthSession(auth, "session");

    expect(window.localStorage.getItem("ListingIQ.auth")).toContain("access-token");
    expect(window.sessionStorage.getItem("ListingIQ.auth")).toBeNull();
    expect(authSessionPersistence()).toBe("session");
    expect(readAuthSession()).toEqual(auth);
  });

  it("migrates an existing tab-only session so a subsequent tab can read it", () => {
    window.sessionStorage.setItem("ListingIQ.auth", JSON.stringify(auth));

    expect(readAuthSession()).toEqual(auth);
    expect(window.localStorage.getItem("ListingIQ.auth")).toContain("access-token");
    expect(window.sessionStorage.getItem("ListingIQ.auth")).toBeNull();
  });
});
