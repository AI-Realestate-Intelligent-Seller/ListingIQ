import type { FormExample, ImportProposal, PersonaDraft, PersonaImportJob, ProfileConfig, ReviewedImport, SourceEvidence, SourceReview } from "./types";

/** Every field the four-section form shows, with where it lives in the draft. */
export const FORM_FIELDS = {
  full_name: "draft.full_name",
  professional_title: "draft.professional_title",
  license_number: "profile_config.identity.license_number",
  licensing_jurisdiction: "profile_config.identity.licensing_jurisdiction",
  licensing_status: "profile_config.identity.licensing_status",
  public_email: "profile_config.identity.public_email",
  public_phone: "profile_config.identity.public_phone",
  professional_intro: "profile_config.identity.professional_intro",
  biography: "draft.biography",
  specialties: "draft.specialties",
  service_areas: "draft.service_areas",
  differentiators: "profile_config.background.differentiators",
  target_audiences: "profile_config.background.target_audiences",
  preferred_language: "draft.preferred_language",
  other_languages: "profile_config.communication.other_languages",
} as const;
export type FieldKey = keyof typeof FORM_FIELDS;
export const FIELD_KEYS = Object.keys(FORM_FIELDS) as FieldKey[];

/** The same required fields the server checks before submission. */
export const REQUIRED_FIELDS: Record<string, FieldKey[]> = {
  who_i_am: ["full_name", "professional_title", "license_number", "licensing_jurisdiction", "licensing_status"],
  professional_background: ["biography", "specialties", "service_areas"],
};
const NOT_APPLICABLE_ALLOWED = new Set<FieldKey>(["professional_title", "license_number", "licensing_jurisdiction", "licensing_status"]);
export const isRequired = (key: FieldKey) => Object.values(REQUIRED_FIELDS).some((fields) => fields.includes(key));

type FieldValue = string | string[] | null | undefined;
export const isEmpty = (value: unknown) => value === null || value === undefined || (typeof value === "string" ? !value.trim() : Array.isArray(value) && !value.length);
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function readField(key: FieldKey, draft: PersonaDraft, profile: ProfileConfig): FieldValue {
  const [root, group, name] = FORM_FIELDS[key].split(".");
  if (root === "draft") return (draft as unknown as Record<string, FieldValue>)[group];
  return (profile as unknown as Record<string, Record<string, FieldValue>>)[group]?.[name];
}

export function writeField(key: FieldKey, value: FieldValue, draft: PersonaDraft, profile: ProfileConfig): [PersonaDraft, ProfileConfig] {
  const [root, group, name] = FORM_FIELDS[key].split(".");
  if (root === "draft") return [{ ...draft, [group]: value } as PersonaDraft, profile];
  const section = group as "identity" | "background" | "communication";
  return [draft, { ...profile, [section]: { ...profile[section], [name]: value } }];
}

function importedValue(key: FieldKey, proposal: ImportProposal): FieldValue {
  const [root, group, name] = FORM_FIELDS[key].split(".");
  if (root === "draft") return (proposal.draft_patch as Record<string, FieldValue>)[group];
  return ((proposal.profile_patch as Record<string, Record<string, FieldValue> | undefined>)[group] ?? {})[name];
}

export type FieldStatus = "extracted" | "missing" | "needs_confirmation" | "owner_confirmed" | "not_applicable";
export type Conflict = { saved: FieldValue; imported: FieldValue };

/** An import shown in the form before anything is saved. */
export type ImportPreview = {
  job: PersonaImportJob;
  examples: FormExample[];
  conflicts: Partial<Record<FieldKey, Conflict>>;
  evidence: SourceEvidence[];
  missingQuestions: string[];
};

function mergeReview(current: SourceReview, proposal: ImportProposal, job: PersonaImportJob): SourceReview {
  const claimKey = (item: { statement: string; field_path: string }) => `${item.statement}\u0000${item.field_path}`;
  const detailKey = (item: { section: string; detail: string }) => `${item.section}\u0000${item.detail}`;
  const claims = [...current.claims];
  const known = new Set(claims.map(claimKey));
  for (const claim of proposal.claims ?? []) {
    const item = { statement: claim.statement, source_status: claim.status, field_path: claim.field_path ?? "", source_excerpt: claim.source_excerpt ?? "", owner_status: "owner_confirmed" as const, owner_note: "" };
    if (!known.has(claimKey(item))) { claims.push(item); known.add(claimKey(item)); }
  }
  const details = [...current.unmapped_details];
  const seen = new Set(details.map(detailKey));
  for (const detail of proposal.unmapped_details ?? []) {
    const item = { ...detail, owner_status: "kept" as const };
    if (!seen.has(detailKey(item))) { details.push(item); seen.add(detailKey(item)); }
  }
  return {
    ...current, claims, unmapped_details: details,
    evidence: [...current.evidence, ...(proposal.evidence ?? [])],
    missing_questions: [...new Set([...current.missing_questions, ...(job.assessment?.missing_questions ?? [])])],
  };
}

/**
 * Fill the form from a completed import without saving anything.
 * Empty saved fields take the imported value; a saved value that differs is
 * kept and reported as a conflict for the owner to resolve.
 */
export function buildPreview(job: PersonaImportJob, draft: PersonaDraft, profile: ProfileConfig): { draft: PersonaDraft; profile: ProfileConfig; preview: ImportPreview } {
  const proposal = (job.proposal ?? {}) as ImportProposal;
  let nextDraft = { ...draft };
  let nextProfile: ProfileConfig = {
    ...profile,
    confirmations: { ...(proposal.profile_patch?.confirmations ?? {}), ...profile.confirmations },
    source_review: mergeReview(profile.source_review, proposal, job),
  };
  const conflicts: ImportPreview["conflicts"] = {};
  for (const key of FIELD_KEYS) {
    const imported = importedValue(key, proposal);
    if (isEmpty(imported)) continue;
    const saved = readField(key, draft, profile);
    const savedIsDefault = key === "preferred_language" && saved === "en";
    if (isEmpty(saved) || savedIsDefault || same(saved, imported)) [nextDraft, nextProfile] = writeField(key, imported, nextDraft, nextProfile);
    else conflicts[key] = { saved, imported };
  }
  const examples: FormExample[] = (proposal.examples ?? []).map((item, index) => ({
    ...item, id: -(index + 1), position: index, source_index: index,
    // Imported examples are never approved until the owner approves them.
    owner_approved: false,
  }));
  return {
    draft: nextDraft, profile: nextProfile,
    preview: { job, examples, conflicts, evidence: proposal.evidence ?? [], missingQuestions: job.assessment?.missing_questions ?? [] },
  };
}

export function fieldStatus(key: FieldKey, draft: PersonaDraft, profile: ProfileConfig, preview: ImportPreview | null): FieldStatus {
  if (profile.confirmations[key] === "not_applicable" && profile.na_reasons[key]?.trim()) return "not_applicable";
  if (isEmpty(readField(key, draft, profile))) return "missing";
  if (preview?.conflicts[key]) return "needs_confirmation";
  if (profile.confirmations[key] === "owner_confirmed") return "owner_confirmed";
  const evidence = [...(preview?.evidence ?? []), ...(profile.source_review.evidence ?? [])].filter((item) => item.field_path === FORM_FIELDS[key]);
  if (evidence.length) return evidence.some((item) => item.needs_owner_confirmation) ? "needs_confirmation" : "extracted";
  return "owner_confirmed";
}

export type Readiness = { sections: { key: string; complete: boolean; missing: string[] }[]; otherErrors: string[]; valid: boolean };

/** The server's submission rules, applied to the unsaved form (used before an import is applied). */
export function localReadiness(draft: PersonaDraft, profile: ProfileConfig, examples: FormExample[]): Readiness {
  const sections: Readiness["sections"] = Object.entries(REQUIRED_FIELDS).map(([section, fields]) => {
    const missing: string[] = fields.filter((key) => isEmpty(readField(key, draft, profile)) && !(NOT_APPLICABLE_ALLOWED.has(key) && profile.confirmations[key] === "not_applicable" && profile.na_reasons[key]?.trim()));
    return { key: section, complete: !missing.length, missing };
  });
  // Apply & Save Draft approves every example the owner keeps, so one answered example is enough here.
  const exampleGaps = examples.some((item) => item.preferred_response.trim()) ? [] : ["at least one conversation example"];
  sections.push({ key: "conversation_examples", complete: !exampleGaps.length, missing: exampleGaps });
  const otherErrors = [!draft.brokerage_name?.trim() ? "Brokerage is missing from your account" : "", !draft.preferred_language?.trim() ? "Primary language is required" : ""].filter(Boolean);
  return { sections, otherErrors, valid: sections.every((item) => item.complete) && !otherErrors.length };
}

/** Exactly what the owner sees in the form, for Apply & Save Draft. */
export function reviewedImport(draft: PersonaDraft, profile: ProfileConfig, examples: FormExample[]): ReviewedImport {
  const identity = Object.fromEntries(["license_number", "licensing_jurisdiction", "licensing_status", "public_email", "public_phone", "professional_intro"].map((key) => [key, String(profile.identity[key] ?? "")]));
  const list = (value: unknown) => Array.isArray(value) ? value.map(String) : [];
  return {
    draft: { full_name: draft.full_name, professional_title: draft.professional_title, biography: draft.biography, specialties: draft.specialties, service_areas: draft.service_areas, preferred_language: draft.preferred_language },
    profile: {
      identity,
      background: { target_audiences: list(profile.background.target_audiences), differentiators: list(profile.background.differentiators) },
      communication: { other_languages: list(profile.communication.other_languages) },
      confirmations: Object.fromEntries(Object.entries(profile.confirmations).filter(([, value]) => value !== "authoritatively_verified")),
      na_reasons: profile.na_reasons,
    },
    // A cleared answer means the owner removed that example.
    examples: examples.filter((item) => item.preferred_response.trim()).map((item) => ({
      ...(item.source_index !== undefined ? { source_index: item.source_index } : {}),
      scenario: item.scenario.trim() || "Custom example", channel: item.channel, language: item.language, customer_message: item.customer_message,
      preferred_response: item.preferred_response, provenance: item.provenance,
    })),
    source_review: { claims: profile.source_review.claims, unmapped_details: profile.source_review.unmapped_details },
  };
}

/** Split a comma-separated list, leaving commas inside parentheses alone. */
export function splitList(value: string): string[] {
  const items: string[] = [];
  let depth = 0;
  let current = "";
  for (const character of value) {
    if ("([{".includes(character)) depth += 1;
    if (")]}".includes(character)) depth = Math.max(0, depth - 1);
    if (character === "," && depth === 0) { items.push(current); current = ""; } else current += character;
  }
  items.push(current);
  return items.map((item) => item.trim()).filter(Boolean);
}


/** Saving the form is the owner's confirmation of every filled-in field. */
export function confirmFilled(draft: PersonaDraft, profile: ProfileConfig): ProfileConfig {
  const confirmations = { ...profile.confirmations };
  for (const key of FIELD_KEYS) {
    if (!isEmpty(readField(key, draft, profile)) && confirmations[key] !== "not_applicable") confirmations[key] = "owner_confirmed";
  }
  return { ...profile, confirmations };
}

/** The examples the owner kept, ready for the atomic save of the How I Communicate step. */
export function examplesToSave(examples: FormExample[]) {
  return examples.filter((item) => item.preferred_response.trim()).map((item) => ({
    ...(item.id > 0 ? { id: item.id } : { provenance: item.provenance }),
    scenario: item.scenario.trim() || "Custom example", channel: item.channel, language: item.language,
    customer_message: item.customer_message, preferred_response: item.preferred_response,
  }));
}
