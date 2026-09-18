import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { requestJson } from "@/lib/api/http-client";
import { DealMachineView } from "./dealmachine-view";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({
    access_token: "test",
    user: { role: "platform_admin" },
  }),
}));
vi.mock("@/lib/api/http-client", () => ({ requestJson: vi.fn() }));
const mock = vi.mocked(requestJson);
const limits = {
  monthly_total_credit_cap: 5000,
  monthly_property_credit_cap: 3500,
  monthly_people_credit_cap: 1500,
  per_run_property_limit: 500,
  per_run_people_limit: 250,
  rows_per_category: 20,
  maximum_categories_per_run: 21,
  daily_request_limit: 4500,
  minimum_remaining_credit_reserve: 100,
  stop_on_warning: true,
};
const category = {
  key: "expired",
  label: "Expired",
  enabled: false,
  support_status: "unconfirmed",
  configuration: {
    enabled: false,
    support_status: "unconfirmed",
    filter_mappings: [],
    locations: [],
    selected_fields: [],
    rows_per_fetch: 20,
    sort: [],
    monitoring_frequency_minutes: 1440,
  },
  last_run_at: null,
  next_run_at: null,
  last_result_count: 0,
  new_match_count: 0,
  changed_record_count: 0,
  credits_used: 0,
};
const summary = {
  status: "Disabled",
  enabled: false,
  connection_status: "disconnected",
  credential: { configured: true, masked: "dm_••••••••1234" },
  encryption_configured: true,
  plan: "Pro",
  available_monthly_credits: 9000,
  property_credits_used: 100,
  people_credits_used: 25,
  last_tested_at: null,
  last_successful_sync_at: null,
  next_scheduled_sync_at: null,
  last_error: null,
  settings: {
    enabled: false,
    limits,
    selected_fields: [],
    automatic_enrichment: false,
    ownership_change_reenrichment: false,
    scheduler_enabled: false,
    scheduler_interval_minutes: 1440,
  },
  categories: [category],
  counts: { properties: 0, runs: 0, queued: 0 },
  export_running: false,
};

beforeEach(() => {
  mock.mockReset();
  mock.mockImplementation(async (path) =>
    path.endsWith("/history") ? { items: [], total: 0 } : summary,
  );
});
afterEach(cleanup);

describe("DealMachine Internal Console", () => {
  it("renders status, masked credential, usage and Configure route", async () => {
    render(<DealMachineView />);
    expect(await screen.findByText("dm_••••••••1234")).toBeTruthy();
    expect(screen.getByText("100 / 25")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Configure" }).getAttribute("href"),
    ).toBe("/operations/integrations/dealmachine");
  });

  it("uses the shared six-section flow with endpoint subtabs", async () => {
    render(<DealMachineView detail />);
    await screen.findByText("Test Connection");
    expect(
      screen.getByText("1 step left before DealMachine is fully set up."),
    ).toBeTruthy();
    expect(screen.getByText("API key")).toBeTruthy();
    expect(screen.getByText("Credential protection")).toBeTruthy();
    expect(screen.getByText("Connection verified")).toBeTruthy();
    expect(screen.getByText("Provider account loaded")).toBeTruthy();
    expect(screen.getByText("Manual execution mode")).toBeTruthy();
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
    expect(screen.getByRole("button", { name: "Cost Estimate" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Contact Enrichment" }),
    ).toBeTruthy();
  });

  it("keeps the advanced request JSON preview collapsed by default", async () => {
    render(<DealMachineView detail />);
    await screen.findByText("Test Connection");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    const toggle = screen.getByText("Advanced request JSON (optional)", {
      selector: "summary",
    });
    const preview = toggle.parentElement as HTMLDetailsElement;
    expect(preview.open).toBe(false);
    fireEvent.click(toggle);
    expect(preview.open).toBe(true);
  });

  it("shows plain-language credit limits without an advanced JSON editor", async () => {
    render(<DealMachineView detail />);
    await screen.findByText("Test Connection");
    fireEvent.click(screen.getByRole("button", { name: "Usage & Limits" }));
    expect(screen.getByText("Spending safety limits")).toBeTruthy();
    expect(screen.getByLabelText("Total credits")).toBeTruthy();
    expect(screen.getByLabelText("Properties per action")).toBeTruthy();
    expect(screen.getByLabelText("Credits to keep in reserve")).toBeTruthy();
    expect(screen.queryByText("Advanced settings")).toBeNull();
    expect(screen.queryByText("Advanced request JSON (optional)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Total credits"), {
      target: { value: "6000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Limits" }));
    await waitFor(() =>
      expect(mock).toHaveBeenCalledWith(
        "/integrations/dealmachine/settings",
        expect.objectContaining({
          method: "PUT",
          payload: expect.objectContaining({
            limits: expect.objectContaining({ monthly_total_credit_cap: 6000 }),
          }),
        }),
      ),
    );
  });

  it("keeps unconfirmed categories unavailable until live filters are mapped", async () => {
    render(<DealMachineView detail />);
    await screen.findByText("Test Connection");
    fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    expect(screen.queryByText("Safe default")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Load DealMachine Filters" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("Category mappings (0 of 1 ready)"));
    expect(
      (screen.getByLabelText("Include in Bulk Search") as HTMLInputElement)
        .disabled,
    ).toBe(true);
  });

  it("requires explicit confirmation and adds the audit reason to paid search", async () => {
    const enabled = {
      ...summary,
      status: "Ready",
      connection_status: "connected",
    };
    mock.mockImplementation(async (path) =>
      path.endsWith("/history")
        ? { items: [], total: 0 }
        : path.endsWith("/properties/search")
          ? { data: [] }
          : enabled,
    );
    vi.spyOn(window, "prompt").mockReturnValue("Approved by operations");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DealMachineView detail />);
    await screen.findByText("Test Connection");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.click(screen.getByRole("button", { name: "Search Properties" }));
    await waitFor(() =>
      expect(mock).toHaveBeenCalledWith(
        expect.stringContaining("/properties/search"),
        expect.objectContaining({
          payload: expect.objectContaining({
            confirmed: true,
            reason: "Approved by operations",
          }),
        }),
      ),
    );
  });

  it("routes property details and contact enrichment through separate actions", async () => {
    const enabled = {
      ...summary,
      status: "Ready",
      connection_status: "connected",
    };
    mock.mockResolvedValue(enabled);
    vi.spyOn(window, "prompt").mockReturnValue("Approved by operations");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DealMachineView detail />);
    await screen.findByText("Test Connection");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.click(screen.getByRole("button", { name: "Property Details" }));
    fireEvent.change(screen.getByLabelText("DealMachine property IDs"), {
      target: { value: "DM1, DM2" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Load Property Details" }),
    );
    await waitFor(() =>
      expect(mock).toHaveBeenCalledWith(
        "/integrations/dealmachine/properties/details",
        expect.objectContaining({
          method: "POST",
          payload: expect.objectContaining({ dm_property_ids: ["DM1", "DM2"] }),
        }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Contact Enrichment" }));
    fireEvent.change(screen.getByLabelText("DealMachine property IDs"), {
      target: { value: "DM1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Enrich Contacts" }));
    await waitFor(() =>
      expect(mock).toHaveBeenCalledWith(
        "/integrations/dealmachine/contacts/enrich",
        expect.objectContaining({
          method: "POST",
          payload: expect.objectContaining({ contact_audience: "owners" }),
        }),
      ),
    );
  });
});
