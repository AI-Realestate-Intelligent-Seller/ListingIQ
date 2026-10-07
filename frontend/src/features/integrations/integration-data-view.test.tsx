import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { requestJson } from "@/lib/api/http-client";
import { IntegrationDataView } from "./integration-data-view";

vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({
    access_token: "test",
    user: { role: "platform_admin" },
  }),
}));
vi.mock("@/lib/api/http-client", () => ({ requestJson: vi.fn() }));
const mock = vi.mocked(requestJson);
const summary = {
  providers: [
    { key: "batchdata", name: "BatchData", count: 1 },
    { key: "propertyradar", name: "PropertyRadar", count: 1 },
    { key: "dealmachine", name: "DealMachine", count: 0 },
  ],
  combined: 2,
  unassigned: 2,
  categories: ["fsbo", "vacant"],
  lead_statuses: ["ready", "needs_review", "dnc"],
  brokerages: [
    { id: "A", name: "Brokerage A", recipient_name: "Head A" },
    { id: "B", name: "Brokerage B", recipient_name: "Head B" },
  ],
};
const plan = {
  preview_hash: "allocation-hash",
  allocated: 2,
  remaining: 0,
  excluded: 0,
  allocations: [
    {
      property_id: 1,
      address: "123 Main St",
      categories: ["fsbo"],
      lead_status: "ready",
      brokerage_name: "Brokerage A",
    },
    {
      property_id: 2,
      address: "124 Main St",
      categories: ["vacant"],
      lead_status: "needs_review",
      brokerage_name: "Brokerage B",
    },
  ],
  summaries: [
    { brokerage_name: "Brokerage A", requested: 1, allocated: 1, shortfall: 0 },
    { brokerage_name: "Brokerage B", requested: 1, allocated: 1, shortfall: 0 },
  ],
};

beforeEach(() => {
  mock.mockReset();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Integration data preparation and distribution", () => {
  it("shows a combined-property issue and opens its saved detail", async () => {
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value() {
        this.setAttribute("open", "");
      },
    });
    Object.defineProperty(HTMLDialogElement.prototype, "close", {
      configurable: true,
      value() {
        this.removeAttribute("open");
      },
    });
    mock.mockImplementation(async (path) => {
      if (path.includes("/summary")) return summary;
      if (path.startsWith("/platform-admin/integrations/data/combined?")) {
        return {
          items: [
            {
              id: 180,
              address: "1923 N Damen Ave Apt 3, Chicago, IL, 60647",
              owner: "Bryan West; Rachel Zigler",
              categories: ["expired"],
              lead_status: "dnc",
              distribution_issue: "All 2 phone numbers are marked DNC",
            },
          ],
          total: 1,
        };
      }
      if (path.endsWith("/combined/180")) {
        return {
          data: {
            _id: "provider-180",
            address: {
              street: "1923 N Damen Ave Apt 3",
              city: "Chicago",
              state: "IL",
              zip: "60647",
            },
            owner: { fullName: "Bryan West; Rachel Zigler" },
          },
          saved: { id: 180, lead_status: "dnc" },
          stages: {},
        };
      }
      if (path.includes("/sources/")) return { items: [], total: 0 };
      return { items: [], total: 0 };
    });

    render(<IntegrationDataView distribution initialMode="live" />);

    expect(
      await screen.findByRole("cell", {
        name: "All 2 phone numbers are marked DNC",
      }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "View property" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Property details",
    });
    expect(
      within(dialog).getByRole("heading", {
        name: "1923 N Damen Ave Apt 3, Chicago, IL, 60647",
      }),
    ).toBeTruthy();
    expect(
      within(dialog).getAllByText(/Bryan West; Rachel Zigler/).length,
    ).toBeGreaterThan(0);
    expect(
      mock.mock.calls.some(([path]) => path.endsWith("/combined/180")),
    ).toBe(true);
  });

  it("shows saved provider tables and combines only on an explicit click", async () => {
    let combined = false;
    mock.mockImplementation(async (path) => {
      if (path.includes("/summary"))
        return {
          ...summary,
          combined: combined ? 2 : 0,
          unassigned: combined ? 2 : 0,
        };
      if (path.includes("/sources/"))
        return {
          items: path.includes("batchdata")
            ? [
                {
                  source_id: "BD1",
                  source_record_id: 7,
                  data: {
                    address: "Saved source address",
                    owner: "Saved source owner",
                  },
                  categories: ["fsbo"],
                  lead_status: "needs_review",
                },
              ]
            : [],
          total: path.includes("batchdata") ? 1 : 0,
        };
      if (path.endsWith("/combine")) {
        combined = true;
        return {
          source_records: 3,
          unique_properties: 2,
          duplicates_removed: 1,
        };
      }
      return { items: [], total: 0 };
    });
    render(<IntegrationDataView />);
    await screen.findByRole("cell", { name: "Saved source address" });
    expect(screen.getByRole("heading", { name: "BatchData" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "PropertyRadar" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "DealMachine" })).toBeTruthy();
    const batchSection = screen
      .getByRole("heading", { name: "BatchData" })
      .closest("section")!;
    expect(
      within(batchSection)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent),
    ).toEqual([
      "Property ID",
      "Address",
      "Owner",
      "Type",
      "Beds",
      "Baths",
      "Area (sq ft)",
      "Estimated value",
      "Equity %",
      "Listing status",
      "Listing price",
      "Details",
    ]);
    fireEvent.click(
      screen.getByRole("button", { name: "Collapse BatchData data" }),
    );
    expect(
      screen.queryByRole("cell", { name: "Saved source address" }),
    ).toBeNull();
    expect(within(batchSection).getByText("1 saved properties")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Expand BatchData data" }),
    );
    await screen.findByRole("cell", { name: "Saved source address" });
    expect(
      mock.mock.calls.every(([, options]) => options?.method === "GET"),
    ).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: "Combine Provider Data" }),
    );
    await screen.findByText(/Combined 3 source records into 2 properties/);
    expect(
      mock.mock.calls.find(([path]) => path.endsWith("/combine"))?.[1]?.payload,
    ).toEqual({ mode: "sandbox" });
    expect(
      mock.mock.calls.some(([path]) => path.includes("distribution/execute")),
    ).toBe(false);
  });

  it("requires a current preview and confirmation before distributing multiple divisions", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    let executed = false;
    mock.mockImplementation(async (path) => {
      if (path.includes("/summary"))
        return { ...summary, unassigned: executed ? 0 : 2 };
      if (path.endsWith("/distribution/preview")) return plan;
      if (path.endsWith("/distribution/execute")) {
        executed = true;
        return {
          run_id: "test-allocation",
          mode: "sandbox",
          allocated: 2,
          leads_created: 0,
        };
      }
      return { items: [], total: 0 };
    });
    render(<IntegrationDataView distribution />);
    await screen.findByLabelText("Brokerage 1");
    fireEvent.change(screen.getByLabelText("Brokerage 1"), {
      target: { value: "A" },
    });
    fireEvent.change(screen.getByLabelText("Quantity 1"), {
      target: { value: "1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add division" }));
    fireEvent.change(screen.getByLabelText("Brokerage 2"), {
      target: { value: "B" },
    });
    fireEvent.change(screen.getByLabelText("Quantity 2"), {
      target: { value: "1" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Preview Distribution" }),
    );
    await screen.findByRole("heading", { name: "Distribution preview" });
    expect(
      mock.mock.calls.some(([path]) => path.endsWith("/distribution/execute")),
    ).toBe(false);
    expect(
      (
        screen.getByRole("button", {
          name: "Confirm Test Distribution",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("Quantity 2"), {
      target: { value: "2" },
    });
    expect(
      screen.queryByRole("heading", { name: "Distribution preview" }),
    ).toBeNull();
    fireEvent.change(screen.getByLabelText("Quantity 2"), {
      target: { value: "1" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Preview Distribution" }),
    );
    await screen.findByLabelText("Distribution reason");
    fireEvent.change(screen.getByLabelText("Distribution reason"), {
      target: { value: "test category allocation" },
    });
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Confirm Test Distribution",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm Test Distribution" }),
    );
    await screen.findByText(
      /Test distribution recorded: 2 allocated, 0 brokerage leads created/,
    );
    expect(confirm).toHaveBeenCalledOnce();
    expect(
      mock.mock.calls.find(([path]) =>
        path.endsWith("/distribution/execute"),
      )?.[1]?.payload,
    ).toEqual({
      mode: "sandbox",
      rules: [
        { brokerage_id: "A", quantity: 1, categories: [], lead_statuses: [] },
        { brokerage_id: "B", quantity: 1, categories: [], lead_statuses: [] },
      ],
      confirmed: true,
      preview_hash: "allocation-hash",
      reason: "test category allocation",
    });
  });
});
