import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CampaignDetail, CampaignRecipient } from "../types/campaigns.types";
import {
  fetchCampaign,
  removeCampaignRecipients,
  suggestReasons,
  updateCampaign,
} from "../api/campaigns-api";
import { CampaignComposer } from "./campaign-composer";

vi.mock("@/features/leads/components/lead-detail-drawer", () => ({
  LeadDetailDrawer: () => null,
}));
vi.mock("../api/campaigns-api", () => ({
  fetchCampaign: vi.fn(),
  assignCampaignMainSignal: vi.fn(),
  removeCampaignRecipients: vi.fn(),
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
    main_signal: null,
    main_signal_basis: null,
    reason_source: "catalog",
    needs_review: false,
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
    group_reason_mode: true,
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
      unresolved_main_signal_count: 0,
      needs_review_count: 0,
      reason_groups: [],
      signal_options: [],
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
    expect(fetchCampaign).toHaveBeenCalledWith(7, "token", undefined, 2, {});
  });

  it("keeps skipped leads collapsed and shows the exact reason on request", () => {
    const campaign: CampaignDetail = {
      id: 8,
      name: "Landline check",
      message_template: "Hello {{owner_name}}",
      outreach_reason: "",
    group_reason_mode: true,
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
      unresolved_main_signal_count: 0,
      needs_review_count: 0,
      reason_groups: [],
      signal_options: [],
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

  it("filters editable signal reasons without losing edits and confirms an AI draft", async () => {
    const campaign: CampaignDetail = {
      id: 9,
      name: "Grouped draft",
      message_template: "Hello {{owner_name}} {{reason}}",
      outreach_reason: "",
      group_reason_mode: true,
      status: "draft",
      sent_at: null,
      created_at: "2026-10-22T00:00:00Z",
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
        campaign_id: 9,
        recipients: [{
          ...recipient(1),
          signals: [{ key: "expired", label: "Expired" }],
          main_signal: "expired",
          main_signal_basis: "single_signal",
        }],
        skipped: [],
        page: 1,
        page_size: 20,
        total_items: 1,
        total_pages: 1,
        unresolved_main_signal_count: 0,
        needs_review_count: 0,
        reason_groups: [
          { key: "expired", label: "Expired", count: 1, reason: "", confirmed: false },
          { key: "high_equity", label: "High Equity", count: 1, reason: "Equity reason", confirmed: true },
        ],
        signal_options: [
          { key: "expired", label: "Expired" },
          { key: "high_equity", label: "High Equity" },
        ],
        template_error: "",
        tokens: { owner_name: "Owner", reason: "Reason" },
      },
    };
    const confirmedCampaign: CampaignDetail = {
      ...campaign,
      preview: {
        ...campaign.preview,
        reason_groups: [
          { key: "expired", label: "Expired", count: 1, reason: "Edited AI reason", confirmed: true },
          { key: "high_equity", label: "High Equity", count: 1, reason: "Equity reason", confirmed: true },
        ],
      },
    };
    vi.mocked(suggestReasons).mockResolvedValueOnce({
      signals: [{ key: "expired", label: "Expired", count: 1, share: 1 }],
      suggestions: ["Expired preferred reason", "AI expired reason", "Another AI reason"],
      options: [
        { text: "Expired preferred reason", source: "preferred" },
        { text: "AI expired reason", source: "ai" },
        { text: "Another AI reason", source: "ai" },
      ],
      source: "ai",
      note: "",
    }).mockResolvedValue({
      signals: [{ key: "high_equity", label: "High Equity", count: 1, share: 1 }],
      suggestions: [],
      options: [],
      source: "catalog",
      note: "AI could not be reached. Retry Suggest with AI.",
    });
    vi.mocked(updateCampaign).mockResolvedValue(confirmedCampaign);

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

    expect(screen.getByText("Reason suggestions")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Lead Signal" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Suggested Reason" })).toBeTruthy();
    expect(document.querySelector(".campaign-reason-table-scroll")).toBeTruthy();
    expect(suggestReasons).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Expired suggested reason"), {
      target: { value: "Unsaved local edit" },
    });
    expect(screen.getByText("Needs confirmation")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter reason signals"), { target: { value: "High" } });
    expect(screen.queryByLabelText("Expired suggested reason")).toBeNull();
    expect(screen.getByLabelText("High Equity suggested reason")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Filter reason signals"), { target: { value: "" } });
    expect((screen.getByLabelText("Expired suggested reason") as HTMLTextAreaElement).value).toBe("Unsaved local edit");

    fireEvent.click(screen.getAllByRole("button", { name: /Suggest with AI/ })[0]);
    expect(await screen.findByText("Expired preferred reason")).toBeTruthy();
    expect(document.querySelectorAll(".campaign-reason-options li")).toHaveLength(3);
    expect(document.querySelectorAll(".campaign-reason-options strong")).toHaveLength(0);
    expect((screen.getByLabelText("Expired suggested reason") as HTMLTextAreaElement).value).toBe("Unsaved local edit");
    fireEvent.click(screen.getByText("AI expired reason").closest("button")!);
    expect((screen.getByLabelText("Expired suggested reason") as HTMLTextAreaElement).value).toBe("AI expired reason");
    fireEvent.change(screen.getByLabelText("Expired suggested reason"), {
      target: { value: "Edited AI reason" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: /Save & Confirm/ })[0]);
    expect(updateCampaign).toHaveBeenCalledWith(
      9,
      { reason_group: { signal: "expired", reason: "Edited AI reason", confirmed: true } },
      "token",
      1,
    );
    await waitFor(() => expect(screen.getAllByText("Confirmed")).toHaveLength(2));

    fireEvent.click(screen.getAllByRole("button", { name: /Suggest with AI/ })[1]);
    expect(await screen.findByText("AI could not be reached. Retry Suggest with AI.")).toBeTruthy();
    expect((screen.getByLabelText("High Equity suggested reason") as HTMLTextAreaElement).value).toBe("Equity reason");

    fireEvent.click(screen.getByRole("button", { name: "Filter campaign recipients" }));
    expect(screen.getByLabelText("Search owners or properties")).toBeTruthy();
    expect(screen.getByLabelText("Filter area or ZIP")).toBeTruthy();
    expect(screen.getByRole("option", { name: "High Equity" })).toBeTruthy();
    expect(screen.queryByText("Needs Main Signal")).toBeNull();

    vi.mocked(fetchCampaign).mockImplementation(() => new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByText("Applying…").closest('[role="status"]')).toBeTruthy();
    expect((screen.getByRole("button", { name: "Apply" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Clear" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByLabelText("Filter by signal").closest("div")?.getAttribute("aria-busy")).toBe("true");
  });

  it("shows signals without a selector and confirms unresolved cleanup before its toast", async () => {
    const single = {
      ...recipient(31),
      signals: [{ key: "expired", label: "Expired" }],
      main_signal: "expired",
      main_signal_basis: "single_signal" as const,
      text: "Single-signal confirmed reason",
    };
    const multi = {
      ...recipient(32),
      owner_name: "Anthony Russo",
      signals: [
        { key: "expired", label: "Expired" },
        { key: "absentee_owner", label: "Absentee Owner" },
      ],
      needs_review: true,
      reason_source: "needs_review" as const,
      text: "",
    };
    const verified = {
      ...recipient(33),
      owner_name: "Verified Owner",
      signals: [
        { key: "expired", label: "Expired" },
        { key: "high_equity", label: "High Equity" },
      ],
      main_signal: "expired",
      main_signal_basis: "source_verified" as const,
      text: "Verified reason",
    };
    const campaign: CampaignDetail = {
      id: 12,
      name: "Multi-signal draft",
      message_template: "Hello {{owner_name}} {{reason}}",
      outreach_reason: "",
      group_reason_mode: true,
      status: "draft",
      sent_at: null,
      created_at: "2026-10-22T00:00:00Z",
      recipients: 23,
      delivered: 0,
      replied: 0,
      no_reply: 23,
      not_sent: 0,
      broker_id: 1,
      broker_name: "Broker",
      broker_email: "broker@example.com",
      broker_role: "broker",
      preview: {
        campaign_id: 12,
        recipients: [single, multi, verified],
        skipped: [],
        page: 2,
        page_size: 20,
        total_items: 23,
        total_pages: 2,
        unresolved_main_signal_count: 1,
        needs_review_count: 1,
        reason_groups: [
          { key: "expired", label: "Expired", count: 3, reason: "Expired reason", confirmed: true },
          { key: "absentee_owner", label: "Absentee Owner", count: 1, reason: "Absentee reason", confirmed: true },
          { key: "high_equity", label: "High Equity", count: 1, reason: "Equity reason", confirmed: true },
        ],
        signal_options: [
          { key: "expired", label: "Expired" },
          { key: "absentee_owner", label: "Absentee Owner" },
          { key: "high_equity", label: "High Equity" },
        ],
        template_error: "",
        tokens: { owner_name: "Owner", reason: "Reason" },
      },
    };
    const updatedCampaign: CampaignDetail = {
      ...campaign,
      recipients: 22,
      preview: {
        ...campaign.preview,
        unresolved_main_signal_count: 0,
        needs_review_count: 0,
        total_items: 22,
        recipients: [single, verified],
      },
    };
    vi.mocked(removeCampaignRecipients)
      .mockRejectedValueOnce(new Error("Removal failed"))
      .mockResolvedValue({
        result: { removed_contacts: 1, returned_properties: 1, remaining_contacts: 22 },
        campaign: updatedCampaign,
      });
    vi.spyOn(window, "confirm").mockReturnValue(true);

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

    expect(screen.queryByText("Choose SMS Reason")).toBeNull();
    expect(screen.getAllByText("Expired").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Absentee Owner").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "Return unresolved to Lead Pool" }));
    expect((await screen.findAllByText("Removal failed")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Return unresolved to Lead Pool" }));
    expect(removeCampaignRecipients).toHaveBeenLastCalledWith(
      12,
      { mode: "unresolved" },
      "token",
      2,
      {},
    );
    expect(await screen.findByText(
      "1 recipients returned to Lead Pool. 22 recipients remain.",
    )).toBeTruthy();
    expect(screen.getByText("Page 2 of 2 · 22 leads")).toBeTruthy();
  });
});
