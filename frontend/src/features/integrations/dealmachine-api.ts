import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { requestJson } from "@/lib/api/http-client";

const root = "/integrations/dealmachine";
type Method = "GET" | "POST" | "PUT" | "DELETE";
async function call<T>(
  path: string,
  method: Method = "GET",
  payload?: unknown,
) {
  const session = readAuthSession();
  if (!session || session.user.role !== "platform_admin")
    throw new Error("Internal administrator access is required.");
  return requestJson<T>(root + path, {
    method,
    payload,
    accessToken: session.access_token,
  });
}

export const dealMachineApi = {
  overview: <T>() => call<T>(""),
  testConnection: <T>() => call<T>("/connection/test", "POST", {}),
  filters: <T>(refresh = false) => call<T>(`/filters?refresh=${refresh}`),
  fields: <T>(refresh = false) => call<T>(`/fields?refresh=${refresh}`),
  usage: <T>() => call<T>("/usage"),
  propertyCount: <T>(body: unknown) =>
    call<T>("/properties/count", "POST", body),
  propertyEstimate: <T>(body: unknown) =>
    call<T>("/properties/estimate", "POST", body),
  propertySearch: <T>(body: unknown) =>
    call<T>("/properties/search", "POST", body),
  propertyDetails: <T>(body: unknown) =>
    call<T>("/properties/details", "POST", body),
  contactEnrichment: <T>(body: unknown) =>
    call<T>("/contacts/enrich", "POST", body),
  createList: <T>(body: unknown) => call<T>("/lists", "POST", body),
  listStatus: <T>(id: string) => call<T>(`/lists/${encodeURIComponent(id)}`),
  addListItems: <T>(id: string, body: unknown) =>
    call<T>(`/lists/${encodeURIComponent(id)}/items`, "POST", body),
  removeListItems: <T>(id: string, body: unknown) =>
    call<T>(`/lists/${encodeURIComponent(id)}/items`, "DELETE", body),
  activitySearch: <T>(body: unknown) =>
    call<T>("/activity/search", "POST", body),
  activityDetail: <T>(id: string) =>
    call<T>(`/activity/${encodeURIComponent(id)}`),
  propertyExport: <T>(body: unknown) =>
    call<T>("/properties/export", "POST", body),
  history: <T>() => call<T>("/history"),
  historyDetail: <T>(id: string) =>
    call<T>(`/history/${encodeURIComponent(id)}`),
  settings: <T>() => call<T>("/settings"),
  saveSettings: <T>(body: unknown) => call<T>("/settings", "PUT", body),
  testSettings: <T>() => call<T>("/settings/test", "POST", {}),
  saveCategory: <T>(id: string, body: unknown) =>
    call<T>(`/categories/${encodeURIComponent(id)}`, "PUT", body),
};
