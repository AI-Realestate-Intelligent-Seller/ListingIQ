import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { PropertyRadarView } from "./propertyradar-view";
import { PlatformAdminView } from "@/features/platform-admin/components/platform-admin-view";
import { requestJson } from "@/lib/api/http-client";
import type { Summary } from "./types";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));
vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({
    access_token: "test",
    user: { role: "platform_admin" },
  }),
  clearAuthSession: vi.fn(),
}));
vi.mock("@/features/auth/lib/session-guard", () => ({
  endSession: vi.fn(),
  markActivity: vi.fn(),
  watchSession: () => () => {},
}));
vi.mock("@/lib/api/http-client", () => ({ requestJson: vi.fn() }));
vi.mock("@/features/platform-admin/api", () => ({}));
let summary: Summary;
const mock = vi.mocked(requestJson);
beforeEach(() => {
  summary = {
    status: "Active",
    enabled: true,
    config: {
      enabled: true,
      public_webhook_url: "",
      state: "IL",
      city: "Chicago",
      zip_codes: [],
      county_fips: "",
      initial_rows: 20,
      high_equity_min: 50,
      selected_categories: ["expired", "vacant"],
      monitor_new_matches: true,
      monitor_status_changes: true,
      skiptrace_new: true,
      skiptrace_initial: false,
      contact_mode: "primary",
      phone_limit: 2450,
      email_limit: 2450,
      export_limit: 45000,
      monitored_limit: 45000,
      billing_cycle_start: "2026-01-01",
    },
    token_configured: true,
    connection_status: "connected",
    last_successful_connection_at: null,
    last_successful_sync_at: null,
    last_webhook_at: null,
    last_error: null,
    busy: false,
    local_mode: true,
    webhook_url: "",
    webhook_id: null,
    webhook_secret_configured: true,
    webhook_registration_allowed: false,
    webhook_message:
      "A public HTTPS URL is required for PropertyRadar to reach this webhook.",
    categories: [
      { key: "expired", label: "Expired" },
      { key: "vacant", label: "Vacant Property" },
      { key: "fsbo", label: "FSBO" },
    ],
    metrics: { active_monitored_lists: 0 },
    usage: {
      cycle_start: "2026-09-01",
      cycle_end: "2026-10-01",
      used: { phone_unlock: 0, email_unlock: 0, property_export: 0 },
      remaining: {
        phone_unlock: 2450,
        email_unlock: 2450,
        property_export: 45000,
      },
      provider_reported_cost: "0",
      no_overage: true,
    },
    max_initial_rows: 100,
    per_list_limit: 10000,
  };
  mock.mockReset();
  push.mockReset();
  mock.mockImplementation(async (path, options) => {
    if (path.endsWith("/config")) {
      summary = {
        ...summary,
        config: options?.payload as Summary["config"],
        enabled: (options?.payload as Summary["config"]).enabled,
      };
      return summary;
    }
    if (path.includes("?offset="))
      return { items: [], total: 0, offset: 0, limit: 25 };
    return summary;
  });
});
afterEach(cleanup);
const open = async (tab: string) => {
  await screen.findByText("Local development mode");
  const destination: Record<string, [string, string?]> = {
    "Limits & Usage": ["Usage & Limits"],
    "Search Settings": ["Data", "Search Settings"],
    "Lead Lists": ["Data", "Lists"],
    "Webhooks & Events": ["Activity"],
    Configuration: ["Configuration"],
  };
  const [section, subtab] = destination[tab] || [tab];
  fireEvent.click(screen.getByRole("button", { name: section }));
  if (subtab) fireEvent.click(screen.getByRole("button", { name: subtab }));
};

describe("Internal Console PropertyRadar", () => {
  it("adds Integrations under Operations and navigates to the existing console route", async () => {
    render(
      <PlatformAdminView>
        <span>integration content</span>
      </PlatformAdminView>,
    );
    const item = screen.getByRole("button", {
      name: "Integrations",
    });
    expect(item.parentElement?.textContent).toContain("Operations");
    fireEvent.click(item);
    expect(push).toHaveBeenCalledWith("/platform-admin/integrations");
  });
  it("shows the integration card without a confusing master toggle", async () => {
    render(<PropertyRadarView />);
    expect(
      await screen.findByText(
        "Property intelligence, monitored lists, webhooks and owner contact enrichment.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Configure" }).getAttribute("href"),
    ).toBe("/platform-admin/integrations/propertyradar");
    expect(
      mock.mock.calls.some(([path]) => path === "/integrations/propertyradar"),
    ).toBe(true);
    expect(screen.queryByRole("switch")).toBeNull();
  });
  it("uses the shared six-section integration flow", async () => {
    render(<PropertyRadarView detail />);
    await screen.findByText("Local development mode");
    expect(screen.getByText("Free manual action")).toBeTruthy();
    const sections = [
      "Connection",
      "Configuration",
      "Data",
      "Usage & Limits",
      "Activity",
      "History",
    ];
    expect(
      sections.every((name) => !!screen.getByRole("button", { name })),
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    expect(
      screen.getByRole("button", { name: "Property Search" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Property Details" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Contact Enrichment" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Properties" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Search Settings" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Lists" })).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Run Property Search" }),
    );
    await waitFor(() =>
      expect(
        mock.mock.calls.some(([path]) => path.endsWith("/properties/search")),
      ).toBe(true),
    );
  });
  it("validates safety cap inputs and resets unsaved changes", async () => {
    render(<PropertyRadarView detail />);
    await open("Limits & Usage");
    const phone = screen.getByLabelText("Phone unlocks") as HTMLInputElement;
    fireEvent.change(phone, { target: { value: "2451" } });
    expect(phone.checkValidity()).toBe(false);
    fireEvent.click(
      screen.getByRole("button", { name: "Reset Unsaved Changes" }),
    );
    expect(phone.value).toBe("2450");
  });
  it("supports category selection and select-all with per-category row counts", async () => {
    render(<PropertyRadarView detail />);
    await open("Search Settings");
    fireEvent.click(screen.getByLabelText("Select all categories"));
    expect((screen.getByLabelText("FSBO") as HTMLInputElement).checked).toBe(
      true,
    );
    expect(screen.getByText(/3 categories × 20 rows = up to 60/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Expired"));
    expect(
      (screen.getByLabelText("Select all categories") as HTMLInputElement)
        .checked,
    ).toBe(false);
  });
  it("displays monitoring limit errors and prevents enabling", async () => {
    mock.mockImplementation(async (path) =>
      path.endsWith("/monitoring/validate")
        ? {
            safe: false,
            preview_id: "m",
            reason: "List exceeds 10,000; narrow geography",
            union_count: 11000,
            counts: { one: 11000 },
          }
        : path.includes("?")
          ? { items: [], total: 0 }
          : summary,
    );
    render(<PropertyRadarView detail />);
    await open("Lead Lists");
    fireEvent.click(screen.getByRole("button", { name: "Enable Monitoring" }));
    expect(
      await screen.findByText("List exceeds 10,000; narrow geography"),
    ).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
  it("renders webhook payloads as escaped text and disables external registration locally", async () => {
    mock.mockImplementation(async (path) =>
      path.includes("/events?")
        ? {
            items: [
              {
                id: 1,
                radar_id: "TEST-P1",
                trigger_type: "New Match",
                is_test: true,
                raw_payload: { text: "<script>evil()</script>" },
              },
            ],
            total: 1,
          }
        : summary,
    );
    render(<PropertyRadarView detail />);
    await open("Webhooks & Events");
    expect(await screen.findByText("TEST-P1")).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Register / Update Webhook" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(document.querySelector("pre")?.textContent).toContain(
      "<script>evil()</script>",
    );
    expect(document.querySelector("pre script")).toBeNull();
  });
  it("reports loading and fetch failures", async () => {
    mock.mockRejectedValue(new Error("Provider unavailable"));
    render(<PropertyRadarView />);
    expect(screen.getByText("Loading PropertyRadar…")).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Provider unavailable",
    );
  });
  it("prevents repeated external actions while working", async () => {
    render(<PropertyRadarView detail />);
    await open("Configuration");
    mock.mockImplementation(() => new Promise(() => {}));
    const button = screen.getByRole("button", { name: "Test Connection" });
    fireEvent.click(button);
    expect(button.hasAttribute("disabled")).toBe(true);
  });
});
