import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthResponse } from "@/features/auth/types/auth.types";
import { clearAuthSession, readAuthSession, saveAuthSession } from "@/features/auth/lib/auth-storage";
import { IDLE_TIMEOUT_MS } from "@/features/auth/lib/session-lifetime";

import { getJson } from "./http-client";

function token(expiresAt: number): string {
  const payload = btoa(JSON.stringify({ exp: Math.floor(expiresAt / 1000) }));
  return `header.${payload}.signature`;
}

function auth(accessToken: string, refreshToken = "refresh-token"): AuthResponse {
  return {
    access_token: accessToken,
    refresh_token: refreshToken,
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
}

afterEach(() => {
  vi.restoreAllMocks();
  clearAuthSession();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("authenticated request session lifetime", () => {
  it("uses the refreshed token in storage even when a screen passes its original token", async () => {
    const original = token(Date.now() - 60_000);
    const refreshed = auth(token(Date.now() + 60 * 60 * 1000), "next-refresh-token");
    saveAuthSession(auth(original), "session");
    window.localStorage.setItem("ListingIQ.lastActivity", String(Date.now()));
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(refreshed), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));

    await expect(getJson<{ ok: boolean }>("/protected", original)).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toContain("/auth/refresh");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Authorization")).toBe(
      `Bearer ${refreshed.access_token}`,
    );
    expect(readAuthSession()?.access_token).toBe(refreshed.access_token);
  });

  it("refreshes and retries once when a browser wakes to a 401", async () => {
    const original = token(Date.now() + 30 * 60 * 1000);
    const refreshed = auth(token(Date.now() + 60 * 60 * 1000));
    saveAuthSession(auth(original), "session");
    window.localStorage.setItem("ListingIQ.lastActivity", String(Date.now()));
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "expired" }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(refreshed), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));

    await expect(getJson<{ ok: boolean }>("/protected", original)).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toContain("/auth/refresh");
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get("Authorization")).toBe(
      `Bearer ${refreshed.access_token}`,
    );
  });

  it("does not refresh or call the API after eight hours of inactivity", async () => {
    const original = token(Date.now() + 60 * 60 * 1000);
    saveAuthSession(auth(original), "session");
    window.localStorage.setItem(
      "ListingIQ.lastActivity",
      String(Date.now() - IDLE_TIMEOUT_MS),
    );
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(getJson("/protected", original)).rejects.toMatchObject({
      status: 401,
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(readAuthSession()).toBeNull();
  });
});
