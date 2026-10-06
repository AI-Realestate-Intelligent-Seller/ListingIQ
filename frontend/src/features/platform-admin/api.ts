import { getJson, patchJson, postJson } from "@/lib/api/http-client";
import type { AuditEvent, BrokerageOnboardingResponse, CustomerScreenRole, Engineering, FeatureFlag, Operations, Organization, PlatformOverview, PlatformUser } from "./types";

// apiBaseUrl already ends in /api/v1.
const root = "/platform-admin";
export const getPlatformOverview = (token: string, signal?: AbortSignal) => getJson<PlatformOverview>(`${root}/overview`, token, signal);
export const getOrganizations = (token: string, signal?: AbortSignal) => getJson<{organizations: Organization[]}>(`${root}/organizations`, token, signal);
export const createBrokerageOnboarding = (token: string, payload: {brokerage_name: string; invite_email: string; initial_role: CustomerScreenRole}) => postJson<BrokerageOnboardingResponse, typeof payload>(`${root}/organizations`, payload, token);
export const getPlatformUsers = (token: string, q = "", signal?: AbortSignal) => getJson<{users: PlatformUser[]}>(`${root}/users?q=${encodeURIComponent(q)}`, token, signal);
export const getFeatureFlags = (token: string, signal?: AbortSignal) => getJson<{flags: FeatureFlag[]}>(`${root}/feature-flags`, token, signal);
export const getOperations = (token: string, signal?: AbortSignal) => getJson<Operations>(`${root}/operations`, token, signal);
export const getAuditLog = (token: string, signal?: AbortSignal) => getJson<{events: AuditEvent[]}>(`${root}/audit-log`, token, signal);
export const getEngineering = (token: string, signal?: AbortSignal) => getJson<Engineering>(`${root}/engineering`, token, signal);
export const setOrganizationActive = (token: string, id: string, is_active: boolean, reason: string) => patchJson<{message: string}, {is_active: boolean; reason: string}>(`${root}/organizations/${encodeURIComponent(id)}`, {is_active, reason}, token);
export const setUserActive = (token: string, id: number, is_active: boolean, reason: string) => patchJson<{message: string}, {is_active: boolean; reason: string}>(`${root}/users/${id}`, {is_active, reason}, token);
export const setUserScreenAccess = (token: string, id: number, role: CustomerScreenRole) => patchJson<{message: string; role: CustomerScreenRole}, {role: CustomerScreenRole}>(`${root}/users/${id}/screen-access`, {role}, token);
export const saveFeatureFlag = (token: string, payload: {key: string; description: string; enabled: boolean; brokerage_id: string | null}) => postJson<FeatureFlag, typeof payload>(`${root}/feature-flags`, payload, token);
