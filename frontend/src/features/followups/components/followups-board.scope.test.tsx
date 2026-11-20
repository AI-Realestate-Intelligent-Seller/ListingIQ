import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listFollowUps, setFollowUpStatus } from "../api/followups-api";
import type { FollowUp } from "../types/followups.types";
import { FollowUpsBoard } from "./followups-board";

vi.mock("next/navigation", () => {
  const router = { replace: vi.fn() };
  return {
    useRouter: () => router,
  };
});

vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({
    access_token: "agent-token",
    user: { id: 3, role: "agent", full_name: "Aman Khan" },
  }),
}));

vi.mock("@/features/auth/lib/session-guard", () => ({
  endSession: vi.fn(),
}));

vi.mock("@/features/campaigns/api/campaigns-api", () => ({
  listCampaigns: vi.fn(() => Promise.resolve([])),
}));

vi.mock("@/features/sms/api/sms-api", () => ({
  listMessages: vi.fn(() => Promise.resolve([])),
  sendMessage: vi.fn(),
  setHandover: vi.fn(),
}));

vi.mock("../api/followups-api", () => ({
  bookAppointment: vi.fn(),
  listFollowUps: vi.fn(() => Promise.resolve([])),
  setFollowUpStatus: vi.fn(),
  suggestReplies: vi.fn(),
}));

vi.mock("@/features/dashboard/components/notification-bell", () => ({ NotificationBell: () => null }));
vi.mock("@/features/dashboard/components/notification-provider", () => ({
  useNotifications: () => ({ markConversationAsRead: vi.fn() }),
}));
vi.mock("./appointment-dialog", () => ({ AppointmentDialog: () => null }));
vi.mock("@/features/leads/components/lead-detail-drawer", () => ({
  LeadDetailDrawer: () => null,
}));
vi.mock("@/features/leads/components/lead-timeline", () => ({
  LeadTimeline: () => null,
}));

const request = vi.mocked(listFollowUps);
const updateStatus = vi.mocked(setFollowUpStatus);

function followUp(id: number, name: string, overrides: Partial<FollowUp> = {}): FollowUp {
  return {
    id, lead_id: null, campaign_id: null, campaign_name: null, contact: `+15551234${id}`,
    name, property_address: `${id} Main St`, area: null, properties: [], has_multiple_properties: false,
    ai_enabled: true, handled_by: "bobbie", awaiting_broker_reply: false, lead_status: "processing",
    queue_status: "completed", dnc_alert: false, meeting_booked: false, sent_to_lofty: false,
    followup_state: "pending", reason: "in_conversation", reason_label: "Conversation in progress",
    latest_outbound_status: "sent", latest_outbound_failure_reason: null, waiting_days: 0,
    reply_count: 1, message_count: 2, first_reply_at: "2026-10-08T14:31:00Z",
    last_reply_at: "2026-10-08T14:31:00Z", latest_message: "Hello",
    latest_message_at: "2026-10-08T14:31:00Z", created_at: "2026-10-08T14:30:00Z",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FollowUpsBoard outcome filter", () => {
  it("shows four outcome tabs and loads every conversation once", async () => {
    render(<FollowUpsBoard />);

    const group = screen.getByRole("group", { name: "Conversation scope" });
    expect(group).toBeTruthy();
    expect(screen.getByRole("button", { name: "Replied" }).getAttribute("aria-pressed")).toBe("true");
    for (const label of ["Leads", "Opt-out", "All"]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }

    await waitFor(() => {
      expect(request.mock.calls.some((call) => call[1] === "all")).toBe(true);
    });
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("does not offer starting a new conversation", () => {
    render(<FollowUpsBoard />);
    expect(screen.queryByRole("button", { name: /new conversation/i })).toBeNull();
  });

  it("keeps waiting for a reply when the stored delivery status failed", async () => {
    request.mockResolvedValueOnce([followUp(41, "Test Owner", {
      latest_outbound_status: "delivery_failed",
      latest_outbound_failure_reason: "The destination is a landline.",
      reply_count: 0,
      message_count: 1,
      first_reply_at: null,
      last_reply_at: null,
    })]);

    render(<FollowUpsBoard initialCategory="all" />);

    const statusLabel = await screen.findByText("In progress");
    expect(statusLabel.classList.contains("reason-reply_needed")).toBe(true);
    const conversationLink = screen.getByRole("listitem");
    expect(conversationLink.tagName).toBe("A");
    expect(conversationLink.getAttribute("href")).toBe("/dashboard/agent/followups?conversation_id=41");
    expect(screen.queryByText("Not delivered — The destination is a landline.")).toBeNull();
  });

  it("partitions replied, positive leads, and opt-outs while All includes no replies", async () => {
    request.mockResolvedValueOnce([
      followUp(1, "Other reply"),
      followUp(2, "Positive lead", { lead_status: "interested" }),
      followUp(3, "Opted out owner", { lead_status: "dnc", dnc_alert: true, reason: "opted_out", reason_label: "Opted out — do not contact" }),
      followUp(4, "No reply yet", { reply_count: 0, first_reply_at: null, last_reply_at: null }),
    ]);
    render(<FollowUpsBoard />);

    expect(await screen.findByText("Other reply")).toBeTruthy();
    expect(screen.queryByText("Positive lead")).toBeNull();
    expect(screen.queryByText("Opted out owner")).toBeNull();
    expect(screen.queryByText("No reply yet")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Leads" }));
    expect(screen.getByText("Positive lead")).toBeTruthy();
    expect(screen.queryByText("Other reply")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Opt-out" }));
    expect(screen.getByText("Opted out owner")).toBeTruthy();
    expect(screen.queryByText("Positive lead")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    for (const name of ["Other reply", "Positive lead", "Opted out owner", "No reply yet"]) {
      expect(screen.getByText(name)).toBeTruthy();
    }
  });

  it("synchronizes a manual lead status across the chat and sidebar", async () => {
    const original = followUp(5, "Status owner");
    const updated = followUp(5, "Status owner", { lead_status: "ready_to_sell", reason: "appointment_pending", reason_label: "Appointment to confirm" });
    request.mockResolvedValueOnce([original]);
    updateStatus.mockResolvedValueOnce(updated);
    render(<FollowUpsBoard initialCategory="all" />);

    fireEvent.click(await screen.findByRole("listitem"));
    fireEvent.click(screen.getByRole("button", { name: "In progress" }));
    fireEvent.click(screen.getByRole("option", { name: "Ready to sell" }));

    await waitFor(() => expect(screen.getAllByText("Ready to sell").length).toBeGreaterThanOrEqual(2));
    expect(updateStatus).toHaveBeenCalledWith(5, "ready_to_sell", "agent-token");
  });

  it("filters Follow-ups to every campaign in an experiment", async () => {
    request.mockResolvedValueOnce([
      followUp(11, "Variant A owner", { campaign_id: 61, campaign_name: "Experiment · Variant A" }),
      followUp(12, "Variant B owner", { campaign_id: 62, campaign_name: "Experiment · Variant B" }),
      followUp(13, "Other campaign owner", { campaign_id: 90, campaign_name: "Other campaign" }),
    ]);

    render(<FollowUpsBoard campaignIds={[61, 62]} />);

    expect(await screen.findByText("Variant A owner")).toBeTruthy();
    expect(screen.getByText("Variant B owner")).toBeTruthy();
    expect(screen.queryByText("Other campaign owner")).toBeNull();
    expect(screen.getByRole("button", { name: "2 variant campaigns" })).toBeTruthy();
    expect(request).toHaveBeenCalledWith("agent-token", "all", undefined, expect.any(AbortSignal), undefined, expect.any(Function));
  });

  it("keeps the conversation actions behind the multiple-properties drawer", async () => {
    request.mockResolvedValueOnce([followUp(21, "Shared owner", {
      lead_id: 210,
      has_multiple_properties: true,
      properties: [
        { lead_id: 210, address: "1 First St", area: "Austin", campaign_id: 61, campaign_name: "Variant A", signals: [] },
        { lead_id: 211, address: "2 Second St", area: "Austin", campaign_id: 62, campaign_name: "Variant B", signals: [] },
      ],
    })]);
    const view = render(<FollowUpsBoard initialCategory="all" />);

    fireEvent.click(await screen.findByRole("listitem"));
    fireEvent.click(screen.getByRole("button", { name: "More actions" }));
    expect(screen.getByRole("menu", { name: "Conversation actions" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "View multiple properties for this owner" }));

    expect(screen.getByRole("dialog", { name: "Multiple property details" })).toBeTruthy();
    expect(screen.queryByRole("menu", { name: "Conversation actions" })).toBeNull();
    expect(view.container.querySelector(".followups-board")?.classList.contains("properties-open")).toBe(true);
  });
});
