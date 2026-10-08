import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchLead } from "../api/leads-api";
import type { LeadDetail } from "../types/leads.types";
import { LeadDetailDrawer } from "./lead-detail-drawer";

vi.mock("../api/leads-api", () => ({
  fetchLead: vi.fn(),
}));
vi.mock("./property-satellite-map", () => ({
  PropertySatelliteMap: ({ latitude, longitude }: { latitude: number; longitude: number }) => (
    <div title="Property satellite map" data-latitude={latitude} data-longitude={longitude} />
  ),
}));

const distributedLead: LeadDetail = {
  id: 42,
  owner_name: "Christopher Theobald; Moriah Theobald",
  phone: "+15551234567",
  phone_numbers: [
    { phone: "+15551234567", dnc: false, owner_name: "Moriah Theobald" },
    { phone: "+15557654321", dnc: true, owner_name: "Moriah Theobald" },
  ],
  property_address: "123 Main St",
  area: "Austin",
  latitude: null,
  longitude: null,
  geocoding_status: "pending",
  geocoding_provider: null,
  source: "provider_distribution",
  property_type: null,
  estimated_value: null,
  listing_price: null,
  signals: [],
  score: 36,
  outreach_reason: null,
  stage: "ready",
  last_activity_at: null,
  conversation_id: null,
  created_at: "2026-10-05T12:00:00Z",
  refreshed_at: null,
  details: {
    beds: 3,
    address_valid: true,
    phones: [
      { number: "+15551234567", dnc: false },
      { number: "+15557654321", dnc: true, carrier: "Example Wireless" },
    ],
    provider_sources: [
      { provider: "batchdata", source_record_id: "property-1" },
    ],
    provider_property_details: {
      building: { bedroomCount: 4, yearBuilt: 2024 },
      deedHistory: [{ buyers: ["THEOBALD CHRISTOPHER", "THEOBALD MORIAH"] }],
      owner: {
        fullName: "Christopher Theobald; Moriah Theobald",
        mailingAddress: { street: "123 Main St", city: "Austin", state: "TX", zip: "78701" },
      },
    },
    contacts: [{
      owner_name: "Moriah Theobald",
      name: { full: "Moriah M Garrity", akas: [{ full: "Moriah Theobald" }] },
      phones: [
        { number: "+15551234567", dnc: false, reachable: true, type: "Mobile", carrier: "Example Wireless" },
        { number: "+15557654321", dnc: true, reachable: false, type: "Land Line", carrier: "Example Telecom" },
      ],
      emails: [{ email: "moriah@example.com", status: "Valid", rank: 1 }],
      propertyOwner: true,
    }],
    contact_match_metadata: [{ matched: true, confidence: "High" }],
  },
  score_breakdown: [],
  conversation: null,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LeadDetailDrawer", () => {
  it("shows adaptive fields preserved from an uploaded spreadsheet", async () => {
    vi.mocked(fetchLead).mockResolvedValue({
      ...distributedLead,
      source: "csv_import",
      details: {
        emails: ["ana@example.com"],
        _imported_fields: {
          "Estimated Home Value": "185000",
          "Custom Motivation": "Moving soon",
        },
      },
    });

    render(
      <LeadDetailDrawer
        leadId={distributedLead.id}
        accessToken="token"
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText("Imported fields")).toBeTruthy();
    expect(screen.getByText("2 fields")).toBeTruthy();
    expect(screen.getByText("Estimated Home Value")).toBeTruthy();
    expect(screen.getByText("185000")).toBeTruthy();
    expect(screen.getByText("Custom Motivation")).toBeTruthy();
    expect(screen.getByText("Moving soon")).toBeTruthy();
    expect(screen.getByRole("link", { name: "ana@example.com" })).toBeTruthy();
  });

  it("renders nested provider-distribution details without passing objects to React", async () => {
    vi.mocked(fetchLead).mockResolvedValue(distributedLead);

    render(
      <LeadDetailDrawer
        leadId={distributedLead.id}
        accessToken="token"
        onClose={() => undefined}
      />,
    );

    expect(screen.getByRole("dialog").classList.contains("leads-detail-modal")).toBe(true);
    expect(
      (await screen.findAllByText("Christopher Theobald; Moriah Theobald")).length,
    ).toBeGreaterThan(0);
    const propertyDetailsSummary = screen.getByText("Property details");
    const propertyDetails = propertyDetailsSummary.closest("details");
    expect(propertyDetails?.open).toBe(false);
    fireEvent.click(propertyDetailsSummary);
    expect(propertyDetails?.open).toBe(true);
    expect(screen.getAllByText("Moriah Theobald").length).toBeGreaterThan(0);
    expect(screen.getByText("DNC — do not contact")).toBeTruthy();
    expect(screen.getByText("Clear")).toBeTruthy();
    expect(screen.getByRole("link", { name: "moriah@example.com" })).toBeTruthy();
    expect(screen.queryByText("Complete provider contact profile")).toBeNull();
    expect(screen.queryByText("Complete owner profile")).toBeNull();
    expect(screen.queryByText("Contact match evidence")).toBeNull();
    expect(screen.getByRole("link", { name: "(555) 123-4567" })).toBeTruthy();
    expect(screen.getByText("(555) 765-4321")).toBeTruthy();
    expect(screen.getAllByText("Example Wireless").length).toBeGreaterThan(0);
    expect(screen.getByText("batchdata")).toBeTruthy();
    expect(screen.getByText("property-1")).toBeTruthy();
    expect(screen.getByText("2024")).toBeTruthy();
    expect(screen.getByText("THEOBALD CHRISTOPHER")).toBeTruthy();
    expect(screen.getAllByText("Yes").length).toBeGreaterThan(0);
  });

  it("turns provider property data into an agent-readable listing brief", async () => {
    vi.mocked(fetchLead).mockResolvedValue({
      ...distributedLead,
      owner_name: "Maita San Agustin-urdiales",
      property_address: "6331 S Throop St, Chicago, IL 60636",
      details: {
        provider_property_details: {
          address: { city: "Chicago", state: "IL", zip: "60636", latitude: 41.778644, longitude: -87.656574 },
          ids: { apn: "20-20-106-006-0000" },
          listing: {
            bedroomCount: 9,
            bathroomCount: 6,
            daysOnMarket: 522,
            livingArea: 3366,
            maxListPrice: 579000,
            maxListPriceDate: "2025-05-02T00:00:00.000Z",
            minListPrice: 424000,
            minListPriceDate: "2021-10-21T00:00:00.000Z",
            originalListingDate: "2025-05-02T00:00:00.000Z",
            price: 449900,
            propertyType: "MULTI_FAMILY",
            soldDate: "2021-10-25T00:00:00.000Z",
            soldPrice: 435000,
            status: "Pending",
            statusCategory: "Pending",
            listingUrl: "https://www.zillow.com/example-listing/",
            taxes: [{ year: 2023, amount: 1160.47 }],
          },
          owner: { fullName: "Maita San Agustin-urdiales" },
          quickLists: {
            absenteeOwner: false,
            highEquity: true,
            listedBelowMarketPrice: true,
            noticeOfLisPendens: true,
            pendingListing: true,
          },
        },
      },
    });

    render(
      <LeadDetailDrawer
        leadId={distributedLead.id}
        accessToken="token"
        onClose={() => undefined}
      />,
    );

    expect((await screen.findAllByText("$449,900")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("2025-05-02").length).toBeGreaterThan(0);
    expect(screen.queryByText("2025-05-02T00:00:00.000Z")).toBeNull();
    expect(screen.getByText("High equity")).toBeTruthy();
    expect(screen.getByText("Below market price")).toBeTruthy();
    expect(screen.getByText("Lis pendens filed")).toBeTruthy();
    expect(screen.queryByText(/Absentee owner/i)).toBeNull();
    expect((await screen.findByTitle("Property satellite map")).getAttribute("data-latitude")).toBe("41.778644");
    expect(screen.queryByTitle("Listing preview")).toBeNull();
    expect(screen.getByText("zillow.com")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open live listing/ }).getAttribute("href")).toBe("https://www.zillow.com/example-listing/");
  });
});
