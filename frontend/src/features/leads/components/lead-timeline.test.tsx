import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchLeadHistory } from "../api/leads-api";
import type { LeadHistory } from "../types/leads.types";
import { LeadTimeline } from "./lead-timeline";

vi.mock("../api/leads-api", () => ({
  fetchLeadHistory: vi.fn(),
}));

const request = vi.mocked(fetchLeadHistory);

const history: LeadHistory = {
  events: [
    {
      id: 1,
      event_category: "activity",
      event_type: "meeting_booked",
      actor_type: "ai",
      actor_id: null,
      actor_name: null,
      target_id: null,
      target_name: null,
      from_value: null,
      to_value: null,
      reason: null,
      meta: null,
      created_at: "2026-09-29T14:35:40.280498",
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LeadTimeline", () => {
  it("clears a transient refresh error after the next successful poll", async () => {
    request.mockResolvedValueOnce(history);
    const view = render(
      <LeadTimeline leadId={10} accessToken="token" refreshToken={0} />,
    );

    expect(await screen.findByText("Meeting booked")).toBeTruthy();

    request.mockRejectedValueOnce(new Error("Unable to reach the server."));
    view.rerender(
      <LeadTimeline leadId={10} accessToken="token" refreshToken={1} />,
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Unable to reach the server.",
    );
    expect(screen.getByText("Meeting booked")).toBeTruthy();

    request.mockResolvedValueOnce(history);
    view.rerender(
      <LeadTimeline leadId={10} accessToken="token" refreshToken={2} />,
    );

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByText("Meeting booked")).toBeTruthy();
  });
});
