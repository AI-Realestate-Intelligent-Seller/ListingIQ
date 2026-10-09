import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LeadPool } from "./lead-pool";
import { fetchLeadPool } from "../api/leads-api";
import {
  getByCity,
  getByZip,
  getCities,
  getFilterData,
  getZipCodes,
  resolveLocation,
} from "@/lib/location/getstates";


vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({ access_token: "test-token" }),
}));
vi.mock("@/features/auth/lib/session-guard", () => ({ endSession: vi.fn() }));
vi.mock("@/features/dashboard/components/notification-bell", () => ({ NotificationBell: () => null }));
vi.mock("@/features/campaigns/api/campaigns-api", () => ({ createCampaignDraft: vi.fn() }));
vi.mock("../api/leads-api", () => ({
  deleteLeads: vi.fn(),
  fetchLeadPool: vi.fn(),
  importLeads: vi.fn(),
  previewImport: vi.fn(),
  repreviewImport: vi.fn(),
}));
vi.mock("@/lib/location/getstates", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/location/getstates")>();
  return {
    ...original,
    getFilterData: vi.fn(),
    getZipCodes: vi.fn(),
    getAllZipCodes: vi.fn(() => Promise.resolve([])),
    getCities: vi.fn(),
    getByCity: vi.fn(() => Promise.resolve({ states: [], zipcodes: [] })),
    getByZip: vi.fn(() => Promise.resolve({ states: [], cities: [], zipcodes: [] })),
    resolveLocation: vi.fn(),
    getBoundary: vi.fn(() => Promise.resolve(null)),
  };
});

const emptyPool = {
  leads: [],
  map: { mappable_count: 0, pending_count: 0, failed_count: 0 },
  facets: { total: 0, signals: {}, stages: {} },
  locations: { states: [], cities: [], zips: [] },
  signal_catalog: [],
  stage_catalog: [],
};

const californiaCities = [
  ...Array.from({ length: 55 }, (_, index) => `A City ${String(index + 1).padStart(2, "0")}`),
  "Bakersfield",
];
const californiaZips = [
  ...Array.from({ length: 55 }, (_, index) => String(90001 + index)),
  "95555",
];

beforeEach(() => {
  vi.mocked(fetchLeadPool).mockResolvedValue(emptyPool);
  vi.mocked(getFilterData).mockResolvedValue({
    states: ["Arizona,AZ", "California,CA", "Illinois,IL", "Pennsylvania,PA"],
    cities: ["Alpine", "Chicago", "Erie", "Pittsburgh"],
  });
  vi.mocked(getZipCodes).mockImplementation(async (state) =>
    state === "Arizona"
      ? [{ zip: "85920", city: "Alpine", state: "Arizona", state_code: "AZ", latitude: "33.85995", longitude: "-109.1753" }]
      : state === "California"
      ? californiaZips.map((zip, index) => ({
          zip,
          city: californiaCities[index] ?? "Bakersfield",
          state: "California",
          state_code: "CA",
          latitude: String(32.6 + index * 0.1),
          longitude: String(-124.2 + index * 0.1),
        }))
      : state === "Pennsylvania"
      ? [
          { zip: "15201", city: "Pittsburgh", state: "Pennsylvania", state_code: "PA", latitude: "40.47", longitude: "-79.96" },
          { zip: "16501", city: "Erie", state: "Pennsylvania", state_code: "PA", latitude: "42.12", longitude: "-80.08" },
          { zip: "19103", city: "Philadelphia", state: "Pennsylvania", state_code: "PA", latitude: "39.95", longitude: "-75.17" },
        ]
      : [{ zip: "60601", city: "Chicago", state: "Illinois", state_code: "IL", latitude: "41.88", longitude: "-87.62" }],
  );
  vi.mocked(getCities).mockImplementation(async ({ state }) =>
    state === "Arizona"
      ? ["Alpine"]
      : state === "California"
      ? californiaCities
      : state === "Pennsylvania"
        ? ["Erie", "Pittsburgh"]
        : ["Chicago"],
  );
  vi.mocked(resolveLocation).mockResolvedValue({
    found: true,
    data: [{
      city: "Alpine",
      state: "Arizona",
      state_code: "AZ",
      zip: "85920",
      county: "Apache",
      latitude: 33.85995,
      longitude: -109.1753,
    }],
  });
  vi.mocked(getByZip).mockImplementation(async (zip) =>
    zip === "85920"
      ? {
          states: ["Arizona,AZ"],
          cities: ["Alpine"],
          zipcodes: [{
            zip: "85920",
            city: "Alpine",
            state: "Arizona",
            state_code: "AZ",
            latitude: "33.85995",
            longitude: "-109.1753",
          }],
        }
      : { states: [], cities: [], zipcodes: [] },
  );
  vi.mocked(getByCity).mockImplementation(async (city) =>
    city === "Des Plaines"
      ? {
          states: ["Illinois,IL"],
          zipcodes: [
            { zip: "60016", city: "Des Plaines", state: "Illinois", state_code: "IL", latitude: "42.05232", longitude: "-87.8899" },
            { zip: "60018", city: "Des Plaines", state: "Illinois", state_code: "IL", latitude: "41.99629", longitude: "-87.89846" },
          ],
        }
      : city === "Greenleaf"
        ? {
            states: ["Idaho,ID", "Kansas,KS", "Wisconsin,WI"],
            zipcodes: [
              { zip: "83626", city: "Greenleaf", state: "Idaho", state_code: "ID", latitude: "43.66876", longitude: "-116.82815" },
              { zip: "66943", city: "Greenleaf", state: "Kansas", state_code: "KS", latitude: "39.67209", longitude: "-96.97313" },
              { zip: "54126", city: "Greenleaf", state: "Wisconsin", state_code: "WI", latitude: "44.28925", longitude: "-88.03218" },
            ],
          }
      : { states: [], zipcodes: [] },
  );
});

afterEach(() => cleanup());

describe("LeadPool dependent location filters", () => {
  it("shows the database query loader until the lead pool response arrives", async () => {
    let finishLoad: ((value: typeof emptyPool) => void) | undefined;
    vi.mocked(fetchLeadPool).mockImplementationOnce(
      () => new Promise((resolve) => {
        finishLoad = resolve;
      }),
    );

    render(<LeadPool />);

    expect(
      await screen.findByRole("status", { name: "Loading lead pool" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("progressbar", { name: "Lead pool database query" }),
    ).toBeTruthy();

    await waitFor(() => expect(fetchLeadPool).toHaveBeenCalled());
    finishLoad?.(emptyPool);

    await waitFor(() => {
      expect(screen.queryByRole("status", { name: "Loading lead pool" })).toBeNull();
    });
  });

  it("opens the map when a mapped lead is clicked in the table", async () => {
    vi.mocked(fetchLeadPool).mockResolvedValueOnce({
      ...emptyPool,
      leads: [{
        id: 71,
        owner_name: "Mapped Owner",
        phone: "+13125550171",
        phone_numbers: [{ phone: "+13125550171", dnc: false }],
        property_address: "123 Main St, Chicago, IL 60601",
        area: "Chicago",
        latitude: 41.88,
        longitude: -87.63,
        geocoding_status: "success",
        geocoding_provider: "nominatim",
        source: "provider_distribution",
        property_type: "Single family",
        estimated_value: 350000,
        listing_price: null,
        signals: [],
        score: 70,
        outreach_reason: null,
        stage: "ready",
        last_activity_at: null,
        conversation_id: null,
        created_at: null,
      }],
      map: { mappable_count: 1, pending_count: 0, failed_count: 0 },
      facets: { total: 1, signals: {}, stages: { ready: 1 } },
    });

    render(<LeadPool />);
    fireEvent.click(await screen.findByRole("button", { name: "Mapped Owner" }));
    expect(screen.getByRole("button", { name: "Hide map" })).toBeTruthy();
  });

  it("shows only the selected state's cities and ZIP codes after the state changes", async () => {
    render(<LeadPool />);

    const stateInput = await screen.findByRole("combobox", { name: "Filter by state" });
    fireEvent.focus(stateInput);
    fireEvent.change(stateInput, { target: { value: "Penn" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Pennsylvania,PA" }));

    await waitFor(() => {
      expect(getCities).toHaveBeenCalledWith({ state: "Pennsylvania", zipcode: undefined });
      expect(getZipCodes).toHaveBeenCalledWith("Pennsylvania");
    });

    const cityInput = screen.getByRole("combobox", { name: "Filter by city" });
    fireEvent.focus(cityInput);
    expect(await screen.findByRole("option", { name: "Pittsburgh" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Erie" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Chicago" })).toBeNull();

    fireEvent.blur(cityInput);
    const zipInput = screen.getByRole("combobox", { name: "Filter by ZIP code" });
    fireEvent.focus(zipInput);
    expect(await screen.findByRole("option", { name: "15201" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "16501" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "60601" })).toBeNull();
  });

  it("shows California cities and ZIP codes beyond the first 50 results", async () => {
    render(<LeadPool />);

    const stateInput = await screen.findByRole("combobox", { name: "Filter by state" });
    fireEvent.focus(stateInput);
    fireEvent.change(stateInput, { target: { value: "California" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: "California,CA" }));

    await waitFor(() => {
      expect(getCities).toHaveBeenCalledWith({ state: "California", zipcode: undefined });
      expect(getZipCodes).toHaveBeenCalledWith("California");
    });

    const cityInput = screen.getByRole("combobox", { name: "Filter by city" });
    fireEvent.focus(cityInput);
    expect(await screen.findByRole("option", { name: "Bakersfield" })).toBeTruthy();

    fireEvent.blur(cityInput);
    const zipInput = screen.getByRole("combobox", { name: "Filter by ZIP code" });
    fireEvent.focus(zipInput);
    expect(await screen.findByRole("option", { name: "95555" })).toBeTruthy();
  });

  it("adds multiple ZIP codes and sends all of them to the lead filter", async () => {
    render(<LeadPool />);

    const stateInput = await screen.findByRole("combobox", { name: "Filter by state" });
    fireEvent.focus(stateInput);
    fireEvent.change(stateInput, { target: { value: "Penn" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Pennsylvania,PA" }));

    await waitFor(() => expect(getZipCodes).toHaveBeenCalledWith("Pennsylvania"));

    const zipInput = screen.getByRole("combobox", { name: "Filter by ZIP code" });
    fireEvent.focus(zipInput);
    fireEvent.mouseDown(await screen.findByRole("option", { name: "15201" }));
    expect(await screen.findByRole("button", { name: "Remove 15201" })).toBeTruthy();

    fireEvent.focus(zipInput);
    fireEvent.mouseDown(await screen.findByRole("option", { name: "16501" }));
    expect(await screen.findByRole("button", { name: "Remove 16501" })).toBeTruthy();

    fireEvent.focus(zipInput);
    expect(screen.queryByRole("option", { name: "15201" })).toBeNull();
    expect(screen.queryByRole("option", { name: "16501" })).toBeNull();
    expect(await screen.findByRole("option", { name: "19103" })).toBeTruthy();

    await waitFor(() => {
      const queries = vi.mocked(fetchLeadPool).mock.calls.map(([query]) => query);
      expect(queries.some((query) =>
        query.zips?.length === 2 &&
        query.zips.includes("15201") &&
        query.zips.includes("16501")
      )).toBe(true);
    });
  });

  it("resolves Alpine within Arizona to ZIP 85920 and its map-area record", async () => {
    render(<LeadPool />);

    const stateInput = await screen.findByRole("combobox", { name: "Filter by state" });
    fireEvent.focus(stateInput);
    fireEvent.change(stateInput, { target: { value: "Arizona" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Arizona,AZ" }));

    await waitFor(() => expect(getCities).toHaveBeenCalledWith({
      state: "Arizona",
      zipcode: undefined,
    }));

    const cityInput = screen.getByRole("combobox", { name: "Filter by city" });
    fireEvent.focus(cityInput);
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Alpine" }));

    await waitFor(() => {
      expect(resolveLocation).toHaveBeenCalledWith("Alpine", "Arizona");
      expect(getByZip).toHaveBeenCalledWith("85920");
      const queries = vi.mocked(fetchLeadPool).mock.calls.map(([query]) => query);
      expect(queries.some((query) =>
        query.states?.includes("AZ") &&
        query.zips?.includes("85920") &&
        (!query.cities || query.cities.length === 0)
      )).toBe(true);
    });
  });

  it("keeps Des Plaines selected and filters by its canonical Illinois city key", async () => {
    vi.mocked(getFilterData).mockResolvedValue({
      states: ["Illinois,IL"],
      cities: ["Des Plaines"],
    });
    vi.mocked(getCities).mockResolvedValue(["Des Plaines"]);
    vi.mocked(getZipCodes).mockResolvedValue([
      { zip: "60016", city: "Des Plaines", state: "Illinois", state_code: "IL", latitude: "42.05232", longitude: "-87.8899" },
      { zip: "60018", city: "Des Plaines", state: "Illinois", state_code: "IL", latitude: "41.99629", longitude: "-87.89846" },
    ]);

    render(<LeadPool />);

    const stateInput = await screen.findByRole("combobox", { name: "Filter by state" });
    fireEvent.focus(stateInput);
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Illinois,IL" }));

    const cityInput = screen.getByRole("combobox", { name: "Filter by city" });
    await waitFor(() => expect(getCities).toHaveBeenCalledWith({
      state: "Illinois",
      zipcode: undefined,
    }));
    fireEvent.focus(cityInput);
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Des Plaines" }));

    await waitFor(() => {
      expect((cityInput as HTMLInputElement).value).toBe("Des Plaines");
      const queries = vi.mocked(fetchLeadPool).mock.calls.map(([query]) => query);
      expect(queries.some((query) =>
        query.states?.includes("IL") &&
        query.cities?.includes("des plaines|IL") &&
        (!query.zips || query.zips.length === 0)
      )).toBe(true);
    });
    expect(resolveLocation).not.toHaveBeenCalledWith("Des Plaines", "Illinois");
  });

  it("keeps Greenleaf selected and filters all matching states by city instead of ZIP only", async () => {
    vi.mocked(getFilterData).mockResolvedValue({
      states: ["Idaho,ID", "Kansas,KS", "Wisconsin,WI"],
      cities: ["Greenleaf"],
    });

    render(<LeadPool />);

    const cityInput = await screen.findByRole("combobox", { name: "Filter by city" });
    await waitFor(() => expect(getFilterData).toHaveBeenCalled());
    fireEvent.focus(cityInput);
    fireEvent.mouseDown(await screen.findByRole("option", { name: "Greenleaf" }));

    await waitFor(() => {
      expect((cityInput as HTMLInputElement).value).toBe("Greenleaf");
      const queries = vi.mocked(fetchLeadPool).mock.calls.map(([query]) => query);
      expect(queries.some((query) =>
        query.cities?.includes("greenleaf|ID") &&
        query.cities.includes("greenleaf|KS") &&
        query.cities.includes("greenleaf|WI") &&
        (!query.zips || query.zips.length === 0)
      )).toBe(true);
    });
    expect(resolveLocation).not.toHaveBeenCalledWith("Greenleaf", undefined);
  });
});
