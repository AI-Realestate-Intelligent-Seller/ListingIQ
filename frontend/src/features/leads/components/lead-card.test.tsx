import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Lead } from "../types/leads.types";
import { LeadCard } from "./lead-card";
import { toLeadCardLead } from "./lead-card-adapter";

afterEach(cleanup);

function poolLead(overrides: Partial<Lead> = {}): Lead {
  return {
    id: 7,
    owner_name: "Dana Reyes",
    phone: null,
    phone_numbers: [],
    property_address: "123 Main St, Chicago, IL, 60601",
    area: "Chicago",
    latitude: null,
    longitude: null,
    geocoding_status: "pending",
    geocoding_provider: null,
    source: "csv",
    property_type: null,
    estimated_value: "410000",
    listing_price: 389000,
    signals: [],
    score: 70,
    outreach_reason: null,
    stage: "ready",
    last_activity_at: null,
    conversation_id: null,
    created_at: "2026-10-01T12:00:00",
    ...overrides,
  };
}

/** Shaped like the 6331 S Throop St lead: fetched for pre-foreclosure, contact found by skip trace. */
const providerLead = poolLead({
  owner_name: "Maita San",
  property_address: "6331 S Throop St, Chicago, IL, 60636",
  estimated_value: 570000,
  listing_price: 449900,
  signals: [{ key: "pre_foreclosure", label: "Pre-Foreclosure" }],
  mls_number: "12349610",
  listing_status: "Pending",
  home_type: "MULTI_FAMILY",
  beds: 9,
  baths: 6,
  living_area_sqft: 3366,
  equity_percent: 25.1,
  opportunity_flags: ["highEquity", "preforeclosure", "samePropertyAndMailingAddress", "noticeOfLisPendens"],
  phone_numbers: [
    { phone: "+13125550101", dnc: false, type: "Mobile", owner_name: "Lajuan Freeman" },
    { phone: "+13125550102", dnc: true, type: "Mobile", owner_name: "Lajuan Freeman" },
    { phone: "+13125550103", dnc: false, type: "Land Line", owner_name: "Lajuan Freeman" },
    { phone: "+13125550104", dnc: false, owner_name: "Lajuan Freeman" },
  ],
  emails: [{ email: "lajuan@example.com", owner_name: "Lajuan Freeman" }],
});

describe("toLeadCardLead", () => {
  it("carries the provider facts, MLS ID and every stand-out signal", () => {
    const card = toLeadCardLead(providerLead);

    expect(card).toMatchObject({
      street: "6331 S Throop St",
      city: "Chicago",
      state: "IL",
      zip: "60636",
      primarySignal: { key: "pre_foreclosure", label: "Pre-Foreclosure" },
      listingStatus: "Pending",
      estimatedValue: 570000,
      listPrice: 449900,
      equity: "High",
      mlsNumber: "12349610",
      beds: 9,
      baths: 6,
      livingAreaSqft: 3366,
      homeType: "Multi family",
    });
    // "Pre-foreclosure" from the provider is the same chip as the lead's own signal.
    expect(card.signals?.map((s) => s.label)).toEqual([
      "Pre-Foreclosure",
      "High equity",
      "Same Property And Mailing Address",
      "Lis pendens filed",
    ]);
  });

  it("puts each person's numbers and emails together, owner first", () => {
    expect(toLeadCardLead(providerLead).owners).toEqual([
      { name: "Maita San", role: "Owner", phones: [], emails: [] },
      {
        name: "Lajuan Freeman",
        role: "Contact",
        emails: ["lajuan@example.com"],
        phones: [
          { number: "(312) 555-0101", type: "Mobile", dnc: false },
          { number: "(312) 555-0102", type: "Mobile", dnc: true },
          { number: "(312) 555-0103", type: "Land Line", dnc: false },
          { number: "(312) 555-0104", type: undefined, dnc: false },
        ],
      },
    ]);
  });

  it("shows equity exactly as BatchData classifies it", () => {
    const level = (overrides: Partial<Lead>) => toLeadCardLead(poolLead(overrides)).equity;

    expect(level({ opportunity_flags: ["highEquity"], equity_percent: 4 })).toBe("High");
    expect(level({ opportunity_flags: ["lowEquity"], equity_percent: 60 })).toBe("Low");
    // No BatchData classification: a percentage alone does not set a level.
    expect(level({ opportunity_flags: ["unknownEquity"], equity_percent: 30 })).toBeUndefined();
    expect(level({})).toBeUndefined();
  });

  it("leaves facts the pool does not have undefined", () => {
    const card = toLeadCardLead(poolLead());
    expect(card.mlsNumber).toBeUndefined();
    expect(card.beds).toBeNull();
    expect(card.homeType).toBeUndefined();
    expect(card.primarySignal).toBeUndefined();
    expect(card.signals).toEqual([]);
  });

  it("drops an unnamed owner with no contacts", () => {
    expect(toLeadCardLead(poolLead({ owner_name: null })).owners).toEqual([]);
  });
});

describe("LeadCard with pool leads", () => {
  it("shows the fetch signal, est. value, MLS ID, home info and signal chips", () => {
    render(<LeadCard lead={toLeadCardLead(providerLead)} />);

    expect(screen.getAllByText("Pre-Foreclosure").length).toBeGreaterThan(0);
    expect(screen.getByText("$570,000")).toBeTruthy();
    expect(screen.getByText("12349610")).toBeTruthy();
    expect(screen.getByText("9 Beds")).toBeTruthy();
    expect(screen.getByText("6 Baths")).toBeTruthy();
    expect(screen.getByText("3,366 Sq ft")).toBeTruthy();
    expect(screen.getByText("Multi family")).toBeTruthy();
    const chips = within(screen.getByRole("list", { name: "Why this lead stands out" }));
    expect(chips.getByText("High equity")).toBeTruthy();
    expect(chips.getByText("Lis pendens filed")).toBeTruthy();
    // Stage and assignment are not on the card.
    expect(screen.queryByText("Unassigned")).toBeNull();
    expect(screen.queryByText(/ready/i)).toBeNull();
  });

  it("gives each person one column, with +N more under that person's list", () => {
    const lead = toLeadCardLead(providerLead);
    render(<LeadCard lead={lead} />);

    const lajuan = screen.getByText("Lajuan Freeman").closest("div.min-w-0.flex-col.gap-1") as HTMLElement;
    expect(within(lajuan).getAllByText(/^\(312\) 555-010\d$/)).toHaveLength(3);
    fireEvent.click(within(lajuan).getByRole("button", { name: "+1 more" }));
    expect(within(lajuan).getAllByText(/^\(312\) 555-010\d$/)).toHaveLength(4);
    expect(screen.getAllByTitle("Do Not Call")).toHaveLength(2); // icon + badge on the one DNC number
    expect(within(lajuan).getByRole("link", { name: "lajuan@example.com" }).getAttribute("href")).toBe(
      "mailto:lajuan@example.com",
    );
  });

  it("renders two named people side by side", () => {
    render(
      <LeadCard
        lead={toLeadCardLead(
          poolLead({
            phone_numbers: [
              { phone: "+13125550101", dnc: false },
              { phone: "+13125550199", dnc: false, owner_name: "Sam Reyes" },
            ],
          }),
        )}
      />,
    );

    expect(screen.getByText("Dana Reyes")).toBeTruthy();
    expect(screen.getByText("Sam Reyes")).toBeTruthy();
    expect(screen.getByText("Contact")).toBeTruthy();
  });

  it("shows the empty state for an owner with no contacts", () => {
    render(<LeadCard lead={toLeadCardLead(poolLead())} />);

    expect(screen.getByText("No contact info")).toBeTruthy();
  });

  it("marks every number when all of them are DNC", () => {
    render(
      <LeadCard
        lead={toLeadCardLead(
          poolLead({
            phone_numbers: [
              { phone: "+13125550101", dnc: true },
              { phone: "+13125550102", dnc: true },
            ],
          }),
        )}
      />,
    );

    expect(screen.getAllByText("DNC")).toHaveLength(2);
    expect(screen.getByText(/2 DNC/)).toBeTruthy();
  });

  it("has no call, SMS or email buttons; row actions live in the menu", () => {
    render(<LeadCard lead={toLeadCardLead(providerLead)} onMore={vi.fn()} />);

    for (const name of ["Call", "SMS", "Email"]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.getByRole("button", { name: "More actions" })).toBeTruthy();
  });

  it("splits a lone person's numbers over two columns and hides the rest behind +N more", () => {
    const phone_numbers = Array.from({ length: 11 }, (_, i) => ({
      phone: `+131255501${String(i).padStart(2, "0")}`,
      dnc: false,
    }));
    const { container } = render(<LeadCard lead={toLeadCardLead(poolLead({ phone_numbers }))} />);
    const shownNumbers = () => within(container).queryAllByText(/^\(312\) 555-01\d\d$/).length;

    expect(shownNumbers()).toBe(6);
    expect([...container.querySelectorAll("ul.flex-col")].map((ul) => ul.children.length)).toEqual([3, 3]);
    fireEvent.click(screen.getByRole("button", { name: "+5 more" }));
    expect(shownNumbers()).toBe(11);
    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(shownNumbers()).toBe(6);
  });
});
