import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { generateLoftySummary, getLoftyPreview, sendToLofty } from "../api/lofty-api";
import type { LoftyExportStatus, LoftyPreview } from "../types/followups.types";
import { LoftyExportDialog } from "./lofty-export-dialog";

vi.mock("../api/lofty-api", () => ({
  getLoftyPreview: vi.fn(),
  generateLoftySummary: vi.fn(),
  sendToLofty: vi.fn(),
}));

const preview = vi.mocked(getLoftyPreview);
const summarize = vi.mocked(generateLoftySummary);
const send = vi.mocked(sendToLofty);

let nextConversation = 100;

function previewFor(id: number, overrides: Partial<LoftyPreview> = {}): LoftyPreview {
  return {
    conversation_id: id,
    listingiq_lead_id: 1,
    name: "John Doe",
    property_address: "123 Main St, Chicago, IL",
    phone: "+13125550123",
    emails: [],
    messages: [
      { id: 1, sender: "AI", text: "Would you consider selling?", created_at: "2026-11-02T16:15:00", not_delivered: false },
      { id: 2, sender: "Owner", text: "Yes, if the offer makes sense.", created_at: "2026-11-02T16:18:00", not_delivered: false },
      { id: 3, sender: "You", text: "We can arrange a consultation.", created_at: "2026-11-02T16:22:00", not_delivered: false },
    ],
    available: true,
    unavailable_reason: null,
    export: null,
    ...overrides,
  };
}

function status(overrides: Partial<LoftyExportStatus> = {}): LoftyExportStatus {
  return {
    status: "completed",
    step: "done",
    revision: 1,
    lofty_lead_id: "9001",
    lofty_url: "https://crm.lofty.com/admin/home/lead/detail/9001",
    lead_action: "created",
    mute_verified: true,
    notes_saved: 1,
    notes_total: 1,
    summary: "Seller is open to offers.",
    last_message_id: 3,
    last_error: null,
    candidate_lead_ids: [],
    attempts: 1,
    completed_at: null,
    updated_at: null,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LoftyExportDialog", () => {
  it("shows lead details, sender labels and a generated, editable summary", async () => {
    const id = nextConversation++;
    preview.mockResolvedValue(previewFor(id));
    summarize.mockResolvedValue({ summary: "Seller is open to offers." });

    render(<LoftyExportDialog conversationId={id} accessToken="t" onClose={vi.fn()} />);

    expect(await screen.findByText("John Doe")).toBeTruthy();
    expect(screen.getByText("123 Main St, Chicago, IL")).toBeTruthy();
    expect(screen.getByText("Not on file (omitted)")).toBeTruthy();
    expect(screen.getByText("AI")).toBeTruthy();
    expect(screen.getByText("Owner")).toBeTruthy();
    expect(screen.getByText("You")).toBeTruthy();

    const box = (await screen.findByDisplayValue("Seller is open to offers.")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Edited summary" } });
    expect(box.value).toBe("Edited summary");
    expect(summarize).toHaveBeenCalledTimes(1);
    expect(send).not.toHaveBeenCalled();
  });

  it("shows a recoverable error when the summary fails", async () => {
    const id = nextConversation++;
    preview.mockResolvedValue(previewFor(id));
    summarize.mockRejectedValue(new Error("The AI summary could not be generated."));

    render(<LoftyExportDialog conversationId={id} accessToken="t" onClose={vi.fn()} />);

    expect(await screen.findByText("The AI summary could not be generated.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Generate summary" })).toBeTruthy();
  });

  it("does not regenerate a summary already saved with an export", async () => {
    const id = nextConversation++;
    preview.mockResolvedValue(previewFor(id, { export: status({ status: "failed", step: "mute", summary: "Saved" }) }));

    render(<LoftyExportDialog conversationId={id} accessToken="t" onClose={vi.fn()} />);

    expect(await screen.findByDisplayValue("Saved")).toBeTruthy();
    expect(summarize).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Retry send" })).toBeTruthy();
  });

  it("sends once on double click and shows the success state with a Lofty link", async () => {
    const id = nextConversation++;
    preview.mockResolvedValue(previewFor(id));
    summarize.mockResolvedValue({ summary: "Seller is open to offers." });
    let resolveSend: (value: LoftyExportStatus) => void = () => {};
    send.mockImplementation(() => new Promise((resolve) => { resolveSend = resolve; }));

    render(<LoftyExportDialog conversationId={id} accessToken="t" onClose={vi.fn()} />);
    await screen.findByDisplayValue("Seller is open to offers.");

    const button = screen.getByRole("button", { name: "Send to Lofty" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith(id, { summary: "Seller is open to offers." }, "t");

    resolveSend(status());
    expect(await screen.findByText("Sent to Lofty successfully")).toBeTruthy();
    expect(screen.getByText("✓ Anna muted")).toBeTruthy();
    const link = screen.getByRole("link", { name: "Open in Lofty ↗" });
    expect(link.getAttribute("href")).toBe("https://crm.lofty.com/admin/home/lead/detail/9001");
  });

  it("shows partial failure progress and lets the agent pick an ambiguous lead", async () => {
    const id = nextConversation++;
    preview.mockResolvedValue(previewFor(id));
    summarize.mockResolvedValue({ summary: "S" });
    send.mockResolvedValueOnce(
      status({
        status: "needs_review",
        step: "lead",
        lofty_lead_id: null,
        lofty_url: null,
        lead_action: null,
        mute_verified: false,
        notes_saved: 0,
        notes_total: 0,
        last_error: "2 Lofty leads match this owner's phone or email (111, 222).",
        candidate_lead_ids: ["111", "222"],
      }),
    );

    render(<LoftyExportDialog conversationId={id} accessToken="t" onClose={vi.fn()} />);
    await screen.findByDisplayValue("S");
    fireEvent.click(screen.getByRole("button", { name: "Send to Lofty" }));

    expect(await screen.findByText("Choose the matching Lofty lead")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Lead #222"));
    send.mockResolvedValueOnce(status({ lofty_lead_id: "222", lead_action: "existing" }));
    fireEvent.click(screen.getByRole("button", { name: "Send with selected lead" }));

    await waitFor(() => expect(send).toHaveBeenLastCalledWith(id, { summary: "S", lofty_lead_id: "222" }, "t"));
    expect(await screen.findByText("✓ Lead updated")).toBeTruthy();
  });

  it("explains when the organization is not connected and offers no send", async () => {
    const id = nextConversation++;
    preview.mockResolvedValue(previewFor(id, { available: false, unavailable_reason: "Your organization is not connected to Lofty." }));

    render(<LoftyExportDialog conversationId={id} accessToken="t" onClose={vi.fn()} />);

    expect(await screen.findByText("Your organization is not connected to Lofty.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send to Lofty" })).toBeNull();
    expect(summarize).not.toHaveBeenCalled();
  });

  it("closes on Cancel without sending", async () => {
    const id = nextConversation++;
    const onClose = vi.fn();
    preview.mockResolvedValue(previewFor(id));
    summarize.mockResolvedValue({ summary: "S" });

    render(<LoftyExportDialog conversationId={id} accessToken="t" onClose={onClose} />);
    await screen.findByDisplayValue("S");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });
});
