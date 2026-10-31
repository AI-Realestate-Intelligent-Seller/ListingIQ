import { getJson, patchJson, postFile, postJson, requestJson } from "@/lib/api/http-client";
import { publicEnv } from "@/config/public-env";
import type { ExamplePage, PersonaDetail, PersonaDraft, PersonaExample, PersonaImportJob, PersonaSubmission, PersonaSummary, PersonaValidation, PersonaVersion, ReviewedImport } from "../types";

const ROOT = "/personas";
export const listPersonas = (token: string, signal?: AbortSignal) => getJson<PersonaSummary[]>(ROOT, token, signal);
export const createPersona = (fullName: string, token: string) => postJson<PersonaDetail, { full_name: string }>(ROOT, { full_name: fullName }, token);
export const getPersona = (id: number, token: string) => getJson<PersonaDetail>(`${ROOT}/${id}`, token);
export const saveDraft = (id: number, payload: Record<string, unknown>, token: string) => patchJson<PersonaDraft, Record<string, unknown>>(`${ROOT}/${id}/draft`, payload, token);
export const validateDraft = (id: number, token: string) => postJson<PersonaValidation, Record<string, never>>(`${ROOT}/${id}/validate`, {}, token);
/** The owner publishes their own Personal Persona directly; no HOB review (409 if the draft changed). */
export const publishOwnPersona = (id: number, revision: number, token: string) => postJson<PersonaVersion, { revision: number }>(`${ROOT}/${id}/versions`, { revision }, token);
export const submitDraft = (id: number, revision: number, token: string) => postJson<PersonaSubmission, { revision: number }>(`${ROOT}/${id}/submit`, { revision }, token);
export const listSubmissions = (id: number, token: string) => getJson<PersonaSubmission[]>(`${ROOT}/${id}/submissions`, token);
export const publishSubmission = (id: number, submissionId: number, token: string) => postJson<PersonaVersion, { submission_id: number }>(`${ROOT}/${id}/publish`, { submission_id: submissionId }, token);
export const rejectSubmission = (id: number, submissionId: number, reason: string, token: string) => postJson<PersonaSubmission, { reason: string }>(`${ROOT}/${id}/submissions/${submissionId}/reject`, { reason }, token);
export const listVersions = (id: number, token: string) => getJson<PersonaVersion[]>(`${ROOT}/${id}/versions`, token);
export const activateVersion = (id: number, version: number, token: string) => postJson<PersonaVersion, Record<string, never>>(`${ROOT}/${id}/versions/${version}/activate`, {}, token);
export const setDefault = (channel: "sms" | "email", personaId: number | null, token: string) => requestJson(`${ROOT}/defaults/${channel}`, { method: "PUT", payload: { persona_id: personaId }, accessToken: token });
export const listExamples = (id: number, token: string, page = 1, channel = "", scenario = "") => getJson<ExamplePage>(`${ROOT}/${id}/examples?page=${page}&page_size=20${channel ? `&channel=${channel}` : ""}${scenario ? `&scenario=${encodeURIComponent(scenario)}` : ""}`, token);
export const createExample = (id: number, payload: Record<string, unknown>, token: string) => postJson<PersonaExample, Record<string, unknown>>(`${ROOT}/${id}/examples`, payload, token);
export const updateExample = (id: number, exampleId: number, payload: Record<string, unknown>, token: string) => patchJson<PersonaExample, Record<string, unknown>>(`${ROOT}/${id}/examples/${exampleId}`, payload, token);
/** Saves the How I Communicate step in one transaction; every saved example is owner-approved (409 if stale). */
export type ExampleBatchItem = { id?: number; scenario: string; channel: "sms" | "email"; language: string; customer_message: string; preferred_response: string; provenance?: PersonaExample["provenance"] };
export const saveExampleList = (id: number, revision: number, examples: ExampleBatchItem[], token: string) => requestJson<ExamplePage>(`${ROOT}/${id}/examples`, { method: "PUT", payload: { revision, examples }, accessToken: token });
export const deleteExample = (id: number, exampleId: number, revision: number, token: string) => requestJson<{ deleted: boolean; revision: number }>(`${ROOT}/${id}/examples/${exampleId}`, { method: "DELETE", payload: { revision }, accessToken: token });
export const duplicateExample = (id: number, exampleId: number, revision: number, token: string) => postJson<PersonaExample, { revision: number }>(`${ROOT}/${id}/examples/${exampleId}/duplicate`, { revision }, token);
export const uploadPersonaImport = (id: number, file: File, token: string) => postFile<PersonaImportJob>(`${ROOT}/${id}/imports`, file, token);
export const listPersonaImports = (id: number, token: string) => getJson<PersonaImportJob[]>(`${ROOT}/${id}/imports`, token);
export const getPersonaImport = (id: number, jobId: string, token: string) => getJson<PersonaImportJob>(`${ROOT}/${id}/imports/${jobId}`, token);
export const retryPersonaImport = (id: number, jobId: string, token: string) => postJson<PersonaImportJob, Record<string, never>>(`${ROOT}/${id}/imports/${jobId}/retry`, {}, token);
/** Applies the owner's reviewed form values and examples in one transaction (409 if the draft changed). */
export const applyPersonaImport = (id: number, jobId: string, revision: number, token: string, reviewed?: ReviewedImport) => postJson<PersonaDraft, { revision: number; reviewed?: ReviewedImport }>(`${ROOT}/${id}/imports/${jobId}/apply`, reviewed ? { revision, reviewed } : { revision }, token);
export async function downloadPersonaTemplate(token: string): Promise<void> {
  const response = await fetch(`${publicEnv.apiBaseUrl}${ROOT}/template`, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) throw new Error("Could not download the Persona template.");
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "listingiq-persona-template.docx"; anchor.click(); URL.revokeObjectURL(url);
}
export async function listSubmissionExamples(id: number, submissionId: number, token: string): Promise<ExamplePage> {
  const first = await getJson<ExamplePage>(`${ROOT}/${id}/submissions/${submissionId}/examples?page=1&page_size=100`, token);
  const pages = Math.ceil(first.total / 100);
  if (pages <= 1) return first;
  const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, index) => getJson<ExamplePage>(`${ROOT}/${id}/submissions/${submissionId}/examples?page=${index + 2}&page_size=100`, token)));
  return { ...first, items: [first, ...rest].flatMap((page) => page.items) };
}
export async function listAllExamples(id: number, token: string): Promise<ExamplePage> {
  const first = await getJson<ExamplePage>(`${ROOT}/${id}/examples?page=1&page_size=100`, token);
  const pages = Math.ceil(first.total / 100);
  if (pages <= 1) return first;
  const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, index) => getJson<ExamplePage>(`${ROOT}/${id}/examples?page=${index + 2}&page_size=100`, token)));
  return { ...first, items: [first, ...rest].flatMap((page) => page.items) };
}
