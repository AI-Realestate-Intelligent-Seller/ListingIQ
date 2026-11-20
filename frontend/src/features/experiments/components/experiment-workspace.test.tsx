import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Campaign } from "@/features/campaigns/types/campaigns.types";

import { ExperimentWorkspace } from "./experiment-workspace";

const api = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), cancel: vi.fn(), pool: vi.fn() }));

vi.mock("../api/experiments-api", () => ({
  listExperiments: (...args: unknown[]) => api.list(...args),
  createExperiment: (...args: unknown[]) => api.create(...args),
  cancelExperiment: (...args: unknown[]) => api.cancel(...args),
}));
vi.mock("@/features/leads/api/leads-api", () => ({
  fetchLeadPool: (...args: unknown[]) => api.pool(...args),
}));

function lead(id: number, area: string, zip: string) {
  return { id, owner_name: `Owner ${id}`, phone: `555000${id}`, phone_numbers: [], property_address: `${id} Main St, Austin, TX ${zip}`, area, latitude: null, longitude: null, geocoding_status: "pending", geocoding_provider: null, source: "csv_import", property_type: null, estimated_value: null, listing_price: null, signals: [], score: 0, outreach_reason: null, stage: "ready", last_activity_at: null, conversation_id: null, created_at: null };
}

describe("ExperimentWorkspace", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    api.list.mockResolvedValue([]);
    api.pool.mockResolvedValue({ leads: [lead(1, "North", "78701"), lead(2, "South", "78702"), lead(3, "North", "78701"), lead(4, "South", "78702")], map: { mappable_count: 0, pending_count: 0, failed_count: 0 }, facets: { total: 4, signals: {}, stages: { ready: 4 } }, locations: { states: [], cities: [], zips: [] }, signal_catalog: [], stage_catalog: [{ key: "ready", label: "Ready" }] });
    api.create.mockImplementation(async (payload) => ({ ...payload, id: 9, status: "scheduled", variants: [], campaign_ids: [], total_leads: 4, outreach_conversations: 0, expected_max_cost: .078, error: "", launched_at: null, cancelled_at: null, created_at: new Date().toISOString() }));
  });

  it("builds and schedules two non-overlapping campaign groups through five steps", async () => {
    render(<ExperimentWorkspace token="token" open onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment name" }), { target: { value: "Tone test" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Length" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    fireEvent.change(screen.getByRole("textbox", { name: "Variant A message" }), { target: { value: "Hi {{first_name}}, quick question." } });
    fireEvent.click(screen.getByRole("button", { name: "Variant B" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Variant B message" }), { target: { value: "Hello {{first_name}}, do you have a moment?" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findAllByText("2 leads");
    expect(api.pool).toHaveBeenCalledWith({ signals: undefined }, "token");
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.queryByText("Lead status")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const timeZoneTrigger = screen.getByRole("button", { name: "Time zone" });
    fireEvent.click(timeZoneTrigger);
    expect(screen.getByRole("listbox", { name: "Choose time zone" })).toBeTruthy();
    fireEvent.focus(screen.getByLabelText("Launch date and time"));
    expect(screen.queryByRole("listbox", { name: "Choose time zone" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Launch date and time"), { target: { value: "2026-11-17T10:00" } });
    fireEvent.click(timeZoneTrigger);
    fireEvent.click(screen.getByRole("option", { name: /Eastern Time/ }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Schedule experiment" }));

    await waitFor(() => expect(api.create).toHaveBeenCalled());
    const payload = api.create.mock.calls[0][0];
    expect(payload.variants.map((variant: { lead_ids: number[] }) => variant.lead_ids)).toEqual([[1, 2], [3, 4]]);
    expect(payload.name).toBe("Tone test");
    expect(payload.test_dimension).toBe("tone,length");
    expect(payload.timezone).toBe("America/New_York");
    expect(payload.scheduled_at).toBe("2026-11-17T15:00:00.000Z");
    expect(payload.launch_now).toBe(false);
    expect(payload.winner_metric).toBe("reply_rate");
  });

  it("can send an experiment immediately without a scheduled time", async () => {
    render(<ExperimentWorkspace token="token" open onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findAllByText("2 leads");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Send now" }));

    await waitFor(() => expect(api.create).toHaveBeenCalled());
    const payload = api.create.mock.calls[0][0];
    expect(payload.launch_now).toBe(true);
    expect(payload.scheduled_at).toBeNull();
  });

  it("opens with an editable setup and complete message variants", () => {
    render(<ExperimentWorkspace token="token" open onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    expect((screen.getByRole("textbox", { name: "Experiment name" }) as HTMLInputElement).value).toBe("Seller outreach message test");
    expect((screen.getByRole("textbox", { name: "Goal / notes" }) as HTMLTextAreaElement).value).toContain("Compare which message");
    expect((screen.getByRole("checkbox", { name: "Tone" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Length" }) as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect((screen.getByRole("textbox", { name: "Variant A message" }) as HTMLTextAreaElement).value).toContain("{{agent_name}}");
    fireEvent.click(screen.getByRole("button", { name: "Variant B" }));
    expect((screen.getByRole("textbox", { name: "Variant B message" }) as HTMLTextAreaElement).value).toContain("good time to connect");
  });

  it("closes the new experiment route when the cross is clicked", () => {
    const onClose = vi.fn();
    const onRouteChange = vi.fn();
    const view = render(<ExperimentWorkspace token="token" open={false} routeSection="drafted" routeId="new" onRouteChange={onRouteChange} onClose={onClose} onUnauthorized={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Save and close experiment editor" }));
    expect(onClose).toHaveBeenCalled();
    expect(onRouteChange).toHaveBeenCalledWith("drafted");

    view.rerender(<ExperimentWorkspace token="token" open={false} routeSection="drafted" onRouteChange={onRouteChange} onClose={onClose} onUnauthorized={vi.fn()} />);
    expect(screen.queryByRole("dialog", { name: "New experiment" })).toBeNull();
  });

  it("saves an unfinished experiment and resumes it from the Drafts screen", async () => {
    const onClose = vi.fn();
    const onRouteChange = vi.fn();
    const first = render(<ExperimentWorkspace token="token" open onRouteChange={onRouteChange} onClose={onClose} onUnauthorized={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Experiment name" }), { target: { value: "Saved tone draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(onClose).toHaveBeenCalled();
    expect(localStorage.getItem("listingiq:experiment-drafts:current")).toContain("Saved tone draft");
    expect(onRouteChange).toHaveBeenCalledWith("drafted");

    first.unmount();
    render(<ExperimentWorkspace token="token" open={false} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Drafted 1" }));
    expect(await screen.findByText("Saved tone draft")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Continue editing" }));
    expect(screen.getByRole("dialog", { name: "Edit experiment draft" })).toBeTruthy();
    expect((screen.getByRole("textbox", { name: "Experiment name" }) as HTMLInputElement).value).toBe("Saved tone draft");
  });

  it("applies the general date and outcome filters to scheduled experiments", async () => {
    const base = { goal_notes: "Compare which message earns more owner replies.", test_dimension: "tone", test_dimension_other: "", winner_metric: "reply_rate", allocation_mode: "balanced", timezone: "UTC", status: "running", variants: [{ label: "Variant A", campaign_id: 101, lead_count: 10, message_template: "Hi {{first_name}}, would you consider selling?", followup_enabled: false, followup_template: null, followup_delay_hours: null, color_index: 0 }], total_leads: 10, outreach_conversations: 8, expected_max_cost: .195, error: "", launched_at: null, cancelled_at: null, created_at: "2026-11-01T00:00:00Z" };
    api.list.mockResolvedValue([
      { ...base, id: 1, name: "Positive experiment", campaign_ids: [101], scheduled_at: "2026-11-10T12:00:00Z" },
      { ...base, id: 2, name: "No reply experiment", campaign_ids: [102], scheduled_at: "2026-11-12T12:00:00Z" },
    ]);
    const campaign = (id: number, replied: number, positive: number): Campaign => ({ id, name: `Campaign ${id}`, message_template: "Hi", outreach_reason: "", group_reason_mode: false, status: "sent", sent_at: "2026-11-10T12:00:00Z", created_at: "2026-11-09T12:00:00Z", recipients: 10, delivered: 10, replied, positive_replies: positive, no_reply: 10 - replied, not_sent: 0, broker_id: 1, broker_name: "Broker", broker_email: "broker@example.com", broker_role: "broker" });
    render(<ExperimentWorkspace token="token" open={false} campaigns={[campaign(101, 3, 1), campaign(102, 0, 0)]} dateFrom="2026-11-01" dateTo="2026-11-11" outcomeFilter="positive" onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    expect(await screen.findByText("Positive experiment")).toBeTruthy();
    expect(screen.queryByText("No reply experiment")).toBeNull();
    expect(screen.getByText("1 positive")).toBeTruthy();
    expect(screen.getByText("10 properties outreached")).toBeTruthy();
    expect(screen.getByText("8 conversations")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Status" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Status All statuses" }));
    expect(screen.getByRole("listbox", { name: "Status" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: "Running" }));
    expect(screen.getByRole("button", { name: "Status Running" })).toBeTruthy();
    expect(screen.queryByRole("listbox", { name: "Status" })).toBeNull();
    const tabs = screen.getByRole("navigation", { name: "Experiment status" }).querySelectorAll("button");
    expect(tabs[0].textContent).toContain("Launched");
    expect(tabs[1].textContent).toContain("Drafted");
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(screen.getByText("10 properties are grouped into 8 conversations.")).toBeTruthy();
    expect(screen.queryByText("Compare which message earns more owner replies.")).toBeNull();
    expect(screen.queryByText("Hi {{first_name}}, would you consider selling?")).toBeNull();
    expect(screen.getByRole("button", { name: "Hide review" })).toBeTruthy();
  });
});
