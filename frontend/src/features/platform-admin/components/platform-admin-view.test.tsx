import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { requestJson } from "@/lib/api/http-client";
import { PlatformAdminView } from "./platform-admin-view";

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  router: { push: vi.fn(), replace: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
}));
vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({
    access_token: "platform-token",
    user: { role: "platform_admin" },
  }),
  clearAuthSession: vi.fn(),
}));
vi.mock("@/features/auth/lib/session-guard", () => ({
  endSession: vi.fn(),
  markActivity: vi.fn(),
  watchSession: () => vi.fn(),
}));
vi.mock("@/lib/api/http-client", () => ({
  requestJson: mocks.request,
  getJson: (path: string, accessToken?: string, signal?: AbortSignal) =>
    mocks.request(path, { method: "GET", accessToken, signal }),
  postJson: (path: string, payload: unknown, accessToken?: string) =>
    mocks.request(path, { method: "POST", payload, accessToken }),
  patchJson: (path: string, payload: unknown, accessToken?: string) =>
    mocks.request(path, { method: "PATCH", payload, accessToken }),
}));

const request = vi.mocked(requestJson);

describe("Platform Admin brokerage onboarding", () => {
  beforeEach(() => {
    request.mockReset();
    request.mockImplementation(async (path, options) => {
      if (path === "/platform-admin/overview") {
        return {
          organizations: 0,
          active_organizations: 0,
          users: 0,
          active_users: 0,
          leads: 0,
          campaigns: 0,
          messages: 0,
          new_users_30d: 0,
          pending_invitations: 0,
        };
      }
      if (path === "/platform-admin/organizations" && options?.method === "POST") {
        return {
          message: "Brokerage created and onboarding invitation sent.",
          brokerage_id: "brokerage-new",
          expires_at: "2026-10-07T12:00:00",
        };
      }
      if (path === "/platform-admin/organizations") {
        return {
          organizations: [
            {
              id: "brokerage-new",
              name: "Northstar Realty",
              owner_email: "owner@northstar.test",
              user_count: 0,
              active_user_count: 0,
              lead_count: 0,
              is_active: false,
              onboarding_status: "invited",
              invitation_role: "broker",
              created_at: "2026-10-05T12:00:00",
            },
          ],
        };
      }
      throw new Error(`Unexpected request: ${path}`);
    });
  });

  afterEach(cleanup);

  it("creates a brokerage and sends its initial user to the selected screen", async () => {
    render(<PlatformAdminView />);
    await screen.findByText("Platform pulse");
    fireEvent.click(screen.getByRole("button", { name: "Organizations" }));

    await screen.findByRole("heading", { name: "Add brokerage" });
    fireEvent.change(screen.getByLabelText("Brokerage name"), {
      target: { value: "Northstar Realty" },
    });
    fireEvent.change(screen.getByLabelText("Invite email"), {
      target: { value: "owner@northstar.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Initial screen" }));
    fireEvent.click(screen.getByRole("option", { name: "Initial screen: Broker" }));
    fireEvent.click(screen.getByRole("button", { name: "Send invite" }));

    await screen.findByText("Invitation pending");
    expect(screen.getByText("Awaiting Broker")).toBeTruthy();
    expect(screen.getByText("Brokerage created and onboarding invitation sent.")).toBeTruthy();
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/platform-admin/organizations",
        expect.objectContaining({
          method: "POST",
          payload: {
            brokerage_name: "Northstar Realty",
            invite_email: "owner@northstar.test",
            initial_role: "broker",
          },
        }),
      ),
    );
  });

  it("assigns Agent, HOB, or Broker screen access to a customer user", async () => {
    request.mockImplementation(async (path, options) => {
      if (path === "/platform-admin/overview") {
        return {
          organizations: 1, active_organizations: 1, users: 1, active_users: 1,
          leads: 0, campaigns: 0, messages: 0, new_users_30d: 0, pending_invitations: 0,
        };
      }
      if (path === "/platform-admin/users?q=" && options?.method === "GET") {
        return { users: [{
          id: 7, email: "member@northstar.test", full_name: "Northstar Member",
          role: "agent", brokerage_id: "brokerage-new", brokerage_name: "Northstar Realty",
          is_active: true, is_verified: true, created_at: "2026-10-05T12:00:00",
        }] };
      }
      if (path === "/platform-admin/users/7/screen-access" && options?.method === "PATCH") {
        return { message: "Screen access updated. The user must sign in again to open the new screen.", role: "broker" };
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<PlatformAdminView />);
    await screen.findByText("Platform pulse");
    fireEvent.click(screen.getByRole("button", { name: "Users & access" }));

    const access = await screen.findByRole("button", { name: "Screen access for member@northstar.test" });
    fireEvent.click(access);
    fireEvent.click(screen.getByRole("option", { name: "Screen access for member@northstar.test: Broker" }));

    await screen.findByText("Screen access updated. The user must sign in again to open the new screen.");
    expect(access.textContent).toContain("Broker");
    expect(request).toHaveBeenCalledWith(
      "/platform-admin/users/7/screen-access",
      expect.objectContaining({ method: "PATCH", payload: { role: "broker" } }),
    );
  });
});
