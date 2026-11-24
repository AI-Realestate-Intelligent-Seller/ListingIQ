import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Campaign } from "@/features/campaigns/types/campaigns.types";
import type { Experiment } from "@/features/experiments/types/experiments.types";

import { AnalysisView } from "./analysis-view";

const listCampaignsWithProgress = vi.fn();
const fetchCampaignAudienceAnalysis = vi.fn();
const listExperiments = vi.fn();
const getBrokerOverview = vi.fn();
const updateExperimentFollowup = vi.fn();
const addExperimentRandomLeads = vi.fn();
const renameExperiment = vi.fn();
const { router, navigation } = vi.hoisted(() => ({ router: { push: vi.fn(), replace: vi.fn() }, navigation: { pathname: "/dashboard/broker/analysis" } }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/features/auth/lib/auth-storage", () => ({
  readAuthSession: () => ({ access_token: "token", user: { role: "broker" } }),
}));
vi.mock("@/features/auth/lib/session-guard", () => ({ endSession: vi.fn() }));
vi.mock("@/features/assignments/api/assignments-api", () => ({
  getBrokerOverview: (...args: unknown[]) => getBrokerOverview(...args),
}));
vi.mock("@/features/dashboard/components/notification-bell", () => ({ NotificationBell: () => null }));
vi.mock("@/components/loading/retrieval-progress", () => ({ RetrievalProgress: () => <div>Loading analysis</div> }));
vi.mock("@/features/experiments/components/experiment-workspace", () => ({
  ExperimentWorkspace: ({ open }: { open: boolean }) => open ? <div>Experiment wizard open</div> : null,
}));
vi.mock("@/features/campaigns/api/campaigns-api", () => ({
  listCampaignsWithProgress: (...args: unknown[]) => listCampaignsWithProgress(...args),
  fetchCampaignAudienceAnalysis: (...args: unknown[]) => fetchCampaignAudienceAnalysis(...args),
}));
vi.mock("@/features/experiments/api/experiments-api", () => ({
  listExperiments: (...args: unknown[]) => listExperiments(...args),
  updateExperimentFollowup: (...args: unknown[]) => updateExperimentFollowup(...args),
  addExperimentRandomLeads: (...args: unknown[]) => addExperimentRandomLeads(...args),
  renameExperiment: (...args: unknown[]) => renameExperiment(...args),
}));

function campaign(id: number, template: string, name: string): Campaign {
  return {
    id, name, message_template: template, outreach_reason: "", group_reason_mode: false,
    status: "sent", sent_at: "2026-11-10T14:00:00Z", created_at: "2026-11-09T14:00:00Z",
    recipients: 20, delivered: 18, replied: 6, positive_replies: 3, no_reply: 14, not_sent: 2,
    broker_id: 1, broker_name: "Broker One", broker_email: "broker@example.com", broker_role: "broker",
  };
}

function experiment(id: number, name: string, campaigns: Campaign[]): Experiment {
  return {
    id, name, goal_notes: "", test_dimension: "tone", test_dimension_other: "",
    winner_metric: "reply_rate", allocation_mode: "balanced", timezone: "UTC",
    scheduled_at: "2026-11-10T14:00:00Z", status: "running",
    variants: campaigns.map((item, index) => ({ label: `Variant ${String.fromCharCode(65 + index)}`, message_template: item.message_template, campaign_id: item.id, lead_count: item.recipients, followup_enabled: false, followup_template: null, followup_delay_hours: null, color_index: index })),
    campaign_ids: campaigns.map((item) => item.id), total_leads: campaigns.reduce((sum, item) => sum + item.recipients, 0), outreach_conversations: 0, outreach_complete: false, expected_max_cost: 0,
    error: "", launched_at: "2026-11-10T14:00:00Z", cancelled_at: null, created_at: "2026-11-09T14:00:00Z",
  };
}

describe("AnalysisView", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    navigation.pathname = "/dashboard/broker/analysis";
    const rows = [
      campaign(1, "Hi {{first_name}}\n\nAbout {{address}}", "Direct test · Variant A"),
      campaign(2, "Hello {{first_name}}, are you selling?", "Direct test · Variant B"),
      campaign(3, "Would you sell {{address}}?", "Question test · Variant A"),
      { ...campaign(4, "Unsent template", "Zero outreach"), delivered: 0, replied: 0, positive_replies: 0 },
    ];
    listCampaignsWithProgress.mockResolvedValue(rows);
    listExperiments.mockResolvedValue([
      experiment(1, "Direct vs conversational", rows.slice(0, 2)),
      experiment(2, "Question test", [rows[2]]),
    ]);
    fetchCampaignAudienceAnalysis.mockResolvedValue([]);
    getBrokerOverview.mockResolvedValue({
      total_campaigns: 4, total_leads_outreached: 54, total_leads_replied: 18,
      total_leads_opted_out: 0, total_positive_leads: 9, total_other_reply_leads: 9,
      total_sent_to_lofty: 0, total_appointments_to_confirm: 0,
    });
  });

  it("groups different variant messages by their persisted experiment", async () => {
    render(<AnalysisView />);

    expect(await screen.findByText("Experiments", { selector: ".analysis-summary span" })).toBeTruthy();
    expect(screen.getByText("2", { selector: ".analysis-summary strong" })).toBeTruthy();
    const outreachCard = screen.getByText("Leads outreached").closest("article");
    expect(within(outreachCard as HTMLElement).getByText("54")).toBeTruthy();
    expect(screen.queryByText("Unsent template")).toBeNull();
    expect(screen.queryByText("Zero outreach")).toBeNull();
    const experimentList = screen.getByRole("complementary", { name: "Experiments" });
    expect(experimentList.querySelector(".analysis-template-list-scroll")).toBeTruthy();
    expect(screen.getByText("VARIANT A")).toBeTruthy();
    expect(screen.getByText("VARIANT B")).toBeTruthy();
    expect(screen.getAllByText(/Alex/).length).toBeGreaterThanOrEqual(2);
  });

  it("opens launched follow-up editing outside the experiment detail card", async () => {
    const row = campaign(1, "Hi {{first_name}}", "Detail experiment · Variant A");
    const running = experiment(7, "Detail experiment", [row]);
    running.variants[0] = {
      ...running.variants[0], followup_editable: true,
      followup_delay_hours: 48, followup_scheduled_at: null,
    };
    listCampaignsWithProgress.mockResolvedValue([row]);
    listExperiments.mockResolvedValue([running]);
    updateExperimentFollowup.mockResolvedValue({
      ...running,
      variants: [{ ...running.variants[0], followup_enabled: true,
        followup_template: "Checking in", followup_delay_hours: 72 }],
    });
    render(<AnalysisView />);

    await screen.findByRole("heading", { name: "Detail experiment" });
    expect(screen.queryByText("FOLLOW-UP SCHEDULE")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Experiment actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit follow-up details" }));
    const editor = screen.getByRole("dialog", { name: "Edit before the first follow-up sends" });
    expect(editor.closest(".analysis-template-detail")).toBeNull();
    expect(screen.getByText("FOLLOW-UP SCHEDULE")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Send follow-up/i }));
    fireEvent.change(screen.getByRole("textbox", { name: "Variant A follow-up message" }), { target: { value: "Checking in" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Variant A send after" }), { target: { value: "72" } });
    fireEvent.click(screen.getByRole("button", { name: "Save follow-up" }));

    await waitFor(() => expect(updateExperimentFollowup).toHaveBeenCalledWith(7, 1, {
      followup_enabled: true,
      followup_template: "Checking in",
      followup_delay_hours: 72,
    }, "token"));
  });

  it("renames an experiment from the detail actions menu", async () => {
    const rows = [campaign(1, "Hi {{first_name}}", "Old name · Variant A")];
    const current = experiment(1, "Old name", rows);
    listCampaignsWithProgress.mockResolvedValue(rows);
    listExperiments.mockResolvedValue([current]);
    renameExperiment.mockResolvedValue({ ...current, name: "New experiment name" });
    render(<AnalysisView />);

    await screen.findByRole("heading", { name: "Old name" });
    fireEvent.click(screen.getByRole("button", { name: "Experiment actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename experiment" }));
    const input = screen.getByRole("textbox", { name: "Experiment name" });
    fireEvent.change(input, { target: { value: "New experiment name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));

    await waitFor(() => expect(renameExperiment).toHaveBeenCalledWith(
      1, "New experiment name", "token",
    ));
    expect(await screen.findByRole("heading", { name: "New experiment name" })).toBeTruthy();
  });

  it("adds random leads to a launched variant from the detail actions menu", async () => {
    const row = campaign(1, "Hi {{first_name}}", "Running experiment · Variant A");
    const running = experiment(7, "Running experiment", [row]);
    listCampaignsWithProgress.mockResolvedValue([row]);
    listExperiments.mockResolvedValue([running]);
    addExperimentRandomLeads.mockResolvedValue({
      ...running,
      total_leads: 25,
      variants: [{ ...running.variants[0], lead_count: 25 }],
    });
    render(<AnalysisView />);

    await screen.findByRole("heading", { name: "Running experiment" });
    fireEvent.click(screen.getByRole("button", { name: "Experiment actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Add random leads" }));
    const editor = screen.getByRole("dialog", { name: "Add random leads after launch" });
    expect(editor.closest(".analysis-template-detail")).toBeNull();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Variant A random leads to add" }), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Add and send outreach" }));

    await waitFor(() => expect(addExperimentRandomLeads).toHaveBeenCalledWith(7, 1, 5, "token"));
    expect(await screen.findByText("5 random leads added to Variant A. Their outreach is queued.")).toBeTruthy();
  });

  it("lets the user compare templates and open the experiment workflow", async () => {
    render(<AnalysisView />);
    await screen.findByText("Experiments", { selector: ".analysis-summary span" });

    fireEvent.click(screen.getByRole("checkbox", { name: "Select experiment to compare" }));
    fireEvent.click(screen.getByRole("button", { name: /Question test/ }));
    expect(router.push).toHaveBeenCalledWith(expect.stringMatching(/^\/dashboard\/broker\/analysis\/experiments\/\d+$/), { scroll: false });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select experiment to compare" }));
    expect(screen.queryByText("Selected messages side by side")).toBeNull();
    expect(screen.getByRole("button", { name: "Clear selection" })).toBeTruthy();
    expect(screen.getByLabelText("Grouped outcome comparison")).toBeTruthy();
    expect(screen.getByLabelText("Direct vs conversational, Outreached: 36")).toBeTruthy();
    expect(screen.getByLabelText("Question test, Outreached: 18")).toBeTruthy();
    expect(router.replace).toHaveBeenLastCalledWith(expect.stringMatching(/^\/dashboard\/broker\/analysis\/comparison\/experiments\?ids=\d+,\d+$/), { scroll: false });

    fireEvent.click(screen.getByRole("button", { name: "+ New experiment" }));
    expect(screen.getByText("Experiment wizard open")).toBeTruthy();
  });

  it("shows the latest three campaign runs and paginates older runs", async () => {
    listCampaignsWithProgress.mockResolvedValue([
      campaign(1, "Same template", "Newest run"),
      { ...campaign(2, "Same template", "Second run"), sent_at: "2026-11-09T14:00:00Z" },
      { ...campaign(3, "Same template", "Third run"), sent_at: "2026-11-08T14:00:00Z" },
      { ...campaign(4, "Same template", "Oldest run"), sent_at: "2026-11-07T14:00:00Z" },
    ]);
    listExperiments.mockResolvedValue([]);
    render(<AnalysisView />);

    await screen.findAllByText("Newest run");
    const runList = document.querySelector(".analysis-run-list") as HTMLElement;
    expect(within(runList).getByText("Newest run")).toBeTruthy();
    expect(within(runList).getByText("Second run")).toBeTruthy();
    expect(within(runList).getByText("Third run")).toBeTruthy();
    expect(within(runList).queryByText("Oldest run")).toBeNull();
    expect(within(runList).getAllByText("Asia/Karachi").length).toBeGreaterThan(0);
    expect(within(runList).queryByText("Evening")).toBeNull();
    expect(screen.getByText("Page 1 of 2")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(within(runList).getByText("Oldest run")).toBeTruthy();
    expect(within(runList).queryByText("Newest run")).toBeNull();
  });

  it("combines area names that differ only by case", async () => {
    fetchCampaignAudienceAnalysis.mockResolvedValue([
      { campaign_id: 1, recipients: 20, signals: [], areas: [], score_bands: [], sources: [], area_outcomes: [{ key: "Chicago", label: "Chicago", audience: 20, outreached: 18, replied: 6, positive_replies: 3 }] },
      { campaign_id: 2, recipients: 20, signals: [], areas: [], score_bands: [], sources: [], area_outcomes: [{ key: "CHICAGO", label: "CHICAGO", audience: 14, outreached: 12, replied: 4, positive_replies: 2 }] },
    ]);

    render(<AnalysisView />);
    fireEvent.click(await screen.findByRole("button", { name: /Direct vs conversational/ }));

    await screen.findByText("Chicago");
    expect(screen.getAllByText("Chicago")).toHaveLength(1);
    const areaChart = screen.getByLabelText("Grouped area results");
    expect(within(areaChart).getByLabelText("Chicago, Outreached: 30")).toBeTruthy();
    expect(within(areaChart).getByLabelText("Chicago, Replied: 10")).toBeTruthy();
    expect(within(areaChart).getByLabelText("Chicago, Positive: 5")).toBeTruthy();
  });

  it("filters the main experiment and comparison campaign lists by reply result", async () => {
    listCampaignsWithProgress.mockResolvedValue([
      campaign(1, "Replying template", "Replying run"),
      { ...campaign(2, "No reply template", "No reply run"), replied: 0, positive_replies: 0, no_reply: 20 },
    ]);
    listExperiments.mockResolvedValue([]);
    render(<AnalysisView />);

    await screen.findByText("Replying run");
    expect(screen.queryByRole("combobox", { name: "Reply result" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reply result All results" }));
    expect(screen.getByRole("listbox", { name: "Reply result" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "Has any replies" }));
    expect(screen.getByRole("button", { name: "Reply result Has any replies" })).toBeTruthy();

    expect((await screen.findAllByText("Replying run")).length).toBeGreaterThan(0);
    expect(screen.queryByText("No reply run")).toBeNull();
    expect(screen.getAllByText("1", { selector: ".analysis-summary strong" })).toHaveLength(2);
  });

  it("restores an experiment detail from its URL id", async () => {
    navigation.pathname = "/dashboard/broker/analysis/experiments/1";
    render(<AnalysisView />);

    expect(await screen.findByRole("heading", { name: "Direct vs conversational" })).toBeTruthy();
    expect(screen.getByText("Experiment #1 · 2 campaign runs · 2 message variants")).toBeTruthy();
    expect(screen.getByRole("link", { name: "View all conversations" }).getAttribute("href")).toBe("/dashboard/broker/followups?campaign_ids=1%2C2");
  });
});
