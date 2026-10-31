export type PersonaSummary = {
  id: number; owner_user_id: number; display_name: string; status: string;
  active_version_id: number | null; active_version_number: number | null;
  membership_ended_at: string | null;
};

export type CommunicationStyle = {
  personality: string; tone: string; formality: number; warmth: number; directness: number;
  message_length: string; sentence_pattern: string; greeting_preference: string;
  vocabulary_preference: string; question_behavior: string; follow_up_behavior: string;
  objection_handling: string; sign_off_preference: string; preferred_phrases: string[];
  avoided_phrases: string[];
};

export type ProfileConfig = {
  schema_version: 2; completeness_enforced: boolean;
  identity: Record<string, string>;
  background: Record<string, string | number | null | string[]>;
  communication: Record<string, string | number | string[]>;
  playbook: Record<string, string>;
  confirmations: Record<string, "missing" | "self_declared" | "owner_confirmed" | "authoritatively_verified" | "not_applicable">;
  na_reasons: Record<string, string>;
  source_review: SourceReview;
};

/** What imported documents said. Written by the server; the owner records decisions only. */
export type SourceClaim = {
  statement: string; source_status: "self_reported" | "unverified"; field_path: string; source_excerpt: string;
  owner_status: "unreviewed" | "owner_confirmed" | "owner_disputed"; owner_note: string;
};
export type SourceDetail = { section: string; detail: string; owner_status: "unreviewed" | "kept" | "dismissed" };
export type SourceEvidence = { field_path: string; source_excerpt: string; confidence: string; needs_owner_confirmation: boolean };
export type SourceReview = {
  imports: { job_id: string; file_name: string; sha256: string }[];
  claims: SourceClaim[]; unmapped_details: SourceDetail[]; evidence: SourceEvidence[];
  missing_questions: string[]; contradictions: string[]; completeness_warnings: string[];
};

export type PersonaDraft = {
  persona_id: number; revision: number; base_version_id: number | null;
  full_name: string; display_name: string; professional_role: string;
  professional_title: string | null; brokerage_name: string; biography: string;
  experience_summary: string; specialties: string[]; service_areas: string[];
  sms_signature: string; style_config: CommunicationStyle; style_instructions: string;
  preferred_language: string; profile_config: ProfileConfig; review_status: string;
  submitted_at: string | null; updated_at: string;
};

export type PersonaDetail = PersonaSummary & { draft: PersonaDraft | null; active_version: PersonaVersion | null };
export type PersonaVersion = Omit<PersonaDraft, "revision" | "base_version_id" | "review_status" | "submitted_at" | "updated_at"> & {
  id: number; version_number: number; published_by_user_id: number; published_at: string;
};
export type PersonaSubmission = {
  id: number; persona_id: number; submission_number: number; draft_revision: number;
  snapshot: Record<string, unknown>; status: string; submitted_at: string;
  reviewed_by_user_id: number | null; reviewed_at: string | null;
  rejection_reason: string | null; published_version_id: number | null;
};
export type PersonaValidation = { valid: boolean; errors: string[]; warnings: string[]; revision: number; missing_count: number; sections: Record<string, { complete: boolean; missing: string[] }> };
export type PersonaExample = { id: number; position: number; scenario: string; channel: "sms" | "email"; language: string; customer_message: string; preferred_response: string; context: string; style_explanation: string; provenance: "authentic" | "user_written" | "illustrative"; owner_approved: boolean; review_flags?: string[]; revision?: number | null };
export type ExamplePage = { items: PersonaExample[]; total: number; page: number; page_size: number; revision: number | null };

export type PersonaImportStage = {
  status: "pending" | "running" | "completed" | "failed";
  started_at: string | null; completed_at: string | null; message: string | null;
};
export type PersonaImportJob = {
  id: string; persona_id: number; base_revision: number; file_name: string; file_size: number; file_hash: string;
  status: "queued" | "processing" | "completed" | "failed" | "applied"; current_stage: string | null;
  stages: Record<string, PersonaImportStage>; proposal: Record<string, unknown> | null;
  assessment: { missing_questions?: string[]; contradictions?: string[]; unsupported_claims?: string[]; uncertain_fields?: string[] } | null;
  error_code: string | null; error_message: string | null; retryable: boolean; attempt_count: number; extraction_mode?: "local" | "ai";
  created_at: string; updated_at: string; completed_at: string | null; applied_at: string | null;
};


/** An example in the working form: saved (positive id) or still part of an unapplied import (negative id). */
export type FormExample = PersonaExample & { source_index?: number };

/** The structured result of a completed import, as the server stored it. */
export type ImportProposal = {
  draft_patch: Partial<Pick<PersonaDraft, "full_name" | "display_name" | "professional_role" | "professional_title" | "biography" | "experience_summary" | "specialties" | "service_areas" | "preferred_language">>;
  style_patch: Partial<CommunicationStyle>;
  profile_patch: Partial<Pick<ProfileConfig, "identity" | "background" | "communication" | "playbook" | "confirmations">>;
  examples: Omit<PersonaExample, "id" | "position" | "owner_approved">[];
  evidence: SourceEvidence[];
  claims: { statement: string; status: SourceClaim["source_status"]; field_path: string; source_excerpt: string }[];
  unmapped_details: { section: string; detail: string }[];
};

/** What the auto-filled form sends to Apply: exactly what the owner reviewed. */
export type ReviewedImport = {
  draft: Pick<PersonaDraft, "full_name" | "biography" | "specialties" | "service_areas" | "preferred_language"> & { professional_title: string | null };
  profile: { identity: Record<string, string>; background: Record<string, string[]>; communication: { other_languages: string[] }; confirmations: ProfileConfig["confirmations"]; na_reasons: Record<string, string> };
  examples: { source_index?: number; scenario: string; channel: "sms" | "email"; language: string; customer_message: string; preferred_response: string; provenance: PersonaExample["provenance"] }[];
  source_review: { claims: SourceClaim[]; unmapped_details: SourceDetail[] };
};
