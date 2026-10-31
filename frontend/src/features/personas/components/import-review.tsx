"use client";

import type { PersonaExample, ProfileConfig, SourceClaim, SourceReview } from "../types";

export const emptySourceReview = (): SourceReview => ({
  imports: [], claims: [], unmapped_details: [], evidence: [],
  missing_questions: [], contradictions: [], completeness_warnings: [],
});

const provenanceLabels: Record<PersonaExample["provenance"], string> = {
  authentic: "Authentic (owner-stated)",
  user_written: "Written by the owner",
  illustrative: "Illustrative · not a real conversation",
};
const claimSource: Record<SourceClaim["source_status"], string> = {
  self_reported: "Self-reported in the document",
  unverified: "Stated without support in the document",
};

export function ProvenanceBadge({ example }: { example: PersonaExample }) {
  return <span className={`persona-badge provenance-${example.provenance}`}>{provenanceLabels[example.provenance]}</span>;
}

export function ExampleFlags({ flags }: { flags?: string[] }) {
  if (!flags?.length) return null;
  return <ul className="persona-flags" aria-label="Import review flags">{flags.map((flag) => <li key={flag}>{flag}</li>)}</ul>;
}

function List({ title, items, empty }: { title: string; items: string[]; empty?: string }) {
  if (!items.length && !empty) return null;
  return <section className="persona-review-block"><h3>{title}</h3>{items.length ? <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul> : <p>{empty}</p>}</section>;
}

export const FIELD_LABELS: Record<string, string> = {
  full_name: "Full name", professional_title: "Professional title", license_number: "License number",
  licensing_jurisdiction: "Licensing jurisdiction", licensing_status: "License status", biography: "Professional biography",
  specialties: "Specialties", service_areas: "Service areas", brokerage_name: "Brokerage", preferred_language: "Primary language",
  "at least one conversation example": "At least one conversation example", "at least one owner-approved example": "At least one approved example",
};

/** HOB view of what the frozen submission says about imported, unverified material. */
export function SubmissionReviewSummary({ snapshot }: { snapshot: Record<string, unknown> }) {
  const profile = (snapshot.profile_config ?? {}) as Partial<ProfileConfig>;
  const review = { ...emptySourceReview(), ...profile.source_review };
  if (!review.imports.length) return null;
  const ownerLabel: Record<SourceClaim["owner_status"], string> = { unreviewed: "not reviewed by owner", owner_confirmed: "owner confirmed", owner_disputed: "owner disputed" };
  return <div className="persona-import-review-panel">
    <List title="Imported claims (not independently verified)" items={review.claims.map((claim) => `${claim.statement} — ${claimSource[claim.source_status]}; ${ownerLabel[claim.owner_status]}${claim.owner_note ? ` (${claim.owner_note})` : ""}`)}/>
    <List title="Missing information at import" items={[...new Set([...review.completeness_warnings, ...review.missing_questions])]}/>
    <List title="Contradictions at import" items={review.contradictions}/>
    <List title="Unmapped imported details" items={review.unmapped_details.map((detail) => `${detail.section}: ${detail.detail} (${detail.owner_status})`)}/>
  </div>;
}
