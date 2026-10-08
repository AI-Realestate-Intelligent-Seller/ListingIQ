import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { requestJson } from "@/lib/api/http-client";
import { BatchDataView } from "./batchdata-view";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({
    access_token: "test",
    user: { role: "platform_admin" },
  }),
}));
vi.mock("@/lib/api/http-client", () => ({ requestJson: vi.fn() }));
const mock = vi.mocked(requestJson);

const config = {
  configurationVersion: 1,
  enabled: false,
  billingMode: "pay_as_you_go",
  monthlySpendCap: 250,
  skipTraceSpendCap: 175,
  monthlySkipTraceLimit: 2500,
  basicPropertyUnitCost: 0.01,
  quickListUnitCost: 0.01,
  listingUnitCost: 0.1,
  contactEnrichmentUnitCost: 0.07,
  allowOverage: false,
  rowsPerCategory: 20,
  selectedCategories: ["fsbo", "vacant"],
  locations: ["Chicago, IL"],
  combination: "OR",
  propertySearchEnabled: true,
  contactEnrichmentEnabled: true,
  publicWebhookUrl: "",
  monitorNewMatchUnitCost: null,
  monitorUpdateUnitCost: null,
  monitorMonthlyFixedCost: null,
  monitorMonthlySpendCap: null,
} as const;
const summary = {
  status: "Disabled",
  enabled: false,
  connection_status: "not_tested",
  token_configured: true,
  webhook_secret_configured: true,
  last_successful_call: null,
  last_webhook: null,
  last_error: null,
  config,
  categories: [
    { key: "fsbo", label: "FSBO", supported: true },
    { key: "vacant", label: "Vacant", supported: true },
    { key: "short_sale", label: "Short Sale", supported: false },
  ],
  usage: {
    monthly_spend: 12.5,
    monthly_cap: 250,
    skip_trace_spend: 7,
    skip_trace_cap: 175,
    skip_trace_matches: 100,
    skip_trace_limit: 2500,
    allow_overage: false,
  },
  metrics: { properties: 32, runs: 2, webhooks: 4 },
  monitoring_ready: false,
  monitoring_status: "Blocked: PAYG prices not confirmed",
};

beforeEach(() => {
  mock.mockReset();
  mock.mockImplementation(async (path) => {
    if (path.includes("/products/"))
      return {
        id: "preview",
        status: "Preview",
        call_plan: {
          property_search_calls: 1,
          maximum_returned_rows: 20,
        },
        estimated_cost: 0.2,
      };
    return path.endsWith("/properties") ||
      path.endsWith("/memberships") ||
      path.endsWith("/saved-files") ||
      path.endsWith("/runs") ||
      path.endsWith("/api-calls") ||
      path.endsWith("/webhooks") ||
      path.endsWith("/audit")
      ? { items: [], total: 0, offset: 0, limit: 25 }
      : summary;
  });
});
afterEach(cleanup);

describe("Internal Console BatchData", () => {
  it("allows a property-search fetch size above twenty", async () => {
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.change(screen.getByLabelText("Results per category"), {
      target: { value: "250" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview Endpoint" }));

    await waitFor(() =>
      expect(
        mock.mock.calls.some(
          ([path, options]) =>
            path.endsWith("/products/property-search") &&
            (options?.payload as { rows_per_category?: number })
              ?.rows_per_category === 250,
        ),
      ).toBe(true),
    );
  });

  it("keeps completed Property Search results when switching products", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("search test");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    mock.mockImplementation(async (path, options) => {
      if (path.endsWith("/products/property-search")) {
        const confirmed = Boolean(
          (options?.payload as { confirmed?: boolean })?.confirmed,
        );
        return {
          id: confirmed ? "search-run" : "preview",
          status: confirmed ? "Completed" : "Preview",
          estimated_cost: 0.12,
          returned_records: confirmed ? 1 : 0,
          unique_properties: confirmed ? 1 : 0,
          duplicate_properties: 0,
          properties: confirmed
            ? [{ property_id: "provider-1", address: "Saved address" }]
            : [],
          provider_calls: [],
          call_plan: {
            property_search_calls: 1,
            maximum_returned_rows: 1,
          },
        };
      }
      if (path.startsWith("/integrations/batchdata/properties?"))
        return { items: [], total: 0, offset: 0, limit: 25 };
      return { ...summary, enabled: true };
    });
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview Endpoint" }));
    await screen.findByText("Preview");
    fireEvent.click(screen.getByRole("button", { name: "Run Endpoint" }));
    await screen.findByText("Returned provider data");
    fireEvent.click(screen.getByRole("button", { name: "Contact Enrichment" }));
    await screen.findByText("No saved properties in this API mode. Run Property Search first.");
    fireEvent.click(screen.getByRole("button", { name: "Property Search" }));
    expect(screen.getByText("Returned provider data")).toBeTruthy();
    expect(screen.getByText("Saved address")).toBeTruthy();
    prompt.mockRestore();
    confirm.mockRestore();
  });

  it("selects contacts across all pages independently of saved details and clears them", async () => {
    mock.mockImplementation(async (path) => {
      if (path.endsWith("/properties/selectable-ids"))
        return { property_ids: [7, 8] };
      if (path.startsWith("/integrations/batchdata/properties?"))
        return {
          items: [
            {
              id: 7,
              property_search_saved: true,
              table_data: { property_id: "P7" },
              stages: { details: { status: "completed" } },
            },
            {
              id: 9,
              property_search_saved: true,
              table_data: { property_id: "P9" },
              stages: { contacts: { status: "completed" } },
            },
          ],
          total: 30,
          offset: 0,
          limit: 25,
        };
      return summary;
    });
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.click(screen.getByRole("button", { name: "Contact Enrichment" }));
    await screen.findByRole("checkbox", { name: "Select property P7" });
    expect(screen.getByText(/1 saved property is already contact-enriched/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Select All" }));
    await screen.findByText(/2 selected/);
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select property P7",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select property P9",
        }) as HTMLInputElement
      ).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await screen.findByText(/0 selected/);
    expect(mock.mock.calls.some(([path]) => path.includes("/products/"))).toBe(
      false,
    );
  });

  it("previews and explicitly retrieves contacts for a saved search record", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("contact test");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    let completed = false;
    mock.mockImplementation(async (path, options) => {
      if (path.startsWith("/integrations/batchdata/properties?"))
        return {
          items: [
            {
              id: 7,
              property_search_saved: true,
              table_data: {
                property_id: "P7",
                address: "Saved Chicago Address",
                owner: "Saved Owner",
              },
              stages: completed ? { contacts: { status: "completed" } } : {},
            },
          ],
          total: 1,
          offset: 0,
          limit: 25,
        };
      if (path.endsWith("/products/contact-enrichment")) {
        const payload = options?.payload as { confirmed?: boolean };
        completed = !!payload.confirmed;
        return {
          id: completed ? "contact-run" : "preview",
          status: completed ? "Completed" : "Preview",
          estimated_cost: 0.07,
          unique_properties: 1,
          call_plan: {
            preview_hash: "contact-preview",
            maximum_returned_rows: 1,
          },
          provider_calls: [],
        };
      }
      return { ...summary, enabled: true };
    });
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.click(screen.getByRole("button", { name: "Contact Enrichment" }));
    const checkbox = await screen.findByRole("checkbox", {
      name: "Select property P7",
    });
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Get Contacts" }));
    await screen.findByText(/Status: Preview/);
    expect(prompt).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Get Contacts" }));
    expect(
      await screen.findByText(
        /Saved contact responses for 1 selected properties/,
      ),
    ).toBeTruthy();
    const calls = mock.mock.calls.filter(([path]) =>
      path.endsWith("/products/contact-enrichment"),
    );
    expect(calls).toHaveLength(2);
    expect(calls[1][1]?.payload).toEqual({
      property_ids: [7],
      confirmed: true,
      reason: "contact test",
      preview_hash: "contact-preview",
    });
    prompt.mockRestore();
    confirm.mockRestore();
  });
  it("opens a saved export in table format", async () => {
    mock.mockImplementation(async (path) => {
      if (path.endsWith("/saved-files"))
        return {
          items: [
            {
              id: 1,
              kind: "properties_csv",
              relative_path: "runs/test/properties/properties_csv.csv",
            },
          ],
          total: 1,
          offset: 0,
          limit: 25,
        };
      if (path.endsWith("/saved-files/1/content"))
        return {
          name: "properties_csv.csv",
          content: "property_id,address\nP1,123 Main St\n",
          properties: [
            { property_id: "P1", address: "123 Main St", owner: "Saved Owner" },
          ],
        };
      return summary;
    });
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "View" }));
    expect(
      await screen.findByRole("cell", { name: "Saved Owner" }),
    ).toBeTruthy();
    expect(screen.getByRole("cell", { name: "123 Main St" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Download file" })).toBeTruthy();
  });
  it("uses normal product fields and execution to display sandbox responses", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("normal test");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    mock.mockImplementation(async (path, options) => {
      if (path.includes("/products/")) {
        const confirmed = (options?.payload as { confirmed?: boolean })
          ?.confirmed;
        return {
          id: "normal-run",
          status: confirmed ? "Completed" : "Preview",
          estimated_cost: 0.2,
          returned_records: 1,
          properties: confirmed
            ? [
                {
                  property_id: "mock-property",
                  address: "123 Main St",
                  owner: "Test Owner",
                  beds: 3,
                },
              ]
            : [],
          unique_properties: 1,
          duplicate_properties: 0,
          call_plan: { property_search_calls: 1, maximum_returned_rows: 20 },
          provider_calls: confirmed
            ? [
                {
                  product: "property_search",
                  request_id: "sandbox-request",
                  request: { searchCriteria: { query: "Chicago, IL" } },
                  response: {
                    results: { properties: [{ _id: "mock-property" }] },
                  },
                },
              ]
            : [],
        };
      }
      return { ...summary, enabled: true, api_mode: "sandbox" };
    });
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    expect(
      screen.queryByRole("button", { name: "Run Sandbox Search" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.change(
      screen.getByLabelText("Locations (separate with semicolons)"),
      { target: { value: "Chicago, IL; Austin, TX" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Preview Endpoint" }));
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Run Endpoint",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: "Run Endpoint" }));
    expect(await screen.findByText("Returned mock data")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Address" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "123 Main St" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "Test Owner" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "mock-property" })).toBeTruthy();
    expect(
      mock.mock.calls.some(
        ([path, options]) =>
          path.endsWith("/products/property-search") &&
          (options?.payload as { confirmed?: boolean; locations?: string[] })
            ?.confirmed &&
          JSON.stringify(
            (options?.payload as { locations?: string[] }).locations,
          ) === JSON.stringify(["Chicago, IL", "Austin, TX"]),
      ),
    ).toBe(true);
    prompt.mockRestore();
    confirm.mockRestore();
  });
  it("keeps the BatchData card visible when its summary request fails", async () => {
    mock.mockRejectedValueOnce(new Error("Unable to reach the server."));
    render(<BatchDataView />);
    expect(
      await screen.findByRole("heading", { name: /BatchData/ }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Configure" })).toBeTruthy();
    expect(screen.getByText("Unable to reach the server.")).toBeTruthy();
  });

  it("shows the requested integration card status and open action", async () => {
    render(<BatchDataView />);
    expect(
      await screen.findByText(
        "PAYG property search, deduplicated owner enrichment, monitoring and immutable provider records.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("$12.50 / $250.00")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Configure" }).getAttribute("href"),
    ).toBe("/platform-admin/integrations/batchdata");
    expect(
      mock.mock.calls.some(([path]) => path === "/integrations/batchdata"),
    ).toBe(true);
  });

  it("uses the BatchData five-section flow without Configuration", async () => {
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    expect(screen.getByText("Free manual action")).toBeTruthy();
    expect(
      screen
        .getAllByRole("button")
        .filter((button) =>
          [
            "Connection",
            "Data",
            "Usage & Limits",
            "Activity",
            "History",
          ].includes(button.textContent || ""),
        ),
    ).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "Configuration" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    expect(screen.getByText("Paid manual action")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Contact Enrichment" }),
    ).toBeTruthy();
    expect(
      (screen.getByLabelText(/Short Sale/) as HTMLInputElement).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Preview Endpoint" }));
    await waitFor(() =>
      expect(
        mock.mock.calls.some(([path]) =>
          path.endsWith("/products/property-search"),
        ),
      ).toBe(true),
    );
  });

  it("links saved properties to brokerage distribution", async () => {
    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.click(screen.getByRole("button", { name: "Saved Properties" }));
    const link = await screen.findByRole("link", {
      name: "Distribute to Brokerages",
    });
    expect(link.getAttribute("href")).toBe(
      "/operations/integrations/distribution?mode=live",
    );
  });

  it("shows saved provider property data in the brokerage-style real estate brief", async () => {
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value() { this.setAttribute("open", ""); },
    });
    Object.defineProperty(HTMLDialogElement.prototype, "close", {
      configurable: true,
      value() { this.removeAttribute("open"); },
    });
    mock.mockImplementation(async (path) => {
      if (
        path.endsWith("/integrations/batchdata/properties") ||
        path.startsWith("/integrations/batchdata/properties?")
      ) {
        return {
          items: [{
            id: 7,
            provider: "batchdata",
            table_data: {
              property_id: "P7",
              address: "6331 S Throop St, Chicago, IL 60636",
              owner: "Maita San Agustin-urdiales",
              listing_status: "Pending",
              listing_price: 449900,
            },
          }],
          total: 1,
          offset: 0,
          limit: 25,
        };
      }
      if (
        [
          "/memberships",
          "/saved-files",
          "/runs",
          "/api-calls",
          "/webhooks",
          "/audit",
        ].some((resource) => path.includes(resource))
      )
        return { items: [], total: 0, offset: 0, limit: 25 };
      if (path.endsWith("/properties/7")) {
        return {
          data: {
            address: { street: "6331 S Throop St", city: "Chicago", state: "IL", zip: "60636" },
            owner: { fullName: "Maita San Agustin-urdiales" },
            listing: {
              price: 449900,
              status: "Pending",
              propertyType: "MULTI_FAMILY",
              maxListPrice: 579000,
              maxListPriceDate: "2025-05-02T00:00:00.000Z",
            },
            quickLists: { highEquity: true, absenteeOwner: false },
          },
          stages: {
            details: { status: "completed" },
            contacts: {
              status: "completed",
              mode: "live",
              request_id: "contact-request-7",
              data: [{
                persons: [{
                  fullName: "Maita San Agustin-urdiales",
                  age: 46,
                  phoneNumbers: [
                    { number: "3125550101", dnc: false, type: "Mobile", carrier: "Example Wireless" },
                    { number: "3125550102", dnc: true, type: "Landline" },
                  ],
                  emails: [{ email: "maita@example.com", status: "Valid" }],
                }],
              }],
            },
          },
        };
      }
      return summary;
    });

    render(<BatchDataView detail />);
    await screen.findByText("Connection status");
    fireEvent.click(screen.getByRole("button", { name: "Data" }));
    fireEvent.click(screen.getByRole("button", { name: "Saved Properties" }));
    fireEvent.click(await screen.findByRole("button", { name: "View property" }));

    expect(await screen.findByRole("dialog", { name: "Property details" })).toBeTruthy();
    expect(screen.getAllByText("$449,900").length).toBeGreaterThan(0);
    expect(screen.getByText("2025-05-02")).toBeTruthy();
    expect(screen.getByText("High equity")).toBeTruthy();
    expect(screen.queryByText(/Absentee owner/i)).toBeNull();
    expect(screen.getByText(/Owner & contact intelligence/i)).toBeTruthy();
    expect(screen.getByText("Contact enrichment: completed")).toBeTruthy();
    expect(screen.getByRole("link", { name: "3125550101" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "maita@example.com" })).toBeTruthy();
    expect(screen.getAllByText("Example Wireless").length).toBeGreaterThan(0);
    expect(screen.getByText("Complete provider contact profile")).toBeTruthy();
    expect(screen.getByText("Complete owner profile")).toBeTruthy();
    expect(screen.getAllByText("Yes").length).toBeGreaterThan(0);
    expect(screen.getAllByText("No").length).toBeGreaterThan(0);
    expect(screen.getByText("Provider evidence & stage data")).toBeTruthy();
  });
});
