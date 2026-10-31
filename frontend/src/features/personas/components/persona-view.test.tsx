import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { PersonaView } from "./persona-view";
import * as api from "../api/persona-api";
import { AUTHENTIC_PERSONALITY_PROMPT } from "../authentic-personality-prompt";
import michael from "../__fixtures__/michael-kang-import.json";
import questionTemplate from "../__fixtures__/question-template-import.json";

vi.mock("@/features/auth/lib/auth-storage", () => ({ readAuthSession: () => ({ access_token: "test-token" }) }));
vi.mock("../api/persona-api", () => ({
  listPersonas: vi.fn(), getPersona: vi.fn(), listSubmissions: vi.fn(), listVersions: vi.fn(),
  saveDraft: vi.fn(), createPersona: vi.fn(), validateDraft: vi.fn(), submitDraft: vi.fn(),
  listExamples: vi.fn(), saveExampleList: vi.fn(), publishOwnPersona: vi.fn(), createExample: vi.fn(), updateExample: vi.fn(), deleteExample: vi.fn(),
  duplicateExample: vi.fn(), listSubmissionExamples: vi.fn(), activateVersion: vi.fn(),
  setDefault: vi.fn(), publishSubmission: vi.fn(), rejectSubmission: vi.fn(),
  listPersonaImports: vi.fn(), getPersonaImport: vi.fn(), uploadPersonaImport: vi.fn(),
  retryPersonaImport: vi.fn(), applyPersonaImport: vi.fn(), downloadPersonaTemplate: vi.fn(), listAllExamples: vi.fn(),
}));

const user = { id: 7, email: "agent@example.com", first_name: "Bobbie", last_name: "Fisher", full_name: "Bobbie Fisher", brokerage_id: "b-1", brokerage_name: "North Realty", role: "agent" as const, is_verified: true, is_active: true, created_at: "2026-01-01" };
const hob = { ...user, id: 9, role: "hob" as const };
const style = { personality: "professional and approachable", tone: "conversational", formality: 3, warmth: 3, directness: 3, message_length: "brief", sentence_pattern: "short", greeting_preference: "", vocabulary_preference: "plain language", question_behavior: "", follow_up_behavior: "", objection_handling: "", sign_off_preference: "", preferred_phrases: [], avoided_phrases: [] };
const draft = { persona_id: 3, revision: 1, base_version_id: null, full_name: "Bobbie Fisher", display_name: "Bobbie AI", professional_role: "Agent", professional_title: null, brokerage_name: "North Realty", biography: "", experience_summary: "", specialties: [], service_areas: [], sms_signature: "Bobbie Fisher", style_config: style, style_instructions: "", preferred_language: "en", profile_config: { playbook: { introduction: "Name, company, one reason." } }, review_status: "draft", submitted_at: null, updated_at: "2026-01-01" };
const detail = { id: 3, owner_user_id: 7, display_name: "Bobbie AI", status: "active", active_version_id: null, active_version_number: null, membership_ended_at: null, draft, active_version: null };
const stages = (state: (index: number) => string) => Object.fromEntries(["document_validation", "personality_extraction", "conversation_analysis", "completeness_assessment", "draft_preparation"].map((key, index) => [key, { status: state(index), started_at: null, completed_at: null, message: null }]));
const failedJob = {
  id: "e".repeat(32), persona_id: 3, base_revision: 1, file_name: "notes.txt", file_size: 100, file_hash: "f".repeat(64),
  status: "failed", current_stage: "personality_extraction", proposal: null, assessment: null, extraction_mode: "local",
  stages: stages((index) => index === 0 ? "completed" : index === 1 ? "failed" : "pending"),
  error_code: "template_not_recognized", error_message: "This document does not follow the ListingIQ Persona template", retryable: false,
  attempt_count: 1, created_at: "2026-01-01", updated_at: "2026-01-01", completed_at: "2026-01-01", applied_at: null,
};
const removedFields = ["AI display name", "Professional role", "Information status", "Transaction experience", "Transaction claim status", "Professional philosophy", "Brand positioning", "Business ownership / operator background"];

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });
  vi.mocked(api.listPersonas).mockResolvedValue([detail]);
  vi.mocked(api.getPersona).mockResolvedValue(detail as never);
  vi.mocked(api.listSubmissions).mockResolvedValue([]);
  vi.mocked(api.listVersions).mockResolvedValue([]);
  vi.mocked(api.listPersonaImports).mockResolvedValue([]);
  vi.mocked(api.downloadPersonaTemplate).mockResolvedValue(undefined);
  vi.mocked(api.listAllExamples).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100, revision: 1 });
  vi.mocked(api.listExamples).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, revision: 1 });
  vi.mocked(api.saveDraft).mockImplementation(async (_id, payload) => ({ ...draft, ...payload, revision: 2 } as never));
});
afterEach(cleanup);

async function expectSetupOnly() {
  expect(await screen.findByRole("heading", { name: "How would you like to begin?" })).toBeTruthy();
  expect(screen.getByText("PERSONAL AI SETUP")).toBeTruthy();
  expect(screen.getByText("My AI Persona / Choose setup method")).toBeTruthy();
  expect(screen.getAllByRole("article")).toHaveLength(2);
  expect(screen.getByRole("heading", { name: "Upload & Auto-Fill" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Set Up Manually" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Start Upload & Auto-Fill" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Continue manually" })).toBeTruthy();
  expect(screen.getByText("Continue through guided sections and save as you go.")).toBeTruthy();
  for (const gone of ["Continue editing", "Choose another setup method", "Create My AI Persona"]) expect(screen.queryByRole("button", { name: gone })).toBeNull();
  expect(screen.queryByText(/Working draft r/)).toBeNull();
  expect(screen.queryByText(/Resume auto-fill/)).toBeNull();
  expect(screen.queryByRole("heading", { name: "Persona approval queue" })).toBeNull();
}

describe("My AI Persona entry", () => {
  it("opens the two setup choices for a new user", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([]);
    render(<PersonaView user={user} />);
    await expectSetupOnly();
    expect(screen.queryByRole("button", { name: /Manage my Persona/ })).toBeNull();
  });

  it("opens the setup choices for an existing owner with a saved draft and a failed import", async () => {
    vi.mocked(api.listPersonaImports).mockResolvedValue([failedJob as never]);
    render(<PersonaView user={user} />);
    await expectSetupOnly();
    expect(screen.queryByRole("heading", { name: "Processing needs attention" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Who I am" })).toBeNull();
  });

  it("shows a HOB only their own Persona, versions and rollback, with no approval queue", async () => {
    const other = { ...detail, id: 4, owner_user_id: 11, display_name: "Agent Persona" };
    const published = { ...detail, display_name: "Claude AI", active_version_id: 21, active_version_number: 2 };
    vi.mocked(api.listPersonas).mockResolvedValue([published, other] as never);
    vi.mocked(api.getPersona).mockResolvedValue(published as never);
    vi.mocked(api.listVersions).mockResolvedValue([{ ...draft, id: 21, version_number: 2, published_by_user_id: 7, published_at: "2026-02-01" }, { ...draft, id: 20, version_number: 1, published_by_user_id: 7, published_at: "2026-01-01" }] as never);
    vi.mocked(api.activateVersion).mockResolvedValue({} as never);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<PersonaView user={{ ...hob, id: 7 }} />);
    await expectSetupOnly();
    fireEvent.click(screen.getByRole("button", { name: /Manage my Persona/ }));
    expect(screen.getByRole("heading", { name: "Your Profile" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Claude AI" })).toBeNull();
    expect(screen.getByText("Active Version 2")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Persona approval queue" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Earlier review requests" })).toBeNull();
    expect(screen.queryByText("Agent Persona")).toBeNull();
    expect(screen.queryByText(/approval|Your HOB/i)).toBeNull();
    expect(api.listSubmissions).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Published versions" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Continue editing" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Activate / restore" }));
    await waitFor(() => expect(api.activateVersion).toHaveBeenCalledWith(3, 1, "test-token"));
    expect(screen.queryByRole("button", { name: "Use for SMS" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Use for Email" })).toBeNull();
  });
  it("exits to the dashboard with Back and Cancel", async () => {
    const onExit = vi.fn();
    render(<PersonaView user={user} onExit={onExit} />);
    await expectSetupOnly();
    fireEvent.click(screen.getByRole("button", { name: "← Back" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onExit).toHaveBeenCalledTimes(2);
    expect(api.saveDraft).not.toHaveBeenCalled();
  });
});

describe("Upload & Auto-Fill", () => {
  it("shows the four guided steps, downloads the template and copies the complete prompt", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([]);
    const onExit = vi.fn();
    render(<PersonaView user={user} onExit={onExit} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start Upload & Auto-Fill" }));
    for (const heading of ["Download the ListingIQ Persona Template", "Copy the authentic-personality prompt", "Return and upload the completed document", "Process, then Apply & Review"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
    }
    expect(screen.queryByText(/ListingIQ processes only the document you return and upload/)).toBeNull();
    expect(screen.queryByText(/Use your own ChatGPT or Claude to complete the interview/)).toBeNull();
    expect((screen.getByLabelText("Completed Persona document") as HTMLInputElement).accept).toBe(".docx,.pdf,.txt,.md");
    fireEvent.click(screen.getByRole("button", { name: "Download Persona Template" }));
    expect(api.downloadPersonaTemplate).toHaveBeenCalledWith("test-token");
    fireEvent.click(screen.getByRole("button", { name: "Copy Prompt" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(AUTHENTIC_PERSONALITY_PROMPT));
    expect(AUTHENTIC_PERSONALITY_PROMPT.length).toBeGreaterThan(5000);
    for (const requiredText of ["automated outreach", "Preserve my natural wording", "Keep residential and commercial/business experience separate", "Do not invent missing answers", "Do not request a separate confirmation or approval", "editable Persona form"]) {
      expect(AUTHENTIC_PERSONALITY_PROMPT).toContain(requiredText);
    }
    expect(screen.getByRole("button", { name: "Prompt copied" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "← Back" }));
    await expectSetupOnly();
    fireEvent.click(screen.getByRole("button", { name: "Start Upload & Auto-Fill" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onExit).toHaveBeenCalledTimes(1);
    expect(api.createPersona).not.toHaveBeenCalled();
  });

  it("sends a completed document into the existing pipeline for a new Persona", async () => {
    const processingJob = { ...failedJob, id: "c".repeat(32), file_name: "completed-persona.docx", status: "processing", current_stage: "document_validation", error_code: null, error_message: null, stages: stages((index) => index === 0 ? "running" : "pending") } as never;
    vi.mocked(api.listPersonas).mockResolvedValue([]);
    vi.mocked(api.createPersona).mockResolvedValue(detail as never);
    vi.mocked(api.uploadPersonaImport).mockResolvedValue(processingJob);
    vi.mocked(api.getPersonaImport).mockResolvedValue(processingJob);
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start Upload & Auto-Fill" }));
    const completed = new File(["completed persona document"], "completed-persona.docx");
    fireEvent.change(screen.getByLabelText("Completed Persona document"), { target: { files: [completed] } });
    await waitFor(() => expect(api.uploadPersonaImport).toHaveBeenCalledWith(3, completed, "test-token"));
    expect(api.createPersona).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("heading", { name: "Building your Persona draft" })).toBeTruthy();
    expect(screen.getByText("Read locally from the ListingIQ template — no AI is used.")).toBeTruthy();
    for (const stage of ["Document validation", "Personality extraction", "Conversation analysis", "Completeness & quality", "Persona draft preparation"]) expect(screen.getByText(stage)).toBeTruthy();
  });

  it("reuses an existing Persona for upload without creating another", async () => {
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start Upload & Auto-Fill" }));
    expect(screen.getByRole("heading", { name: "Download the ListingIQ Persona Template" })).toBeTruthy();
    expect(api.createPersona).not.toHaveBeenCalled();
  });

  it("never offers AI interpretation for an unrecognized document", async () => {
    vi.mocked(api.listPersonaImports).mockResolvedValue([failedJob as never]);
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: /Manage my Persona/ }));
    fireEvent.click(screen.getByRole("button", { name: /Resume auto-fill/ }));
    expect(screen.getByRole("heading", { name: "Processing needs attention" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /AI-assisted/ })).toBeNull();
    expect(screen.queryByText(/AI provider/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Upload another document" }));
    expect(screen.getByRole("heading", { name: "Download the ListingIQ Persona Template" })).toBeTruthy();
  });


});

describe("Simplified manual setup", () => {
  it("shows four simple sections without the removed fields or sliders", async () => {
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    expect(await screen.findByRole("heading", { name: "Who I am" })).toBeTruthy();
    expect(api.createPersona).not.toHaveBeenCalled();
    const nav = screen.getByRole("navigation", { name: "Persona configuration sections" });
    expect(within(nav).getAllByRole("button").map((item) => item.textContent)).toEqual(["1Who I Am", "2My Background", "3How I Communicate", "4Review & Publish"]);
    for (const section of ["Who I Am", "My Background", "How I Communicate"]) {
      fireEvent.click(within(nav).getByRole("button", { name: new RegExp(section) }));
      for (const label of removedFields) expect(screen.queryByLabelText(label)).toBeNull();
      expect(document.querySelectorAll("input[type=range]")).toHaveLength(0);
      for (const slider of ["confidence", "warmth", "directness", "formality", "empathy", "energy"]) expect(screen.queryByText(new RegExp(`^${slider}`, "i"))).toBeNull();
    }
    expect(screen.getByText("How do you introduce yourself to a new client?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "+ Add custom example" })).toBeTruthy();
  });

  it("gives every visible input a working help icon", async () => {
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    const nav = await screen.findByRole("navigation", { name: "Persona configuration sections" });
    for (const section of ["Who I Am", "My Background"]) {
      fireEvent.click(within(nav).getByRole("button", { name: new RegExp(section) }));
      const card = document.querySelector(".persona-card")!;
      const inputs = card.querySelectorAll(".persona-field input, .persona-field textarea, .persona-field select, .persona-na input");
      const fields = card.querySelectorAll(".persona-field, .persona-na");
      expect(inputs.length).toBeGreaterThan(4);
      fields.forEach((field) => expect(field.querySelector(".persona-help-button")).toBeTruthy());
    }
    fireEvent.click(within(nav).getByRole("button", { name: /Who I Am/ }));
    // License inputs stay; the technical credential disclaimer is gone.
    for (const label of ["License / registration number", "Licensing jurisdiction", "License status"]) expect(screen.getByLabelText(label)).toBeTruthy();
    expect(screen.queryByText(/Credentials are stored as owner-provided information/)).toBeNull();
    expect(document.querySelector(".persona-card .persona-guidance")).toBeNull();
    expect(screen.getByLabelText("Current brokerage")).toHaveProperty("readOnly", true);
    expect((screen.getByLabelText("Current brokerage") as HTMLInputElement).value).toBe("North Realty");
    expect((screen.getByLabelText("Public professional email") as HTMLInputElement).value).toBe("agent@example.com");
    const help = screen.getByRole("button", { name: "Help: License / registration number" });
    fireEvent.click(help);
    const tip = screen.getByRole("tooltip");
    expect(tip.textContent).toContain("What it means:");
    expect(tip.textContent).toContain("What to enter:");
    expect(tip.textContent).toContain("Why it matters:");
    expect(tip.textContent).toContain("Example:");
    expect(help.getAttribute("aria-expanded")).toBe("true");
    expect(help.getAttribute("aria-describedby")).toBe(tip.id);
    fireEvent.keyDown(help, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.focus(screen.getByRole("button", { name: "Help: Full name" }));
    expect(screen.getByRole("tooltip").textContent).toContain("Your AI introduces itself");
    fireEvent.blur(screen.getByRole("button", { name: "Help: Full name" }));
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.click(within(nav).getByRole("button", { name: /How I Communicate/ }));
    const questions = Array.from(document.querySelectorAll(".persona-qa"));
    expect(questions).toHaveLength(7);
    questions.forEach((question) => expect(question.querySelector(".persona-qa-question .persona-help-button")).toBeTruthy());
    document.querySelectorAll(".persona-answer-options .persona-field").forEach((field) => expect(field.querySelector(".persona-help-button")).toBeTruthy());
  });

  it("saves the simplified fields with the full stored profile and a reasoned N/A", async () => {
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    fireEvent.change(await screen.findByLabelText("Professional title"), { target: { value: "Associate Broker" } });
    fireEvent.click(screen.getByLabelText("License number does not apply"));
    fireEvent.change(screen.getByLabelText("Why license number does not apply"), { target: { value: "Not licensed; broker's assistant" } });
    fireEvent.click(screen.getByRole("button", { name: "Save & next" }));
    await waitFor(() => expect(api.saveDraft).toHaveBeenCalled());
    const payload = vi.mocked(api.saveDraft).mock.calls[0][1] as Record<string, unknown> & { profile_config: { completeness_enforced: boolean; playbook: Record<string, string>; confirmations: Record<string, string>; na_reasons: Record<string, string>; identity: Record<string, string> } };
    expect(payload.professional_title).toBe("Associate Broker");
    expect(payload).not.toHaveProperty("display_name");
    expect(payload).not.toHaveProperty("professional_role");
    expect(payload.profile_config.completeness_enforced).toBe(true);
    expect(payload.profile_config.playbook.introduction).toBe("Name, company, one reason.");
    expect(payload.profile_config.confirmations.license_number).toBe("not_applicable");
    expect(payload.profile_config.na_reasons.license_number).toBe("Not licensed; broker's assistant");
    expect(await screen.findByRole("heading", { name: "My background" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Professional biography"), { target: { value: "Local market advisor." } });
    fireEvent.change(screen.getByLabelText("Primary language"), { target: { value: "ko" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(api.saveDraft).toHaveBeenCalledTimes(2));
    const second = vi.mocked(api.saveDraft).mock.calls[1][1];
    expect(second.biography).toBe("Local market advisor.");
    expect(second.preferred_language).toBe("ko");
  });

  it("captures answers under each question and saves them in one step", async () => {
    vi.mocked(api.saveExampleList).mockImplementation(async (_id, _revision, examples) => ({ items: examples.map((item, index) => ({ ...item, id: 50 + index, position: index, context: "", style_explanation: "", provenance: "user_written", owner_approved: true, review_flags: [] })), total: examples.length, page: 1, page_size: 100, revision: 2 } as never));
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    fireEvent.click(screen.getByRole("button", { name: /How I Communicate/ }));
    // Each question once, with its answer box directly underneath; nothing to approve or count.
    for (const question of ["How do you introduce yourself to a new client?", "How would you suggest an appointment?"]) expect(screen.getAllByRole("heading", { name: question })).toHaveLength(1);
    expect(screen.queryByText("Add another real example of how you naturally communicate.")).toBeNull();
    for (const gone of [/Add my response/, /Add another$/, /Approve/, /saved$/]) expect(screen.queryByRole("button", { name: gone })).toBeNull();
    const reply = "Hi, Bobbie Fisher with North Realty 😊\n\n1. Quick question first.\n안녕하세요!";
    fireEvent.change(screen.getByLabelText("How do you introduce yourself to a new client?"), { target: { value: reply } });
    expect((screen.getByLabelText("How do you introduce yourself to a new client?") as HTMLTextAreaElement).value).toBe(reply);
    fireEvent.click(screen.getByRole("button", { name: "+ Add custom example" }));
    fireEvent.change(screen.getByLabelText("Situation"), { target: { value: "Price drop alert" } });
    fireEvent.change(screen.getByLabelText("Price drop alert (your example)"), { target: { value: "Good news — the price just dropped!" } });
    fireEvent.click(screen.getByRole("button", { name: "Save & next" }));
    await waitFor(() => expect(api.saveExampleList).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.saveExampleList).mock.calls[0].slice(0, 2)).toEqual([3, 1]);
    expect(vi.mocked(api.saveExampleList).mock.calls[0][2]).toEqual([
      { scenario: "Introducing myself to a new client", channel: "sms", language: "en", customer_message: "", preferred_response: reply, provenance: "user_written" },
      { scenario: "Price drop alert", channel: "sms", language: "en", customer_message: "", preferred_response: "Good news — the price just dropped!", provenance: "user_written" },
    ]);
    expect(await screen.findByRole("heading", { name: "Review & Publish" })).toBeTruthy();
    expect(api.createExample).not.toHaveBeenCalled();
    expect(api.updateExample).not.toHaveBeenCalled();
    expect(api.submitDraft).not.toHaveBeenCalled();
  });
  it("protects unsaved manual edits and discards only local changes", async () => {
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Unsaved Name" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    expect(screen.getByRole("dialog", { name: "Leave without saving?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stay and continue editing" }));
    expect((screen.getByLabelText("Full name") as HTMLInputElement).value).toBe("Unsaved Name");
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Discard unsaved edits and leave" }));
    await expectSetupOnly();
    expect(api.saveDraft).not.toHaveBeenCalled();
  });


});

const completedJob = {
  ...failedJob, id: "a".repeat(32), file_name: "michael-kang.md", status: "completed", current_stage: "draft_preparation",
  error_code: null, error_message: null, retryable: false, stages: stages(() => "completed"),
  proposal: michael.proposal, assessment: michael.assessment,
};
const KOREAN = michael.proposal.examples[2].preferred_response;

async function openAutofill() {
  vi.mocked(api.listPersonaImports).mockResolvedValue([completedJob as never]);
  render(<PersonaView user={user} />);
  fireEvent.click(await screen.findByRole("button", { name: /Manage my Persona/ }));
  fireEvent.click(screen.getByRole("button", { name: /Resume auto-fill · completed/ }));
  expect(screen.getByRole("heading", { name: "Your Persona form is filled in" })).toBeTruthy();
  expect(screen.getByText(/Your document has been read, including 11 conversation examples/)).toBeTruthy();
  expect(screen.queryByText(/claims|additional details/i)).toBeNull();
  expect(screen.queryByText(/source evidence/i)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Review & Edit Persona" }));
  expect(await screen.findByRole("heading", { name: "Who I am" })).toBeTruthy();
}
/** Review & Publish shows the three sections with Edit and the actions, and nothing else. */
function expectMinimalReview() {
  expect(screen.getByRole("heading", { name: "Review & Publish" })).toBeTruthy();
  expect(screen.getByText("Review your information before publishing. You can return to any section to make changes.")).toBeTruthy();
  for (const section of ["Who I Am", "My Background", "How I Communicate"]) expect(screen.getByRole("button", { name: `Edit ${section}` })).toBeTruthy();
  for (const gone of [/Unverified claims/, /Your review/, /I confirm this is accurate/, /This is inaccurate/, /Correction or note/, /Additional details/, /Missing required information/, /policy wording/, /quiet-hour/i, /consent/i, /opt-out/i, /not independently verified/i]) expect(screen.queryByText(gone)).toBeNull();
  expect(document.querySelector(".persona-card details, .persona-card .persona-guidance, .persona-card .persona-claim, .persona-card .persona-consent")).toBeNull();
}
const value = (label: string) => (screen.getByLabelText(label) as HTMLInputElement).value;
const statusOf = (label: string) => screen.getByLabelText(label).closest(".persona-field")!.querySelector(".persona-field-status")?.textContent;

describe("Upload & Auto-Fill opens the same editable form", () => {
  it("fills every mapped field, keeps saved values until the owner chooses, and leaves missing data missing", async () => {
    await openAutofill();
    expect(screen.getByText(/Filled in from/)).toBeTruthy();
    expect(screen.getByText("Not saved")).toBeTruthy();
    // A saved value is never overwritten silently.
    expect(value("Full name")).toBe("Bobbie Fisher");
    expect(screen.getByRole("group", { name: "Full name: saved value differs from the document" }).textContent).toContain("Michael Kang");
    fireEvent.click(screen.getByRole("button", { name: "Use document value" }));
    expect(value("Full name")).toBe("Michael Kang");
    // Only genuinely missing required fields carry a marker.
    expect(statusOf("Full name")).toBeUndefined();
    expect(value("Professional title")).toBe("Founder & Team Lead, SpaceMatch Inc. (SpaceMatch Chicago real estate team)");
    expect(statusOf("Professional title")).toBeUndefined();
    expect(value("Current brokerage")).toBe("North Realty");
    expect(value("Licensing jurisdiction")).toBe("Illinois");
    expect(statusOf("Licensing jurisdiction")).toBeUndefined();
    // The document has no license number: it stays missing, not "not applicable".
    expect(value("License / registration number")).toBe("");
    expect(statusOf("License / registration number")).toBe("Missing · required");
    expect((screen.getByLabelText("License number does not apply") as HTMLInputElement).checked).toBe(false);
    expect(value("Public professional email")).toBe("michael@spacematchinc.com");
    expect(value("Public professional phone")).toBe("(773) 344-0738");
    expect(value("Professional introduction")).toBe("Michael Kang with SpaceMatch Inc. in Chicago.");
    // No field-by-field confirmation: saving the form confirms what the owner keeps.
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("heading", { name: "My background" })).toBeTruthy();
    expect(value("Professional biography")).toMatch(/^Michael's background is computer science and IT/);
    expect(value("Specialties (comma separated)")).toContain("coin laundries/laundromats");
    expect(value("Service areas (comma separated)")).toContain("Chicago and suburbs (Cook, DuPage, Lake, Kane and nearby counties)");
    expect(value("Expertise and differentiators (comma separated)")).toContain("strong financing knowledge (SBA, DSCR, conventional)");
    expect(value("Target audiences (comma separated)")).toContain("Korean-speaking owners");
    expect(value("Other languages (comma separated)")).toBe("Korean (fluent)");
    expect(api.saveDraft).not.toHaveBeenCalled();
    expect(api.applyPersonaImport).not.toHaveBeenCalled();
  });

  it("shows all 11 imported examples as plain editable answers and applies exactly what the owner reviewed", { timeout: 30000 }, async () => {
    vi.mocked(api.applyPersonaImport).mockResolvedValue({ ...draft, revision: 2, full_name: "Bobbie Fisher" } as never);
    await openAutofill();
    fireEvent.click(screen.getByRole("button", { name: /How I Communicate/ }));
    // Michael's examples use his own situations, so they appear under "Your own examples", each editable.
    const answers = () => Array.from(document.querySelectorAll<HTMLTextAreaElement>(".persona-answer textarea[aria-label]")).filter((item) => !item.closest(".persona-answer-options"));
    expect(await screen.findByRole("heading", { name: "Your own examples" })).toBeTruthy();
    const imported = answers().filter((item) => item.value);
    expect(imported).toHaveLength(11);
    expect(imported.map((item) => item.value)).toContain(KOREAN);
    expect(imported.map((item) => item.value)).toContain(michael.proposal.examples[9].preferred_response);
    // No import internals in the editor.
    for (const text of [/Illustrative · not a real conversation/, /Channel was not stated/, /Language was not stated/, /authentic or illustrative/, /First-touch outreach/, /No customer message/, /Needs your approval/, /\d saved/]) expect(screen.queryByText(text)).toBeNull();
    expect(document.querySelector(".persona-card .persona-flags")).toBeNull();
    expect(Array.from(document.querySelectorAll(".persona-card button")).map((button) => button.textContent ?? "").filter((text) => /Approve|Add another$|^Confirm$/.test(text))).toEqual([]);

    fireEvent.change(screen.getByLabelText("Valuation question (your example)"), { target: { value: "Let me pull the real numbers first. Call or text?" } });
    const notInterested = screen.getByLabelText("Not interested (your example)").closest(".persona-answer") as HTMLElement;
    fireEvent.click(within(notInterested).getByRole("button", { name: "Remove" }));
    expect(screen.queryByLabelText("Not interested (your example)")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    expectMinimalReview();
    expect(screen.getByText("Still needed: License number, License status")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Publish My Persona" })).toHaveProperty("disabled", true);

    fireEvent.click(screen.getByRole("button", { name: "Apply & Save Draft" }));
    await waitFor(() => expect(api.applyPersonaImport).toHaveBeenCalledTimes(1));
    const [personaId, jobId, revision, token, reviewed] = vi.mocked(api.applyPersonaImport).mock.calls[0];
    expect([personaId, jobId, revision, token]).toEqual([3, "a".repeat(32), 1, "test-token"]);
    expect(reviewed!.examples).toHaveLength(10);
    expect(reviewed!.examples[3]).toMatchObject({ source_index: 3, preferred_response: "Let me pull the real numbers first. Call or text?" });
    expect(reviewed!.examples.some((item) => item.preferred_response === KOREAN)).toBe(true);
    expect(reviewed!.examples.some((item) => item.source_index === 7)).toBe(false);
    expect(reviewed!.examples.every((item) => !("owner_approved" in item))).toBe(true);
    expect(reviewed!.profile.identity.license_number).toBe("");
    expect(reviewed!.profile.confirmations.professional_title).toBe("owner_confirmed");
    expect(await screen.findByText(/Your Persona is saved as a working draft. Publish it when you are ready, or keep editing. Nothing was submitted or published./)).toBeTruthy();
    expect(api.submitDraft).not.toHaveBeenCalled();
    expect(api.publishSubmission).not.toHaveBeenCalled();
  });
  it("keeps the owner's edits on screen when Apply is refused", async () => {
    vi.mocked(api.applyPersonaImport).mockRejectedValue(new Error("The working draft changed after upload; review the latest draft and upload again"));
    await openAutofill();
    fireEvent.change(screen.getByLabelText("Professional title"), { target: { value: "Founder" } });
    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    fireEvent.click(screen.getByRole("button", { name: "Apply & Save Draft" }));
    expect(await screen.findByText(/The working draft changed after upload.*Your edits are still on this screen./)).toBeTruthy();
    expect(screen.getByText("Not saved")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Edit Who I Am" }));
    expect(value("Professional title")).toBe("Founder");
  });

  it("asks before leaving the unsaved auto-filled form", async () => {
    await openAutofill();
    fireEvent.click(screen.getAllByRole("button", { name: "Cancel" })[0]);
    expect(screen.getByRole("dialog", { name: "Leave without saving?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Discard unsaved edits and leave" }));
    await expectSetupOnly();
    expect(api.applyPersonaImport).not.toHaveBeenCalled();
  });

  it("opens the filled form by itself when processing finishes", async () => {
    const running = { ...completedJob, status: "processing", stages: stages((index) => index < 2 ? "completed" : index === 2 ? "running" : "pending") };
    vi.mocked(api.uploadPersonaImport).mockResolvedValue(running as never);
    vi.mocked(api.getPersonaImport).mockResolvedValue(completedJob as never);
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start Upload & Auto-Fill" }));
    fireEvent.change(screen.getByLabelText("Completed Persona document"), { target: { files: [new File(["x"], "michael-kang.md")] } });
    expect(await screen.findByRole("heading", { name: "Building your Persona draft" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: "Who I am" }, { timeout: 4000 })).toBeTruthy();
    expect(value("Licensing jurisdiction")).toBe("Illinois");
  });

  it("accepts the auto-filled form from its first section without stepping through a second review", async () => {
    const running = { ...completedJob, status: "processing", stages: stages((index) => index < 2 ? "completed" : index === 2 ? "running" : "pending") };
    vi.mocked(api.uploadPersonaImport).mockResolvedValue(running as never);
    vi.mocked(api.getPersonaImport).mockResolvedValue(completedJob as never);
    vi.mocked(api.applyPersonaImport).mockResolvedValue(draft as never);
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start Upload & Auto-Fill" }));
    fireEvent.change(screen.getByLabelText("Completed Persona document"), { target: { files: [new File(["x"], "michael-kang.md")] } });
    expect(await screen.findByRole("heading", { name: "Who I am" }, { timeout: 4000 })).toBeTruthy();
    expect(value("Licensing jurisdiction")).toBe("Illinois");
    expect(api.applyPersonaImport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Apply & Save Draft" }));
    await waitFor(() => expect(api.applyPersonaImport).toHaveBeenCalledTimes(1));
    expect(api.publishOwnPersona).not.toHaveBeenCalled(); // publishing stays a separate, explicit action
    expect(await screen.findByRole("button", { name: "Publish My Persona" })).toBeTruthy();
  });

  it("keeps Review & Publish minimal, with Edit links, and submits only when the saved draft is complete", async () => {
    const sourceReview = {
      imports: [{ job_id: "a".repeat(32), file_name: "michael.md", sha256: "b".repeat(64) }],
      claims: [{ statement: "Sold about 120 laundromats", source_status: "self_reported", field_path: "profile_config.background.achievements", source_excerpt: "(self-reported)", owner_status: "unreviewed", owner_note: "" }],
      unmapped_details: [{ section: "Who I Am", detail: "Team line and Instagram account", owner_status: "unreviewed" }],
      evidence: [{ field_path: "draft.biography", source_excerpt: "Twelve years", confidence: "high", needs_owner_confirmation: false }], missing_questions: [], contradictions: ["Kept your saved display name"], completeness_warnings: [],
    };
    vi.mocked(api.getPersona).mockResolvedValue({ ...detail, draft: { ...draft, profile_config: { source_review: sourceReview } } } as never);
    const blocked = { valid: false, errors: ["who_i_am.license_number is required or needs a reasoned N/A"], warnings: [], revision: 1, missing_count: 1, sections: { who_i_am: { complete: false, missing: ["license_number"] }, professional_background: { complete: true, missing: [] }, conversation_examples: { complete: true, missing: [] } } };
    vi.mocked(api.validateDraft).mockResolvedValue(blocked);
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    expect(await screen.findByText("Still needed: License number")).toBeTruthy();
    expect(screen.getByText("Complete before publishing: License number.")).toBeTruthy();
    expectMinimalReview();
    expect(screen.queryByText("Sold about 120 laundromats")).toBeNull();
    expect(screen.queryByText("Team line and Instagram account")).toBeNull();
    expect(screen.queryByText("Kept your saved display name")).toBeNull();
    expect(screen.getByRole("button", { name: "Publish My Persona" })).toHaveProperty("disabled", true);

    // Edit goes to the section; the missing field is marked beside its input.
    fireEvent.click(screen.getByRole("button", { name: "Edit Who I Am" }));
    expect(screen.getByRole("heading", { name: "Who I am" })).toBeTruthy();
    expect(screen.getByLabelText("License / registration number").closest(".persona-field")!.textContent).toContain("Missing · required");
    fireEvent.change(screen.getByLabelText("License / registration number"), { target: { value: "475.123456" } });
    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    expect(screen.getByRole("button", { name: "Publish My Persona" })).toHaveProperty("disabled", true);
    expect(screen.getByText("Save changes before publishing.")).toBeTruthy();
    vi.mocked(api.validateDraft).mockResolvedValue({ ...blocked, revision: 2, valid: true, errors: [], sections: { who_i_am: { complete: true, missing: [] }, professional_background: { complete: true, missing: [] }, conversation_examples: { complete: true, missing: [] } } });
    fireEvent.click(screen.getByRole("button", { name: "Save Draft" }));
    await waitFor(() => expect(api.saveDraft).toHaveBeenCalledTimes(1));
    // Saving a draft never publishes.
    expect(api.publishOwnPersona).not.toHaveBeenCalled();
    // The stored review record is sent back untouched.
    const savedProfile = vi.mocked(api.saveDraft).mock.calls[0][1].profile_config as { source_review: typeof sourceReview; identity: Record<string, string> };
    expect(savedProfile.source_review).toEqual(sourceReview);
    expect(savedProfile.identity.license_number).toBe("475.123456");

    fireEvent.click(screen.getByRole("button", { name: "Edit My Background" }));
    expect(screen.getByRole("heading", { name: "My background" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    fireEvent.click(screen.getByRole("button", { name: "Edit How I Communicate" }));
    expect(screen.getByRole("heading", { name: "How I communicate" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Publish My Persona" })).toHaveProperty("disabled", false));
    expect(screen.getByText("Ready to publish.")).toBeTruthy();
    const publishedDraft = { ...draft, revision: 2, base_version_id: 9, review_status: "published" };
    const publishedVersion = { ...publishedDraft, id: 9, version_number: 1, published_by_user_id: 7, published_at: "2026-10-29" };
    vi.mocked(api.publishOwnPersona).mockResolvedValue(publishedVersion as never);
    vi.mocked(api.getPersona).mockResolvedValue({ ...detail, active_version_id: 9, active_version_number: 1, draft: publishedDraft, active_version: publishedVersion } as never);
    vi.mocked(api.listVersions).mockResolvedValue([publishedVersion] as never);
    fireEvent.click(screen.getByRole("button", { name: "Publish My Persona" }));
    await waitFor(() => expect(api.publishOwnPersona).toHaveBeenCalledWith(3, 2, "test-token"));
    expect(await screen.findByText("Your AI Persona has been published successfully.")).toBeTruthy();
    expect(screen.getByText("Active Version 1")).toBeTruthy();
    expect(screen.getByText("Version 1")).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(screen.queryByText(/HOB/)).toBeNull();
    expect(api.submitDraft).not.toHaveBeenCalled();
    expect(api.publishSubmission).not.toHaveBeenCalled();
  });
  it("does not publish when the readiness revision is stale", async () => {
    vi.mocked(api.validateDraft).mockResolvedValue({ valid: true, errors: [], warnings: [], revision: 2, missing_count: 0, sections: { who_i_am: { complete: true, missing: [] }, professional_background: { complete: true, missing: [] }, conversation_examples: { complete: true, missing: [] } } });
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    expect(await screen.findByText("Publication readiness is out of date. Check it again.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Publish My Persona" })).toHaveProperty("disabled", true);
    expect(api.publishOwnPersona).not.toHaveBeenCalled();
  });
  it("keeps several saved answers to one question and saves edits together", async () => {
    const saved = [
      { id: 2, position: 0, scenario: "Upset customer", channel: "sms", language: "en", customer_message: "Nobody called me.", preferred_response: "I'm so sorry.", context: "", style_explanation: "", provenance: "illustrative", owner_approved: true, review_flags: ["First-touch outreach: confirm consent"] },
      { id: 3, position: 1, scenario: "Upset customer", channel: "email", language: "en", customer_message: "", preferred_response: "Dear Sam,\n\nI apologise.", context: "", style_explanation: "", provenance: "authentic", owner_approved: true, review_flags: [] },
    ];
    vi.mocked(api.listAllExamples).mockResolvedValue({ items: saved, total: 2, page: 1, page_size: 100, revision: 1 } as never);
    vi.mocked(api.saveExampleList).mockResolvedValue({ items: saved, total: 2, page: 1, page_size: 100, revision: 2 } as never);
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
    fireEvent.click(screen.getByRole("button", { name: /How I Communicate/ }));
    expect(screen.getAllByRole("heading", { name: "How would you respond to an upset customer?" })).toHaveLength(1);
    expect((screen.getByLabelText("How would you respond to an upset customer? (answer 2)") as HTMLTextAreaElement).value).toBe("Dear Sam,\n\nI apologise.");
    expect(screen.queryByText(/First-touch outreach/)).toBeNull();
    expect(screen.getByText("SMS · English · replying to a message")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("How would you respond to an upset customer? (answer 1)"), { target: { value: "I'm so sorry!! Calling you now." } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(api.saveExampleList).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.saveExampleList).mock.calls[0][2]).toEqual([
      { id: 2, scenario: "Upset customer", channel: "sms", language: "en", customer_message: "Nobody called me.", preferred_response: "I'm so sorry!! Calling you now." },
      { id: 3, scenario: "Upset customer", channel: "email", language: "en", customer_message: "", preferred_response: "Dear Sam,\n\nI apologise." },
    ]);
  });

  it("grows answer boxes to fit their text", async () => {
    const original = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "scrollHeight");
    Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", { configurable: true, get(this: HTMLTextAreaElement) { return 24 * Math.max(1, this.value.split("\n").length); } });
    try {
      render(<PersonaView user={user} />);
      fireEvent.click(await screen.findByRole("button", { name: "Continue manually" }));
      fireEvent.click(screen.getByRole("button", { name: /How I Communicate/ }));
      const box = screen.getByLabelText("How do you introduce yourself to a new client?") as HTMLTextAreaElement;
      expect(box.style.height).toBe("24px");
      fireEvent.change(box, { target: { value: "Line one\nLine two\nLine three\nLine four\nLine five" } });
      expect((screen.getByLabelText("How do you introduce yourself to a new client?") as HTMLTextAreaElement).style.height).toBe("120px");
    } finally {
      if (original) Object.defineProperty(HTMLTextAreaElement.prototype, "scrollHeight", original);
    }
  });
});

describe("Word template answers fill the How I Communicate prompts", () => {
  it("shows each Word-template answer directly under its question and applies the owner's edits", async () => {
    const job = { ...completedJob, id: "d".repeat(32), file_name: "dana.docx", proposal: questionTemplate.proposal, assessment: questionTemplate.assessment };
    vi.mocked(api.listPersonaImports).mockResolvedValue([job as never]);
    vi.mocked(api.applyPersonaImport).mockResolvedValue({ ...draft, revision: 2 } as never);
    render(<PersonaView user={user} />);
    fireEvent.click(await screen.findByRole("button", { name: /Manage my Persona/ }));
    fireEvent.click(screen.getByRole("button", { name: /Resume auto-fill · completed/ }));
    fireEvent.click(screen.getByRole("button", { name: "Review & Edit Persona" }));
    fireEvent.click(await screen.findByRole("button", { name: /How I Communicate/ }));
    const questions = [
      "How do you introduce yourself to a new client?", "How would you reply when someone asks about their property's value?",
      "What would you say to someone who is interested but hesitant?", "How would you respond to a commission or pricing objection?",
      "How would you follow up with someone who hasn't replied?", "How would you respond to an upset customer?", "How would you suggest an appointment?",
    ];
    questions.forEach((question, index) => {
      expect(screen.getAllByRole("heading", { name: question })).toHaveLength(1);
      expect((screen.getByLabelText(question) as HTMLTextAreaElement).value).toBe(questionTemplate.proposal.examples[index].preferred_response);
    });
    for (const extra of questionTemplate.proposal.examples.slice(7)) expect((screen.getByLabelText(`${extra.scenario} (your example)`) as HTMLTextAreaElement).value).toBe(extra.preferred_response);
    for (const text of [/\d saved/, /Needs your approval/, /Illustrative · not/, /not stated/, /First-touch/]) expect(screen.queryByText(text)).toBeNull();

    const appointment = screen.getByLabelText("How would you suggest an appointment?").closest(".persona-answer") as HTMLElement;
    expect(within(appointment).getByText("SMS · Korean")).toBeTruthy();
    fireEvent.change(within(appointment).getByLabelText("Channel"), { target: { value: "email" } });
    fireEvent.change(screen.getByLabelText("What would you say to someone who is interested but hesitant?"), { target: { value: "No rush! Want me to check back next month?" } });

    fireEvent.click(screen.getByRole("button", { name: /Review & Publish/ }));
    fireEvent.click(screen.getByRole("button", { name: "Apply & Save Draft" }));
    await waitFor(() => expect(api.applyPersonaImport).toHaveBeenCalledTimes(1));
    const reviewed = vi.mocked(api.applyPersonaImport).mock.calls[0][4]!;
    expect(reviewed.examples).toHaveLength(10);
    expect(reviewed.examples.map((item) => item.scenario).slice(0, 7)).toEqual([
      "Introducing myself to a new client", "Property value question", "Interested but hesitant", "Commission or pricing objection",
      "Following up after no reply", "Upset customer", "Suggesting an appointment",
    ]);
    expect(reviewed.examples[6]).toMatchObject({ channel: "email", language: "ko", preferred_response: "안녕하세요 사장님! 내일 오후 2시 괜찮으세요?" });
    expect(reviewed.examples[2].preferred_response).toBe("No rush! Want me to check back next month?");
    expect(reviewed.examples[1].preferred_response).toBe("Great question… let me pull 3 recent sales first:\n1. Same block\n2. Same size\nThen I'll text you a range, OK?");
    expect(api.submitDraft).not.toHaveBeenCalled();
  });
});
