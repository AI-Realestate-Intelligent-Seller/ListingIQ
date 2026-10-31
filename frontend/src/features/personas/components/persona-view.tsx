"use client";

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ApiRequestError } from "@/lib/api/http-client";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import type { AuthUser } from "@/features/auth/types/auth.types";
import { AUTHENTIC_PERSONALITY_PROMPT } from "../authentic-personality-prompt";
import {
  activateVersion, createPersona,
  applyPersonaImport, downloadPersonaTemplate, getPersona, getPersonaImport, listAllExamples, listPersonaImports, listPersonas,
  listVersions, publishOwnPersona, saveDraft, saveExampleList,
  retryPersonaImport, uploadPersonaImport, validateDraft,
} from "../api/persona-api";
import type { FormExample, PersonaDetail, PersonaDraft, PersonaImportJob, PersonaValidation, PersonaVersion, ProfileConfig } from "../types";
import { FIELD_LABELS, emptySourceReview } from "./import-review";
import { HelpTip, type Help } from "./help-tip";
import { AutoTextarea } from "./auto-textarea";
import {
  FORM_FIELDS, buildPreview, confirmFilled, examplesToSave, fieldStatus, isRequired, localReadiness, reviewedImport, splitList, writeField,
  type Conflict, type FieldKey, type FieldStatus, type ImportPreview, type Readiness,
} from "../persona-form";

const sections = [
  ["identity", "Who I Am"], ["background", "My Background"],
  ["examples", "How I Communicate"], ["review", "Review & Publish"],
] as const;
type SectionKey = (typeof sections)[number][0];
// Server completeness sections shown against each step.
const validationSection: Partial<Record<SectionKey, string>> = {
  identity: "who_i_am", background: "professional_background", examples: "conversation_examples",
};

// Stored playbook keys. They are no longer edited in the form but are kept intact.
const playbookLabels: [string, string][] = [
  ["introduction", "Introducing myself"], ["trust_building", "Building trust"], ["qualifying_questions", "Qualifying questions"],
  ["valuation_conversations", "Valuation conversations"], ["buyer_vs_seller", "Buyers vs. sellers"], ["investors_and_owners", "Investors and owners"],
  ["pricing_disagreements", "Pricing disagreements"], ["objections", "Objections"], ["follow_up", "Follow-up"],
  ["appointments", "Appointments"], ["sensitive_escalation", "Escalating sensitive matters"], ["human_handoff", "Handing off to a person"],
];

const emptyProfile = (): ProfileConfig => ({
  schema_version: 2, completeness_enforced: true,
  identity: { license_number: "", licensing_jurisdiction: "", licensing_status: "", public_email: "", public_phone: "", professional_intro: "", brand_identity: "" },
  background: { years_experience: null, previous_experience: "", target_audiences: [], industry_expertise: [], differentiators: [], unique_selling_proposition: "", achievements: [], transaction_experience: "", transaction_claim_status: "missing", professional_philosophy: "", brand_positioning: "", business_background: "" },
  communication: { professional_casual_behavior: "", confidence: 3, warmth: 3, directness: 3, formality: 3, empathy: 3, energy: 3, preferred_sms_length: "brief", preferred_email_length: "balanced", paragraph_style: "", questions_per_message: 1, emoji_usage: "", industry_terminology: "", other_languages: [], multilingual_preferences: "", language_switching: "", cultural_preferences: "", signature_phrases: [], unnatural_expressions: [], brand_statements: [], hesitant_response: "", concept_explanation: "", uncertainty_behavior: "", negative_reply_behavior: "", next_step_behavior: "", human_involvement: "" },
  playbook: Object.fromEntries(playbookLabels.map(([key]) => [key, ""])), confirmations: {}, na_reasons: {},
  source_review: emptySourceReview(),
});

/** Every stored value is kept, including fields the simplified form no longer shows. */
function normalizeProfile(value?: Partial<ProfileConfig> | null, user?: AuthUser): ProfileConfig {
  const base = emptyProfile();
  const profile = { ...base, ...value, completeness_enforced: true, identity: { ...base.identity, ...value?.identity }, background: { ...base.background, ...value?.background }, communication: { ...base.communication, ...value?.communication }, playbook: { ...base.playbook, ...value?.playbook }, confirmations: value?.confirmations ?? {}, na_reasons: value?.na_reasons ?? {}, source_review: { ...base.source_review, ...value?.source_review } };
  // Known account details are offered instead of asking for them again.
  if (user?.email && !profile.identity.public_email) profile.identity = { ...profile.identity, public_email: user.email };
  return profile;
}

function asList(value: unknown): string[] { return Array.isArray(value) ? value.map(String) : []; }
function date(value: string | null | undefined): string { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—"; }
const readable = (error: string) => FIELD_LABELS[error.replace(/ is required.*$/, "")] ? `${FIELD_LABELS[error.replace(/ is required.*$/, "")]} is required` : error.replaceAll("_", " ");

type PersonaScreen = "setup" | "upload" | "processing" | "manual" | "manage";
type FieldState = { status: FieldStatus; required: boolean; conflict?: Conflict; onResolve: (useImported: boolean) => void };

export function PersonaView({ user, onDirtyChange, onExit }: { user: AuthUser; onDirtyChange?: (dirty: boolean) => void; onExit?: () => void }) {
  const token = readAuthSession()?.access_token ?? "";
  const [persona, setPersona] = useState<PersonaDetail | null>(null);
  const [draft, setDraft] = useState<PersonaDraft | null>(null);
  const [profile, setProfile] = useState<ProfileConfig>(emptyProfile);
  const [section, setSection] = useState<SectionKey>("identity");
  const [status, setStatus] = useState<"loading" | "idle" | "saving" | "saved" | "error" | "stale">("loading");
  const [message, setMessage] = useState("");
  const [validation, setValidation] = useState<PersonaValidation | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(false);
  const [versions, setVersions] = useState<PersonaVersion[]>([]);
  // Every visit starts at the setup chooser; saved work stays reachable from Manage.
  const [screen, setScreen] = useState<PersonaScreen>("setup");
  const [importJob, setImportJob] = useState<PersonaImportJob | null>(null);
  // An import shown in the form but not yet applied. Nothing in it is saved.
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [savedDraft, setSavedDraft] = useState<PersonaDraft | null>(null);
  const [savedProfile, setSavedProfile] = useState<ProfileConfig>(emptyProfile);
  const [draftDirty, setDraftDirty] = useState(false);
  // The How I Communicate step edits a working copy that is saved in one step.
  const [workingExamples, setWorkingExamples] = useState<FormExample[]>([]);
  const [savedExamples, setSavedExamples] = useState<FormExample[]>([]);
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const manualOrigin = useRef<"setup" | "manage" | "processing">("setup");
  const watchedImport = useRef(false);
  const exampleDirty = !preview && JSON.stringify(workingExamples) !== JSON.stringify(savedExamples);
  const dirty = draftDirty || exampleDirty;
  const exit = useCallback(() => { onExit?.(); }, [onExit]);

  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty, onDirtyChange]);
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);

  const fail = useCallback((error: unknown) => {
    const stale = error instanceof ApiRequestError && error.status === 409;
    setStatus(stale ? "stale" : "error");
    setMessage(stale ? "This draft changed in another tab. Your inputs remain here; reload before saving again." : error instanceof Error ? error.message : "The request could not be completed.");
  }, []);

  const adopt = useCallback((next: PersonaDraft | null) => {
    const normalized = normalizeProfile(next?.profile_config, user);
    setDraft(next); setSavedDraft(next); setProfile(normalized); setSavedProfile(normalized);
  }, [user]);

  const load = useCallback(async () => {
    if (!token) return;
    setStatus("loading");
    try {
      const personas = await listPersonas(token);
      const own = personas.find((item) => item.owner_user_id === user.id);
      if (own) {
        const [detail, versionRows, importRows] = await Promise.all([getPersona(own.id, token), listVersions(own.id, token), listPersonaImports(own.id, token)]);
        setPersona(detail); adopt(detail.draft); setVersions(versionRows);
        const examples = (await listAllExamples(own.id, token))?.items ?? [];
        setWorkingExamples(examples); setSavedExamples(examples);
        const latest = importRows[0];
        // An unfinished import is offered from Manage; it never takes over the entry screen.
        setImportJob(latest && latest.status !== "applied" ? latest : null);
      }
      setPreview(null); setDraftDirty(false); setStatus("idle");
    } catch (error) { setStatus("error"); setMessage(error instanceof Error ? error.message : "Could not load AI Persona."); }
  }, [token, user.id, adopt]);

  useEffect(() => {
    // The loader synchronizes this screen with the authenticated API resource.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  useEffect(() => {
    if (!persona || !importJob || !["queued", "processing"].includes(importJob.status)) return;
    const timer = window.setInterval(() => {
      void getPersonaImport(persona.id, importJob.id, token).then(setImportJob).catch(fail);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [persona, importJob, token, fail]);

  /** Fill the four-section form from a completed import. Nothing is saved until Apply & Save Draft. */
  function openPreview(job: PersonaImportJob) {
    if (!savedDraft) return;
    // Compare against what is actually stored: an account email offered as a
    // suggestion is not a saved value and must not block the document's email.
    const built = buildPreview(job, savedDraft, normalizeProfile(savedDraft.profile_config));
    if (!built.profile.identity.public_email && user.email) built.profile.identity = { ...built.profile.identity, public_email: user.email };
    setDraft(built.draft); setProfile(built.profile); setPreview(built.preview); setDraftDirty(true); setStatus("idle");
    setMessage(savedDraft.revision !== job.base_revision ? "Your saved draft changed after this upload, so applying it will be refused. Upload the document again to start from your latest draft." : "");
    manualOrigin.current = "processing"; setSection("identity"); setScreen("manual");
  }

  // When processing finishes while the owner watches, the filled form opens on its own.
  const openPreviewRef = useRef(openPreview);
  useEffect(() => { openPreviewRef.current = openPreview; });
  useEffect(() => {
    if (screen !== "processing" || !importJob) return;
    if (importJob.status === "queued" || importJob.status === "processing") { watchedImport.current = true; return; }
    if (importJob.status === "completed" && watchedImport.current) { watchedImport.current = false; openPreviewRef.current(importJob); }
  }, [screen, importJob]);

  // Review & Publish always shows the server's verdict on the saved draft.
  const refreshReadiness = useCallback(async () => {
    if (!persona) return null;
    setReadinessLoading(true);
    try {
      const result = (await validateDraft(persona.id, token)) ?? null;
      setValidation(result);
      return result;
    } catch (error) {
      fail(error);
      return null;
    } finally {
      setReadinessLoading(false);
    }
  }, [persona, token, fail]);
  useEffect(() => {
    if (screen !== "manual" || section !== "review" || preview) return;
    // Validation reflects the saved draft whenever the owner opens the final step.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshReadiness();
  }, [screen, section, preview, refreshReadiness]);

  async function createWorkingPersona() {
    setStatus("saving");
    const created = await createPersona(user.full_name, token); setPersona(created); adopt(created.draft); setDraftDirty(false); setStatus("saved");
    return created;
  }

  async function beginManual() { try { if (!persona) await createWorkingPersona(); openManual("setup"); setMessage(persona ? "" : "Your working Persona is ready."); } catch (error) { fail(error); } }
  async function beginUpload(file: File) { try { const current = persona ?? await createWorkingPersona(); const job = await uploadPersonaImport(current.id, file, token); watchedImport.current = job.status !== "completed"; setImportJob(job); setScreen("processing"); setMessage(""); } catch (error) { fail(error); } }

  function requestLeave(action: () => void) { if (dirty) setPendingLeave(() => action); else action(); }
  function discardAndLeave() { const action = pendingLeave; setPendingLeave(null); setDraft(savedDraft); setProfile(savedProfile); setPreview(null); setDraftDirty(false); setWorkingExamples(savedExamples); action?.(); }
  function openManual(origin: "setup" | "manage") { manualOrigin.current = origin; setSection("identity"); setScreen("manual"); }

  const handleRevision = useCallback((revision: number) => {
    setDraft((current) => current && current.revision !== revision ? { ...current, revision } : current);
    setSavedDraft((current) => current && current.revision !== revision ? { ...current, revision } : current);
  }, []);

  /** Editing a form field records it as the owner's own value and settles any conflict. */
  function ownerValue(current: ProfileConfig, key: string): ProfileConfig {
    if (!(key in FORM_FIELDS) || current.confirmations[key] === "not_applicable") return current;
    return { ...current, confirmations: { ...current.confirmations, [key]: "owner_confirmed" } };
  }
  function settle(key: string) { setPreview((current) => current && key in current.conflicts ? { ...current, conflicts: Object.fromEntries(Object.entries(current.conflicts).filter(([name]) => name !== key)) } : current); }
  function touched() { setDraftDirty(true); setStatus("idle"); }
  function setCore<K extends keyof PersonaDraft>(key: K, value: PersonaDraft[K]) { setDraft((current) => current ? { ...current, [key]: value } : current); setProfile((current) => ownerValue(current, key)); settle(key); touched(); }
  function setProfileField(group: "identity" | "background" | "communication", key: string, value: string | string[]) { setProfile((current) => ownerValue({ ...current, [group]: { ...current[group], [key]: value } }, key)); settle(key); touched(); }
  function setNotApplicable(field: string, reason: string | null) {
    setProfile((current) => {
      const confirmations = { ...current.confirmations };
      const reasons = { ...current.na_reasons };
      if (reason === null) { if (confirmations[field] === "not_applicable") confirmations[field] = "missing"; delete reasons[field]; }
      else { confirmations[field] = "not_applicable"; reasons[field] = reason; }
      return { ...current, confirmations, na_reasons: reasons };
    });
    touched();
  }
  function fieldState(key: FieldKey): FieldState {
    return {
      status: draft ? fieldStatus(key, draft, profile, preview) : "missing",
      required: isRequired(key),
      conflict: preview?.conflicts[key],
      onResolve: (useImported) => {
        const conflict = preview?.conflicts[key];
        if (useImported && conflict && draft) { const [nextDraft, nextProfile] = writeField(key, conflict.imported, draft, profile); setDraft(nextDraft); setProfile(ownerValue(nextProfile, key)); }
        else setProfile((current) => ownerValue(current, key));
        settle(key); touched();
      },
    };
  }

  async function save(next?: SectionKey) {
    if (!persona || !draft || preview) return;
    if (section === "examples") { await saveExamples(next); return; }
    setStatus("saving"); setMessage("");
    try {
      // The full profile is sent, so stored values the form does not show are preserved.
      // Saving is the owner's confirmation of the filled-in fields; the server approves their examples too.
      const saved = await saveDraft(persona.id, {
        revision: draft.revision, full_name: draft.full_name, professional_title: draft.professional_title,
        biography: draft.biography, specialties: draft.specialties, service_areas: draft.service_areas,
        preferred_language: draft.preferred_language, profile_config: confirmFilled(draft, profile),
      }, token);
      adopt(saved); setDraftDirty(false); setStatus("saved"); setMessage("Saved safely."); if (next) setSection(next);
      await refreshReadiness();
    } catch (error) { fail(error); }
  }

  /** Save the whole How I Communicate step at once; every saved example is the owner's approved wording. */
  async function saveExamples(next?: SectionKey) {
    if (!persona || !draft) return;
    if (!exampleDirty) { if (next) setSection(next); return; }
    setStatus("saving"); setMessage("");
    try {
      const result = await saveExampleList(persona.id, draft.revision, examplesToSave(workingExamples), token);
      setWorkingExamples(result.items); setSavedExamples(result.items);
      if (result.revision) handleRevision(result.revision);
      setStatus("saved"); setMessage("Saved safely."); if (next) setSection(next);
      await refreshReadiness();
    } catch (error) { fail(error); }
  }

  /** Save every pending Review change, even when fields and examples changed together. */
  async function saveAll() {
    if (!persona || !draft || preview) return;
    setStatus("saving"); setMessage("");
    let currentDraft = draft;
    try {
      if (draftDirty) {
        currentDraft = await saveDraft(persona.id, {
          revision: currentDraft.revision, full_name: currentDraft.full_name, professional_title: currentDraft.professional_title,
          biography: currentDraft.biography, specialties: currentDraft.specialties, service_areas: currentDraft.service_areas,
          preferred_language: currentDraft.preferred_language, profile_config: confirmFilled(currentDraft, profile),
        }, token);
        adopt(currentDraft); setDraftDirty(false);
      }
      if (exampleDirty) {
        const result = await saveExampleList(persona.id, currentDraft.revision, examplesToSave(workingExamples), token);
        setWorkingExamples(result.items); setSavedExamples(result.items);
        if (result.revision) {
          currentDraft = { ...currentDraft, revision: result.revision };
          handleRevision(result.revision);
        }
      }
      const verdict = await validateDraft(persona.id, token);
      setValidation(verdict); setStatus("saved"); setMessage("Saved safely. Publication readiness is up to date.");
    } catch (error) { fail(error); }
  }

  /** Save exactly what the owner reviewed in the form, values and examples together. */
  async function applyImport() {
    if (!persona || !draft || !preview || !savedDraft) return;
    setStatus("saving"); setMessage("");
    try {
      const saved = await applyPersonaImport(persona.id, preview.job.id, savedDraft.revision, token, reviewedImport(draft, confirmFilled(draft, profile), preview.examples));
      const examples = (await listAllExamples(persona.id, token))?.items ?? [];
      const verdict = await validateDraft(persona.id, token);
      adopt(saved); setPreview(null); setImportJob(null); setDraftDirty(false); setWorkingExamples(examples); setSavedExamples(examples); setStatus("saved");
      setValidation(verdict);
      manualOrigin.current = "setup"; setSection("review");
      setMessage("Your Persona is saved as a working draft. Publish it when you are ready, or keep editing. Nothing was submitted or published.");
    } catch (error) {
      setStatus("error");
      setMessage(`${error instanceof Error ? error.message : "The import could not be applied."} Your edits are still on this screen.`);
    }
  }

  /** The owner publishes the saved draft they reviewed; it becomes their active Persona. */
  async function publish() {
    if (!persona || !draft) return;
    setStatus("saving"); setMessage("");
    try {
      const verdict = await validateDraft(persona.id, token);
      setValidation(verdict);
      if (!verdict.valid || verdict.revision !== draft.revision) {
        setStatus(verdict.revision !== draft.revision ? "stale" : "idle");
        setMessage(verdict.revision !== draft.revision ? "The saved draft changed. Reload it before publishing." : "Complete the items shown below before publishing.");
        return;
      }
      const version = await publishOwnPersona(persona.id, draft.revision, token);
      const reusedActiveVersion = persona.active_version_id === version.id;
      await load();
      setScreen("manage"); setStatus("saved");
      setMessage(reusedActiveVersion ? "This Persona version was already published and remains active." : "Your AI Persona has been published successfully.");
    } catch (error) { fail(error); }
  }

  const sectionIndex = sections.findIndex(([key]) => key === section);
  function backFromManual() {
    if (sectionIndex === 0) requestLeave(() => { setPreview(null); setScreen(manualOrigin.current); });
    else if (exampleDirty) requestLeave(() => setSection(sections[sectionIndex - 1][0]));
    else setSection(sections[sectionIndex - 1][0]);
  }
  function nextFromManual() {
    const next = sections[Math.min(sectionIndex + 1, sections.length - 1)][0];
    if (preview) setSection(next);
    else void save(next);
  }
  const leaveDialog = pendingLeave ? <UnsavedChangesDialog onStay={() => setPendingLeave(null)} onDiscard={discardAndLeave} /> : null;
  if (status === "loading") return <div className="persona-state" role="status">Loading your Personal AI workspace…</div>;
  // Personal Personas are self-managed; Manage holds the owner's own history and versions.
  const canManage = Boolean(persona);
  if (screen === "setup") return <><SetupChoices onUpload={() => setScreen("upload")} onManual={beginManual} onExit={exit} onManage={canManage ? () => setScreen("manage") : undefined} message={status === "error" ? message : ""} />{leaveDialog}</>;
  if (screen === "upload") return <><UploadScreen token={token} onUpload={beginUpload} onBack={() => setScreen("setup")} onCancel={exit} onError={fail} message={status === "error" ? message : ""} />{leaveDialog}</>;
  if (screen === "manage") return <><PersonaManage persona={persona} importJob={importJob} message={message} onBack={() => setScreen("setup")} onCancel={exit} onEdit={() => openManual("manage")} onResume={() => { watchedImport.current = false; setScreen("processing"); }}>{persona ? <PersonaHistory persona={persona} token={token} versions={versions} onChanged={load} onError={fail} /> : null}</PersonaManage>{leaveDialog}</>;
  if (!persona || !draft) return <div className="persona-state" role="alert">The Persona workspace could not be prepared.</div>;
  if (screen === "processing" && importJob && importJob.status !== "applied") return <><Processing job={importJob} onBack={() => setScreen("setup")} onUploadAgain={() => setScreen("upload")} onRetry={async () => { try { watchedImport.current = true; setImportJob(await retryPersonaImport(persona.id, importJob.id, token)); } catch (error) { fail(error); } }} onReview={() => openPreview(importJob)} />{leaveDialog}</>;

  const readiness: Readiness | null = preview
    ? localReadiness(draft, profile, preview.examples)
    : validation ? { sections: Object.entries(validation.sections).map(([key, value]) => ({ key, ...value })), otherErrors: validation.errors.filter((error) => !error.includes(".")).map(readable), valid: validation.valid } : null;
  const stepComplete = (key: SectionKey) => Boolean(validationSection[key] && readiness?.sections.find((item) => item.key === validationSection[key])?.complete);

  return <div className="persona-page">
    <div className="persona-context"><button onClick={backFromManual}>← Back</button><span>My AI Persona / {sections[sectionIndex][1]}</span><button onClick={() => requestLeave(() => { setPreview(null); setScreen("setup"); })}>Cancel</button></div>
    <header className="persona-header"><div><span>PERSONAL AI</span><h1>{draft.full_name || persona.display_name}</h1><p>{preview ? `Auto-filled from ${preview.job.file_name} · not saved yet` : `Working draft r${draft.revision} · ${draft.review_status} ${persona.active_version_number ? `· Active v${persona.active_version_number}` : "· No active version"}`}</p></div><div className={`persona-save persona-save-${preview ? "idle" : status}`}>{preview ? "Not saved" : status === "saving" ? "Saving…" : status === "saved" ? "Saved" : status === "stale" ? "Reload required" : "Draft"}</div></header>
    <div className="persona-layout">
      <nav className="persona-steps" aria-label="Persona configuration sections">{sections.map(([key, label], index) => <button key={key} className={section === key ? "active" : ""} onClick={() => exampleDirty ? requestLeave(() => setSection(key)) : setSection(key)}><i>{stepComplete(key) ? "✓" : index + 1}</i><span>{label}</span></button>)}</nav>
      <main className="persona-card">
        {preview ? <div className="persona-autofill-note" role="status">Filled in from <b>{preview.job.file_name}</b>. Edit any field if you want to, or accept it as it is with <b>Apply &amp; Save Draft</b>. Nothing is saved until you choose it, and nothing is published until you choose Publish.</div> : null}
        {message ? <div className={`persona-notice ${status}`} role={status === "error" || status === "stale" ? "alert" : "status"}>{message}{status === "stale" && !preview ? <button onClick={load}>Reload draft</button> : null}</div> : null}
        {section === "identity" ? <Identity draft={draft} profile={profile} user={user} setCore={setCore} setField={setProfileField} setNotApplicable={setNotApplicable} state={fieldState} /> : null}
        {section === "background" ? <Background draft={draft} profile={profile} setCore={setCore} setField={setProfileField} state={fieldState} /> : null}
        {section === "examples" ? <><Conversations items={preview ? preview.examples : workingExamples} defaultLanguage={draft.preferred_language} onChange={(items) => { if (preview) { setPreview((current) => current ? { ...current, examples: items } : current); touched(); } else { setWorkingExamples(items); setStatus("idle"); } }} /><CommunicationNotes draft={draft} profile={profile} /></> : null}
        {section === "review" ? <Review preview={preview} readiness={readiness} readinessLoading={readinessLoading} readinessCurrent={Boolean(preview) || Boolean(validation && validation.revision === draft.revision)} dirty={dirty} saving={status === "saving"} onEdit={(key) => setSection(key)} onSave={saveAll} onApply={applyImport} onRefresh={refreshReadiness} onPublish={publish} /> : null}
        <div className="persona-actions persona-manual-actions"><button className="button secondary" onClick={backFromManual}>Back</button><button className="persona-cancel" onClick={() => requestLeave(() => { setPreview(null); setScreen("setup"); })}>Cancel</button><span/>{!preview && section !== "review" ? <button className="button secondary" onClick={() => save()}>Save draft</button> : null}{preview && section !== "review" ? <button className="button secondary" disabled={status === "saving"} onClick={() => void applyImport()}>{status === "saving" ? "Saving…" : "Apply & Save Draft"}</button> : null}{section !== "review" ? <button className="button" onClick={nextFromManual}>{preview ? "Next" : "Save & next"}</button> : null}</div>
      </main>
    </div>
    {leaveDialog}
  </div>;
}

type FieldSetter = (group: "identity" | "background" | "communication", key: string, value: string | string[]) => void;
type CoreSetter = <K extends keyof PersonaDraft>(key: K, value: PersonaDraft[K]) => void;

const LANGUAGES: [string, string][] = [
  ["en", "English"], ["ko", "Korean"], ["es", "Spanish"], ["zh", "Chinese"], ["vi", "Vietnamese"], ["tl", "Tagalog"], ["hi", "Hindi"],
  ["ar", "Arabic"], ["ru", "Russian"], ["pl", "Polish"], ["fr", "French"], ["ja", "Japanese"], ["pt", "Portuguese"], ["ur", "Urdu"],
];

function SetupChoices({ onUpload, onManual, onExit, onManage, message }: { onUpload: () => void; onManual: () => Promise<void>; onExit: () => void; onManage?: () => void; message: string }) {
  return <section className="persona-setup"><div className="persona-context"><button onClick={onExit}>← Back</button><span>My AI Persona / Choose setup method</span><button onClick={onExit}>Cancel</button></div><header><span>PERSONAL AI SETUP</span><h1>How would you like to begin?</h1><p>Choose the fastest path for you. Every auto-filled value remains a draft until you review and submit it.</p></header>{message ? <p className="persona-notice error" role="alert">{message}</p> : null}<div className="persona-setup-grid">
    <article><i>01</i><h2>Upload &amp; Auto-Fill</h2><p>Follow a guided template and interview flow, then upload the completed PDF, DOCX, TXT, or Markdown document.</p><button className="button" onClick={onUpload}>Start Upload &amp; Auto-Fill</button></article>
    <article><i>02</i><h2>Set Up Manually</h2><p>Continue through guided sections and save as you go.</p><button className="button secondary" onClick={() => void onManual()}>Continue manually</button></article>
  </div>{onManage ? <div className="persona-manage-link"><button onClick={onManage}>Manage my Persona and versions</button></div> : null}</section>;
}

function PersonaManage({ persona, importJob, message, onBack, onCancel, onEdit, onResume, children }: { persona: PersonaDetail | null; importJob: PersonaImportJob | null; message: string; onBack: () => void; onCancel: () => void; onEdit: () => void; onResume: () => void; children?: ReactNode }) {
  return <section className="persona-setup persona-manage"><div className="persona-context"><button onClick={onBack}>← Back</button><span>My AI Persona / Manage</span><button onClick={onCancel}>Cancel</button></div>
    <header><span>PERSONAL AI MANAGEMENT</span><h1>Your Profile</h1><p>{persona?.active_version_number ? `Active Version ${persona.active_version_number}` : "No active version yet"}</p></header>
    {message ? <p className="persona-notice" role="status">{message}</p> : null}
    {persona ? <div className="persona-landing-actions"><button className="button" onClick={onEdit}>Continue editing</button>{importJob ? <button className="button secondary" onClick={onResume}>Resume auto-fill · {importJob.status}</button> : null}</div> : null}
    {children}
  </section>;
}

function UploadScreen({ token, onUpload, onBack, onCancel, onError, message }: { token: string; onUpload: (file: File) => Promise<void>; onBack: () => void; onCancel: () => void; onError: (error: unknown) => void; message: string }) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  async function upload(file?: File) { if (!file) return; setBusy(true); try { await onUpload(file); } catch (error) { onError(error); } finally { setBusy(false); } }
  async function copyPrompt() {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(AUTHENTIC_PERSONALITY_PROMPT);
      else {
        const area = document.createElement("textarea"); area.value = AUTHENTIC_PERSONALITY_PROMPT; area.style.position = "fixed"; area.style.opacity = "0"; document.body.appendChild(area); area.select();
        const copiedWithFallback = document.execCommand("copy"); area.remove(); if (!copiedWithFallback) throw new Error("Clipboard access is unavailable.");
      }
      setCopied(true);
    } catch (error) { onError(error instanceof Error ? error : new Error("Could not copy the interview prompt.")); }
  }
  return <section className="persona-setup persona-upload"><div className="persona-context"><button onClick={onBack}>← Back</button><span>My AI Persona / Upload &amp; Auto-Fill</span><button onClick={onCancel}>Cancel</button></div><header><span>PERSONAL AI AUTO-FILL</span><h1>Create an authentic Persona document</h1></header>{message ? <p className="persona-notice error" role="alert">{message}</p> : null}<ol className="persona-upload-guide">
    <li><i>1</i><div><h2>Download the ListingIQ Persona Template</h2><p>An editable Word document. Keep the headings and labels, and complete every applicable section.</p><button className="button secondary" onClick={() => void downloadPersonaTemplate(token).catch(onError)}>Download Persona Template</button></div></li>
    <li><i>2</i><div><h2>Copy the authentic-personality prompt</h2><p>In your own ChatGPT or Claude, attach the template, paste the complete prompt and add your notes or messages. It returns the completed template, which opens here as an editable form.</p><button className="button secondary" onClick={() => void copyPrompt()}>{copied ? "Prompt copied" : "Copy Prompt"}</button><span className="persona-copy-status" aria-live="polite">{copied ? "The complete authentic-personality prompt is on your clipboard." : ""}</span><details><summary>Preview the complete prompt</summary><pre>{AUTHENTIC_PERSONALITY_PROMPT}</pre></details></div></li>
    <li><i>3</i><div><h2>Return and upload the completed document</h2><p>DOCX (recommended), PDF, TXT, or Markdown · Maximum 5 MB. Remove private client identifiers before uploading.</p><label className="button"><input aria-label="Completed Persona document" type="file" hidden accept=".docx,.pdf,.txt,.md" disabled={busy} onChange={(event) => void upload(event.target.files?.[0])}/>{busy ? "Uploading…" : "Choose completed document"}</label></div></li>
    <li><i>4</i><div><h2>Process, then Apply &amp; Review</h2><p>ListingIQ reads the completed template directly on its own servers — no AI is used — and runs document validation, personality extraction, conversation analysis, completeness checks, and draft preparation. You explicitly apply the result and review it before submission.</p></div></li>
  </ol></section>;
}

export function UnsavedChangesDialog({ onStay, onDiscard }: { onStay: () => void; onDiscard: () => void }) {
  return <div className="persona-dialog-backdrop"><section className="persona-dialog" role="dialog" aria-modal="true" aria-labelledby="persona-unsaved-title"><h2 id="persona-unsaved-title">Leave without saving?</h2><p>Your saved Persona data will remain intact. Only the unsaved edits on this screen will be discarded.</p><div><button className="button secondary" onClick={onStay}>Stay and continue editing</button><button className="button" onClick={onDiscard}>Discard unsaved edits and leave</button></div></section></div>;
}

const importStages: [string, string][] = [
  ["document_validation", "Document validation"], ["personality_extraction", "Personality extraction"],
  ["conversation_analysis", "Conversation analysis"], ["completeness_assessment", "Completeness & quality"],
  ["draft_preparation", "Persona draft preparation"],
];

function Processing({ job, onBack, onRetry, onReview, onUploadAgain }: { job: PersonaImportJob; onBack: () => void; onRetry: () => Promise<void>; onReview: () => void; onUploadAgain: () => void }) {
  const proposal = (job.proposal ?? {}) as { examples?: unknown[] };
  return <section className="persona-processing"><div className="persona-context"><button onClick={onBack}>← Back to My AI Persona</button><span>My AI Persona / Auto-fill processing</span><span>Processing continues in the background</span></div><header><span>PERSONAL AI · AUTO-FILL</span><h1>{job.status === "completed" ? "Your Persona form is filled in" : job.status === "failed" ? "Processing needs attention" : "Building your Persona draft"}</h1><p>{job.file_name} · Attempt {job.attempt_count || 1}. Progress is saved, so you can leave and return safely.</p><p className="persona-mode-note">Read locally from the ListingIQ template — no AI is used.</p></header><ol>{importStages.map(([key, label], index) => { const stage = job.stages[key] ?? { status: "pending", message: null }; return <li key={key} className={`persona-stage ${stage.status}`}><i>{stage.status === "completed" ? "✓" : stage.status === "failed" ? "!" : index + 1}</i><div><strong>{label}</strong><span>{stage.status}{stage.message ? ` · ${stage.message}` : ""}</span></div>{stage.status === "running" ? <b className="persona-spinner" aria-label="Running"/> : null}</li>; })}</ol>
    {job.status === "failed" ? <div className="persona-processing-action"><p role="alert">{job.error_message}</p>{job.retryable ? <button className="button" onClick={() => void onRetry()}>Retry safely</button> : <div><p>{job.error_code === "template_not_recognized" ? "Use the ListingIQ Persona template headings and labels, then upload the document again." : "Choose a readable, relevant Persona document and upload it again."}</p><button className="button secondary" onClick={onUploadAgain}>Upload another document</button></div>}</div> : null}
    {job.status === "completed" ? <div className="persona-processing-action"><p>Your document has been read{proposal.examples?.length ? `, including ${proposal.examples.length} conversation examples` : ""}. Review and edit your Persona; nothing is saved until you apply it.</p><button className="button" onClick={onReview}>Review &amp; Edit Persona</button></div> : null}
  </section>;
}

const STATUS_LABELS: Record<FieldStatus, string> = {
  extracted: "Extracted", missing: "Missing", needs_confirmation: "Needs confirmation", owner_confirmed: "Owner confirmed", not_applicable: "Not applicable",
};
const show = (value: unknown) => Array.isArray(value) ? value.join(", ") : String(value ?? "");

/** Only genuinely missing required information is marked, beside the input itself. */
function FieldMeta({ state }: { state?: FieldState }) {
  if (!state || state.status !== "missing" || !state.required) return null;
  return <span className="persona-field-meta"><span className="persona-field-status missing required">{STATUS_LABELS.missing} · required</span></span>;
}
function ConflictNote({ label, state }: { label: string; state?: FieldState }) {
  if (!state?.conflict) return null;
  return <div className="persona-conflict" role="group" aria-label={`${label}: saved value differs from the document`}><p>Your saved value is kept: “{show(state.conflict.saved)}”. The document says: “{show(state.conflict.imported)}”.</p><div><button type="button" onClick={() => state.onResolve(true)}>Use document value</button><button type="button" onClick={() => state.onResolve(false)}>Keep saved value</button></div></div>;
}

/** A labelled input with its help icon and status. Every visible manual input uses this. */
function Text({ label, help, value, onChange, area = false, placeholder = "", readOnly = false, wide = false, state }: { label: string; help: Help; value: string; onChange?: (value: string) => void; area?: boolean; placeholder?: string; readOnly?: boolean; wide?: boolean; state?: FieldState }) {
  const id = useId();
  const missing = state?.status === "missing" && state.required;
  return <div className={`persona-field${area || wide ? " wide" : ""}${missing ? " is-missing" : ""}`}><div className="persona-field-label"><label htmlFor={id}>{label}</label><HelpTip label={label} help={help}/><FieldMeta state={state}/></div>{area ? <AutoTextarea id={id} value={value} onChange={(event) => onChange?.(event.target.value)} placeholder={placeholder} readOnly={readOnly} aria-invalid={missing || undefined} /> : <input id={id} value={value} onChange={(event) => onChange?.(event.target.value)} placeholder={placeholder} readOnly={readOnly} aria-invalid={missing || undefined} />}<ConflictNote label={label} state={state}/></div>;
}
/** A comma-separated list that keeps exactly what the owner types while they type it. */
function ListText({ value, onChange, ...props }: { label: string; help: Help; value: string[]; onChange: (value: string[]) => void; state?: FieldState; wide?: boolean }) {
  const [raw, setRaw] = useState(value.join(", "));
  const [shown, setShown] = useState(value);
  if (JSON.stringify(shown) !== JSON.stringify(value)) { setShown(value); setRaw(value.join(", ")); }
  return <Text {...props} value={raw} onChange={(text) => { const parsed = splitList(text); setRaw(text); setShown(parsed); onChange(parsed); }} />;
}
function Choice({ label, help, value, options, onChange, state }: { label: string; help: Help; value: string; options: [string, string][]; onChange: (value: string) => void; state?: FieldState }) {
  const id = useId();
  const all = options.some(([key]) => key === value) || !value ? options : [...options, [value, value] as [string, string]];
  return <div className="persona-field"><div className="persona-field-label"><label htmlFor={id}>{label}</label><HelpTip label={label} help={help}/><FieldMeta state={state}/></div><select id={id} value={value} onChange={(event) => onChange(event.target.value)}>{all.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select><ConflictNote label={label} state={state}/></div>;
}

function NotApplicable({ field, label, profile, onChange }: { field: string; label: string; profile: ProfileConfig; onChange: (field: string, reason: string | null) => void }) {
  const checked = profile.confirmations[field] === "not_applicable";
  const id = useId();
  return <div className="persona-na"><div className="persona-field-label"><label className="persona-na-check" htmlFor={id}><input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(field, event.target.checked ? profile.na_reasons[field] ?? "" : null)}/>{label} does not apply</label><HelpTip label={`${label} does not apply`} help={NA_HELP}/></div>{checked ? <Text label={`Why ${label.toLowerCase()} does not apply`} help={NA_HELP} value={profile.na_reasons[field] ?? ""} onChange={(reason) => onChange(field, reason)} wide/> : null}</div>;
}

function Identity({ draft, profile, user, setCore, setField, setNotApplicable, state }: { draft: PersonaDraft; profile: ProfileConfig; user: AuthUser; setCore: CoreSetter; setField: FieldSetter; setNotApplicable: (field: string, reason: string | null) => void; state: (key: FieldKey) => FieldState }) {
  const identity = profile.identity;
  return <><Heading eyebrow="01 · IDENTITY" title="Who I am" copy="Your account details are filled in where ListingIQ already knows them."/><div className="persona-form-grid">
    <Text label="Full name" help={HELP.full_name} value={draft.full_name} onChange={(value) => setCore("full_name", value)} state={state("full_name")}/>
    <Text label="Professional title" help={HELP.professional_title} value={draft.professional_title ?? ""} onChange={(value) => setCore("professional_title", value)} state={state("professional_title")}/>
    <Text label="Current brokerage" help={HELP.brokerage} value={user.brokerage_name || draft.brokerage_name} readOnly/>
    <div><Text label="License / registration number" help={HELP.license_number} value={identity.license_number} onChange={(value) => setField("identity", "license_number", value)} state={state("license_number")}/><NotApplicable field="license_number" label="License number" profile={profile} onChange={setNotApplicable}/></div>
    <div><Text label="Licensing jurisdiction" help={HELP.licensing_jurisdiction} value={identity.licensing_jurisdiction} onChange={(value) => setField("identity", "licensing_jurisdiction", value)} state={state("licensing_jurisdiction")}/><NotApplicable field="licensing_jurisdiction" label="Jurisdiction" profile={profile} onChange={setNotApplicable}/></div>
    <div><Text label="License status" help={HELP.licensing_status} value={identity.licensing_status} onChange={(value) => setField("identity", "licensing_status", value)} placeholder="Active, inactive, pending…" state={state("licensing_status")}/><NotApplicable field="licensing_status" label="License status" profile={profile} onChange={setNotApplicable}/></div>
    <Text label="Public professional email" help={HELP.public_email} value={identity.public_email} onChange={(value) => setField("identity", "public_email", value)} state={state("public_email")}/>
    <Text label="Public professional phone" help={HELP.public_phone} value={identity.public_phone} onChange={(value) => setField("identity", "public_phone", value)} state={state("public_phone")}/>
    <Text label="Professional introduction" help={HELP.professional_intro} value={identity.professional_intro} onChange={(value) => setField("identity", "professional_intro", value)} area state={state("professional_intro")}/>
  </div></>;
}

function Background({ draft, profile, setCore, setField, state }: { draft: PersonaDraft; profile: ProfileConfig; setCore: CoreSetter; setField: FieldSetter; state: (key: FieldKey) => FieldState }) {
  return <><Heading eyebrow="02 · BACKGROUND" title="My background" copy="A few facts about your work. Your communication style comes from your real examples in the next step."/><div className="persona-form-grid">
    <Text label="Professional biography" help={HELP.biography} value={draft.biography} onChange={(value) => setCore("biography", value)} area state={state("biography")}/>
    <ListText label="Specialties (comma separated)" help={HELP.specialties} value={draft.specialties} onChange={(value) => setCore("specialties", value)} state={state("specialties")}/>
    <ListText label="Service areas (comma separated)" help={HELP.service_areas} value={draft.service_areas} onChange={(value) => setCore("service_areas", value)} state={state("service_areas")}/>
    <ListText label="Expertise and differentiators (comma separated)" help={HELP.differentiators} value={asList(profile.background.differentiators)} onChange={(value) => setField("background", "differentiators", value)} state={state("differentiators")} wide/>
    <ListText label="Target audiences (comma separated)" help={HELP.target_audiences} value={asList(profile.background.target_audiences)} onChange={(value) => setField("background", "target_audiences", value)} state={state("target_audiences")} wide/>
    <Choice label="Primary language" help={HELP.preferred_language} value={draft.preferred_language} options={LANGUAGES} onChange={(value) => setCore("preferred_language", value)} state={state("preferred_language")}/>
    <ListText label="Other languages (comma separated)" help={HELP.other_languages} value={asList(profile.communication.other_languages)} onChange={(value) => setField("communication", "other_languages", value)} state={state("other_languages")}/>
  </div></>;
}

/** Imported communication details that have no form field; kept with the Persona and shown read-only. */
function CommunicationNotes({ draft, profile }: { draft: PersonaDraft; profile: ProfileConfig }) {
  const c = profile.communication;
  const style = draft.style_config;
  const text = (value: unknown) => typeof value === "string" ? value : asList(value).join(" · ");
  const rows: [string, string][] = ([
    ["How I come across", style.personality === "professional and approachable" ? "" : style.personality],
    ["Phrases I use", text(c.signature_phrases)],
    ["Phrases I avoid", [...style.avoided_phrases, ...asList(c.unnatural_expressions)].join(" · ")],
    ["Language switching", text(c.language_switching)],
    ["When I am unsure", text(c.uncertainty_behavior)],
    ["Negative replies", text(c.negative_reply_behavior)],
    ["Next steps", text(c.next_step_behavior)],
    ...playbookLabels.map(([key, label]) => [label, profile.playbook[key] ?? ""] as [string, string]),
  ] as [string, string][]).filter(([, value]) => value.trim());
  if (!rows.length) return null;
  return <details className="persona-review-block persona-additional"><summary>Other communication notes from your document ({rows.length})</summary><dl className="persona-notes">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></details>;
}

const languageName = (code: string) => LANGUAGES.find(([key]) => key === code)?.[1] ?? code;

/**
 * The How I Communicate step: each question once, with its answers directly
 * underneath. Import details (provenance, review flags) stay on the example
 * and are summarised on Review & Submit instead of crowding the editor.
 */
function Conversations({ items, defaultLanguage, onChange }: { items: FormExample[]; defaultLanguage: string; onChange: (items: FormExample[]) => void }) {
  const STANDARD_QUESTIONS = SCENARIOS.filter((item) => item.scenario);
  const standard = new Set(STANDARD_QUESTIONS.map((item) => item.scenario));
  const newExample = (scenario: string, change: Partial<FormExample> = {}): FormExample => ({
    id: Math.min(0, ...items.map((item) => item.id)) - 1, position: items.length, scenario, channel: "sms", language: defaultLanguage || "en",
    customer_message: "", preferred_response: "", context: "", style_explanation: "", provenance: "user_written", owner_approved: false, review_flags: [], ...change,
  });
  const update = (id: number, change: Partial<FormExample>) => onChange(items.map((item) => item.id === id ? { ...item, ...change } : item));
  const remove = (id: number) => onChange(items.filter((item) => item.id !== id));
  const custom = items.filter((item) => !standard.has(item.scenario));
  return <><Heading eyebrow="03 · HOW I COMMUNICATE" title="How I communicate" copy="Write each answer exactly as you would send it, in your own words. Leave a question blank if you would not say anything."/>
    <div className="persona-qa-list">
      {STANDARD_QUESTIONS.map((question, index) => {
        const answers = items.filter((item) => item.scenario === question.scenario);
        return <section key={question.scenario} className="persona-qa">
          <div className="persona-qa-question"><span>{String(index + 1).padStart(2, "0")}</span><h3>{question.question}</h3><HelpTip label={question.question} help={EXAMPLE_HELP.preferred_response}/></div>
          {answers.length
            ? answers.map((answer, position) => <Answer key={`${question.scenario}:${position}`} label={answers.length > 1 ? `${question.question} (answer ${position + 1})` : question.question} answer={answer} onChange={(change) => update(answer.id, change)} onRemove={answers.length > 1 ? () => remove(answer.id) : undefined}/>)
            : <Answer key={`${question.scenario}:0`} label={question.question} answer={newExample(question.scenario)} onChange={(change) => onChange([...items, newExample(question.scenario, change)])}/>}
        </section>;
      })}
      {custom.length ? <section className="persona-qa"><div className="persona-qa-question"><h3>Your own examples</h3><HelpTip label="Your own examples" help={EXAMPLE_HELP.scenario}/></div>{custom.map((answer, position) => <Answer key={answer.id} custom label={answer.scenario.trim() ? `${answer.scenario} (your example)` : `Your example ${position + 1}`} answer={answer} onChange={(change) => update(answer.id, change)} onRemove={() => remove(answer.id)}/>)}</section> : null}
    </div>
    <button type="button" className="persona-add-custom" onClick={() => onChange([...items, newExample("")])}>+ Add custom example</button></>;
}

function Answer({ label, answer, onChange, onRemove, custom = false }: { label: string; answer: FormExample; onChange: (change: Partial<FormExample>) => void; onRemove?: () => void; custom?: boolean }) {
  const replying = Boolean(answer.customer_message.trim());
  return <div className="persona-answer">
    {custom ? <Text label="Situation" help={EXAMPLE_HELP.scenario} value={answer.scenario} onChange={(scenario) => onChange({ scenario })} wide/> : null}
    <AutoTextarea aria-label={label} value={answer.preferred_response} placeholder="Your response" onChange={(event) => onChange({ preferred_response: event.target.value })}/>
    <div className="persona-answer-footer">
      <details className="persona-answer-options"><summary>{answer.channel === "email" ? "Email" : "SMS"} · {languageName(answer.language)}{replying ? " · replying to a message" : ""}</summary><div className="persona-form-grid">
        <Choice label="Channel" help={EXAMPLE_HELP.channel} value={answer.channel} options={[["sms", "SMS"], ["email", "Email"]]} onChange={(channel) => onChange({ channel: channel as "sms" | "email" })}/>
        <Choice label="Language" help={EXAMPLE_HELP.language} value={answer.language} options={LANGUAGES} onChange={(language) => onChange({ language })}/>
        <Text label="Customer's message (optional)" help={EXAMPLE_HELP.customer_message} value={answer.customer_message} onChange={(customer_message) => onChange({ customer_message })} area/>
      </div></details>
      {onRemove ? <button type="button" className="persona-answer-remove" onClick={onRemove}>Remove</button> : null}
    </div>
  </div>;
}

const REVIEW_SECTIONS: [SectionKey, string, string][] = [
  ["identity", "Who I Am", "who_i_am"], ["background", "My Background", "professional_background"], ["examples", "How I Communicate", "conversation_examples"],
];

/** A short final check: each section with Edit, then save and publish. */
function Review({ preview, readiness, readinessLoading, readinessCurrent, dirty, saving, onEdit, onSave, onApply, onRefresh, onPublish }: { preview: ImportPreview | null; readiness: Readiness | null; readinessLoading: boolean; readinessCurrent: boolean; dirty: boolean; saving: boolean; onEdit: (section: SectionKey) => void; onSave: () => Promise<void>; onApply: () => Promise<void>; onRefresh: () => Promise<PersonaValidation | null>; onPublish: () => Promise<void> }) {
  const blocked = Boolean(preview) || dirty || saving || readinessLoading || !readinessCurrent || !readiness?.valid;
  const missing = readiness?.sections.flatMap((section) => section.missing).map((item) => FIELD_LABELS[item] ?? item.replaceAll("_", " ")) ?? [];
  const reason = preview ? "Apply and save this auto-filled draft before publishing."
    : dirty ? "Save changes before publishing."
      : saving ? "Saving your draft…"
        : readinessLoading ? "Checking publication readiness…"
          : !readinessCurrent ? "Publication readiness is out of date. Check it again."
            : missing.length ? `Complete before publishing: ${missing.join(", ")}.`
              : readiness?.otherErrors.length ? readiness.otherErrors.join(" ")
                : !readiness?.valid ? "The saved draft is not ready to publish." : "Ready to publish.";
  return <><Heading eyebrow="04 · REVIEW" title="Review & Publish" copy="Review your information before publishing. You can return to any section to make changes."/>
    <ul className="persona-review-sections">{REVIEW_SECTIONS.map(([key, label, server]) => {
      const result = readiness?.sections.find((item) => item.key === server);
      const missing = (result?.missing ?? []).map((item) => FIELD_LABELS[item] ?? item.replaceAll("_", " "));
      return <li key={key} className={result?.complete ? "complete" : ""}><b aria-hidden="true">{result?.complete ? "✓" : ""}</b><div><strong>{label}</strong>{missing.length ? <span>Still needed: {missing.join(", ")}</span> : null}</div><button type="button" onClick={() => onEdit(key)} aria-label={`Edit ${label}`}>Edit</button></li>;
    })}</ul>
    {readiness?.otherErrors.length ? <p className="persona-review-note">{readiness.otherErrors.join(" ")}</p> : null}
    <p className={`persona-publish-reason${blocked ? " blocked" : " ready"}`} role="status">{reason}</p>
    <div className="persona-review-actions">
      {preview ? <button className="button secondary" disabled={saving} onClick={() => void onApply()}>{saving ? "Saving…" : "Apply & Save Draft"}</button> : dirty ? <button className="button secondary" disabled={saving} onClick={() => void onSave()}>Save Draft</button> : null}
      {!preview && !dirty && !saving && !readinessLoading && !readinessCurrent ? <button className="button secondary" onClick={() => void onRefresh()}>Check readiness</button> : null}
      <button className="button" disabled={blocked} onClick={() => void onPublish()}>Publish My Persona</button>
    </div>
  </>;
}

/** Published versions remain available on the self-managed Persona screen. */
function PersonaHistory({ persona, token, versions, onChanged, onError }: { persona: PersonaDetail; token: string; versions: PersonaVersion[]; onChanged: () => Promise<void>; onError: (error: unknown) => void }) {
  return <section className="persona-history"><h3>Published versions</h3>{versions.map((item) => <article key={item.id}><div><strong>Version {item.version_number}</strong>{persona.active_version_number === item.version_number ? <span className="persona-badge active">Active</span> : null}</div><p>Published {date(item.published_at)}</p><div className="persona-version-actions">{persona.active_version_number !== item.version_number ? <button onClick={async () => { if (!confirm(`Activate Persona version ${item.version_number}?`)) return; try { await activateVersion(persona.id, item.version_number, token); await onChanged(); } catch (error) { onError(error); } }}>Activate / restore</button> : null}</div></article>)}{!versions.length ? <p>No published versions yet.</p> : null}</section>;
}

function Heading({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) { return <header className="persona-section-heading"><span>{eyebrow}</span><h2>{title}</h2><p>{copy}</p></header>; }

const NA_HELP: Help = { what: "This item genuinely does not apply to you.", enter: "Tick it only when it does not apply, then explain why in one sentence.", why: "Submission needs either the information or an honest reason; nothing is filled in for you.", example: "Not licensed; I am the broker's assistant." };

const HELP: Record<string, Help> = {
  full_name: { what: "Your name as clients know you.", enter: "First and last name. It comes from your ListingIQ account and you can correct it here.", why: "Your AI introduces itself on your behalf with this name.", example: "Michael Kang" },
  professional_title: { what: "Your job title and business.", enter: "Title and company in one line.", why: "Clients see it when your AI explains who it represents.", example: "Founder & Team Lead, SpaceMatch Inc." },
  brokerage: { what: "The brokerage your ListingIQ account belongs to.", enter: "Nothing; it comes from your account and cannot be changed here.", why: "Your AI always represents your current brokerage." },
  license_number: { what: "Your real estate license or registration number.", enter: "The number exactly as issued, or tick 'does not apply' with a reason.", why: "It is shown as owner-provided information and is never invented or marked verified.", example: "475.123456" },
  licensing_jurisdiction: { what: "Where your license was issued.", enter: "State or province.", why: "Licensing rules differ by jurisdiction.", example: "Illinois" },
  licensing_status: { what: "Whether your license is currently active.", enter: "Active, inactive, or pending.", why: "It is shown as the status you state; ListingIQ never marks it verified.", example: "Active" },
  public_email: { what: "The email clients may use to reach you.", enter: "A professional address. Your account email is filled in; change it if clients use another.", why: "Your AI may share it when someone asks how to contact you.", example: "michael@spacematchinc.com" },
  public_phone: { what: "The phone number clients may call or text.", enter: "A professional number you are happy to share.", why: "Your AI may offer it when handing a conversation to you.", example: "(773) 344-0738" },
  professional_intro: { what: "How you describe yourself in one or two sentences.", enter: "Your own words, as you would say them to a new client. Optional.", why: "It keeps introductions sounding like you.", example: "Michael Kang with SpaceMatch Inc. in Chicago." },
  biography: { what: "A short professional background.", enter: "A few sentences about your experience and how you work. Facts only.", why: "Your AI uses it to answer 'who are you?' accurately.", example: "Twelve years helping North Side families buy and sell." },
  specialties: { what: "The kinds of property or clients you focus on.", enter: "A comma-separated list.", why: "Your AI stays within what you actually do.", example: "condos, two-flats, first-time buyers" },
  service_areas: { what: "Where you work.", enter: "Neighborhoods, cities or counties, separated by commas.", why: "Your AI will not offer help outside these areas.", example: "Lincoln Park, Lakeview, Uptown" },
  preferred_language: { what: "The language you use with most clients.", enter: "Choose one language.", why: "Your AI replies in this language unless the client writes in another you use.", example: "English" },
  other_languages: { what: "Other languages you are comfortable using with clients.", enter: "A comma-separated list, or leave empty.", why: "Your AI can switch when a client writes in one of them.", example: "Korean, Spanish" },
  differentiators: { what: "What you know well and what sets you apart.", enter: "Short phrases separated by commas.", why: "Your AI can explain why a client should work with you, using only what you state.", example: "operator's perspective, SBA and DSCR financing know-how" },
  target_audiences: { what: "The clients you mainly serve.", enter: "Groups of clients separated by commas.", why: "Your AI adapts its replies to the people you usually help.", example: "retiring business owners, first-time buyers" },
};

/** Scenario prompts that collect the owner's real wording instead of adjectives. */
const SCENARIOS: { scenario: string; question: string; customer: string }[] = [
  { scenario: "Introducing myself to a new client", question: "How do you introduce yourself to a new client?", customer: "" },
  { scenario: "Property value question", question: "How would you reply when someone asks about their property's value?", customer: "What do you think my home is worth?" },
  { scenario: "Interested but hesitant", question: "What would you say to someone who is interested but hesitant?", customer: "Maybe, but I'm not sure it's the right time." },
  { scenario: "Commission or pricing objection", question: "How would you respond to a commission or pricing objection?", customer: "Your commission is too high." },
  { scenario: "Following up after no reply", question: "How would you follow up with someone who hasn't replied?", customer: "" },
  { scenario: "Upset customer", question: "How would you respond to an upset customer?", customer: "Nobody has called me back. This is frustrating." },
  { scenario: "Suggesting an appointment", question: "How would you suggest an appointment?", customer: "Sure, I'd like to talk." },
  { scenario: "", question: "Add another real example of how you naturally communicate.", customer: "" },
];

const EXAMPLE_HELP: Record<string, Help> = {
  scenario: { what: "The situation this example is about.", enter: "A few words naming the situation.", why: "Your AI uses the closest situation when deciding how to reply.", example: "Commission or pricing objection" },
  customer_message: { what: "What the client wrote to you, if anything.", enter: "Their message with names and details removed. Leave empty when you start the conversation.", why: "It shows your AI what you were responding to.", example: "Your commission is too high." },
  preferred_response: { what: "Exactly what you would send.", enter: "Your own words, as you would really type them, including greetings and sign-off.", why: "Your AI learns your natural wording from these replies; they are stored exactly as written.", example: "Fair question. What you're paying for is the result…" },
  channel: { what: "Where this message is sent.", enter: "SMS or Email.", why: "Texts and emails are written differently.", example: "SMS" },
  language: { what: "The language of your reply.", enter: "Choose the language you wrote it in.", why: "Your AI keeps examples in each language separate.", example: "Korean" },
  provenance: { what: "Whether this is a real message or written for setup.", enter: "Authentic if you really sent it (details removed); illustrative if you wrote it to show your style.", why: "It records which examples come from real conversations.", example: "Illustrative" },
};
