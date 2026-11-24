import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../api/experiments-api";
import type { Experiment } from "../types/experiments.types";
import { ExperimentDetailMenu } from "./experiment-detail-menu";

vi.mock("../api/experiments-api", () => ({
  getExperiment: vi.fn(),
  renameExperiment: vi.fn(),
}));

const experiment: Experiment = {
  id: 24,
  name: "Testing Experiment 24 Nov initial",
  goal_notes: "",
  test_dimension: "tone",
  test_dimension_other: "",
  winner_metric: "reply_rate",
  allocation_mode: "balanced",
  timezone: "Asia/Karachi",
  scheduled_at: "2026-11-24T14:05:16Z",
  status: "running",
  variants: [],
  campaign_ids: [67, 68],
  total_leads: 4,
  outreach_conversations: 3,
  outreach_complete: true,
  expected_max_cost: 0.078,
  error: "",
  launched_at: "2026-11-24T14:05:17Z",
  cancelled_at: null,
  created_at: "2026-11-24T14:05:16Z",
};

describe("ExperimentDetailMenu outreach overlay", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getExperiment).mockResolvedValue({
      ...experiment,
      outreached_leads: 3,
      not_contacted: [{
        lead_id: 14332,
        owner_name: "Omar Diaz",
        property_address: "7531 Long Ave, Burbank, IL, 60459",
        variant: "Variant A",
        reason: "Phone type could not be verified as mobile by Telnyx. No message was sent.",
      }],
    });
  });

  it("opens the three-dot outreach details overlay with totals and reasons", async () => {
    render(<ExperimentDetailMenu
      experiment={experiment}
      token="token"
      onEditFollowup={vi.fn()}
      onAddRandomLeads={vi.fn()}
      onRenamed={vi.fn()}
      onError={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Experiment actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Outreach details" }));

    expect(await screen.findByRole("dialog", { name: experiment.name })).toBeTruthy();
    expect(api.getExperiment).toHaveBeenCalledWith(24, "token");
    expect(screen.getByText("Leads selected").parentElement?.textContent).toContain("4");
    expect(screen.getByText("Outreached").parentElement?.textContent).toContain("3");
    expect(screen.getByText("Not contacted").parentElement?.textContent).toContain("1");
    expect(screen.getByText("Omar Diaz")).toBeTruthy();
    expect(screen.getByText(/Phone type could not be verified as mobile/)).toBeTruthy();
  });
});
