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
  createConversation: vi.fn(),
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

vi.mock("@/features/sms/components/new-conversation-dialog", () => ({
  NewConversationDialog: () => null,
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
});
