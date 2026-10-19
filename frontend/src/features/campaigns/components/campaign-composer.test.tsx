import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CampaignDetail, CampaignRecipient } from "../types/campaigns.types";
import { fetchCampaign } from "../api/campaigns-api";
import { CampaignComposer } from "./campaign-composer";

vi.mock("@/features/leads/components/lead-detail-drawer", () => ({
  LeadDetailDrawer: () => null,
}));
vi.mock("../api/campaigns-api", () => ({
  fetchCampaign: vi.fn(),
  sendCampaign: vi.fn(),
  suggestReasons: vi.fn(),
  updateCampaign: vi.fn(),
}));

function recipient(id: number): CampaignRecipient {
  return {
    lead_id: id,
    owner_name: `Owner ${id}`,
    phone: `+1312555${String(id).padStart(4, "0")}`,
    property_address: `${id} Main St`,
    area: "Chicago",
    signals: [],
    score: 70,
    stage: "ready",
    last_activity_at: null,
    text: `Hello Owner ${id}`,
    is_long: false,
    conversation_id: null,
    replied: false,
    delivery_status: null,
    delivery_failure_reason: null,
  };
}

afterEach(cleanup);

describe("CampaignComposer recipient pagination", () => {
  it("requests and displays only the current 20-recipient preview page", async () => {
    const recipients = Array.from({ length: 25 }, (_, index) => recipient(index + 1));
    const campaign: CampaignDetail = {
      id: 7,
      name: "Draft campaign",
      message_template: "Hello {{owner_name}}",
      outreach_reason: "",
      status: "draft",
      sent_at: null,
      created_at: "2026-10-16T00:00:00Z",
      recipients: recipients.length,
      delivered: 0,
      replied: 0,
      no_reply: recipients.length,
      not_sent: 0,
      broker_id: 1,
      broker_name: "Broker",
      broker_email: "broker@example.com",
      broker_role: "broker",
      preview: {
        campaign_id: 7,
        recipients: recipients.slice(0, 20),
        skipped: [],
        page: 1,
        page_size: 20,
        total_items: 25,
        total_pages: 2,
        template_error: "",
        tokens: { owner_name: "Owner" },
      },
    };
    vi.mocked(fetchCampaign).mockResolvedValue({
      ...campaign,
      preview: {
        ...campaign.preview,
        recipients: recipients.slice(20),
        page: 2,
      },
    });

    function Harness() {
      const [current, setCurrent] = useState(campaign);
      return (
        <CampaignComposer
          campaign={current}
          accessToken="token"
          onChange={setCurrent}
          onSent={vi.fn()}
          onDiscard={vi.fn()}
          onBack={vi.fn()}
          onOpenConversation={vi.fn()}
        />
      );
    }

    render(<Harness />);

    expect(screen.getByRole("rowheader", { name: /^Owner 1\b/ })).toBeTruthy();
    expect(screen.getByRole("rowheader", { name: /^Owner 20\b/ })).toBeTruthy();
    expect(screen.queryByRole("rowheader", { name: /^Owner 21\b/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByRole("rowheader", { name: /^Owner 21\b/ })).toBeTruthy();
    expect(screen.queryByRole("rowheader", { name: /^Owner 1\b/ })).toBeNull();
    expect(screen.getByText("Page 2 of 2 · 25 leads")).toBeTruthy();
    expect(fetchCampaign).toHaveBeenCalledWith(7, "token", undefined, 2);
  });

  it("keeps skipped leads collapsed and shows the exact reason on request", () => {
    const campaign: CampaignDetail = {
      id: 8,
      name: "Landline check",
      message_template: "Hello {{owner_name}}",
      outreach_reason: "",
      status: "sending",
      sent_at: null,
      created_at: "2026-10-16T00:00:00Z",
      recipients: 1,
      delivered: 0,
      replied: 0,
      no_reply: 1,
      not_sent: 0,
      broker_id: 1,
      broker_name: "Broker",
      broker_email: "broker@example.com",
      broker_role: "broker",
      preview: {
        campaign_id: 8,
        recipients: [],
        skipped: [{
          lead_id: 41,
          owner_name: "Moriah Theobald",
          reason: "Number is fixed line; campaigns send only to mobile numbers.",
        }],
        page: 1,
        page_size: 20,
        total_items: 1,
        total_pages: 1,
        template_error: "",
        tokens: { owner_name: "Owner" },
      },
    };

    render(
      <CampaignComposer
        campaign={campaign}
        accessToken="token"
        onChange={vi.fn()}
        onSent={vi.fn()}
        onDiscard={vi.fn()}
        onBack={vi.fn()}
        onOpenConversation={vi.fn()}
      />,
    );

    const summary = screen.getByText("Show reasons").closest("summary");
    const details = summary?.closest("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("Moriah Theobald")).toBeTruthy();
    expect(screen.getByText("Landline")).toBeTruthy();
    expect(screen.queryByText("Number is fixed line; campaigns send only to mobile numbers.")).toBeNull();

    fireEvent.click(summary!);

    expect(details?.open).toBe(true);
    expect(screen.getByText("Hide reasons")).toBeTruthy();
  });
});
