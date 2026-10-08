import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listFollowUps } from "../api/followups-api";
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FollowUpsBoard scope filter", () => {
  it("shows Replied and All to an agent and sends the selected scope", async () => {
    render(<FollowUpsBoard />);

    const group = screen.getByRole("group", { name: "Conversation scope" });
    expect(group).toBeTruthy();
    expect(screen.getByRole("button", { name: "Replied" }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "All" }));

    await waitFor(() => {
      expect(request.mock.calls.some((call) => call[1] === "all")).toBe(true);
    });
    expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("does not offer starting a new conversation", () => {
    render(<FollowUpsBoard />);
    expect(screen.queryByRole("button", { name: /new conversation/i })).toBeNull();
  });

  it("shows the carrier reason instead of waiting for a reply when delivery failed", async () => {
    request.mockResolvedValueOnce([{
      id: 41,
      lead_id: null,
      campaign_id: null,
      campaign_name: null,
      contact: "+15551234567",
      name: "Test Owner",
      property_address: "1 Main St",
      area: null,
      properties: [],
      has_multiple_properties: false,
      ai_enabled: true,
      handled_by: "bobbie",
      awaiting_broker_reply: false,
      lead_status: "processing",
      queue_status: "completed",
      dnc_alert: false,
      meeting_booked: false,
      followup_state: "pending",
      reason: "in_conversation",
      reason_label: "Conversation in progress",
      latest_outbound_status: "delivery_failed",
      latest_outbound_failure_reason: "The destination is a landline.",
      waiting_days: 0,
      reply_count: 0,
      message_count: 1,
      first_reply_at: null,
      last_reply_at: null,
      latest_message: "Hello",
      latest_message_at: "2026-10-08T14:30:00Z",
      created_at: "2026-10-08T14:30:00Z",
    }]);

    render(<FollowUpsBoard />);

    expect(await screen.findByText("Not delivered — The destination is a landline.")).toBeTruthy();
    expect(screen.queryByText("Waiting for first reply")).toBeNull();
  });
});
