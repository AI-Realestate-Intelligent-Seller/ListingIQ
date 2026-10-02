import { getJson, postJson } from "@/lib/api/http-client";

import type {
  AuthResponse,
  BrokerageOverview,
  InvitableRole,
  InvitationAcceptResponse,
  InvitationCreatedResponse,
  InvitationValidation,
  LoginPayload,
  MessageResponse,
  RegisterPayload,
  RegistrationResponse,
  TeamDirectoryResponse,
} from "../types/auth.types";

const AUTH_ENDPOINTS = {
  login: "/auth/login",
  register: "/auth/register",
  forgotPassword: "/auth/forgot-password",
  resetPassword: "/auth/reset-password",
} as const;

const TEAM_ENDPOINTS = {
  invitations: "/team/invitations",
  directory: "/team/directory",
  overview: "/team/overview",
} as const;

export function login(payload: LoginPayload): Promise<AuthResponse> {
  return postJson<AuthResponse, LoginPayload>(AUTH_ENDPOINTS.login, {
    ...payload,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
}

export function register(payload: RegisterPayload): Promise<RegistrationResponse> {
  return postJson<RegistrationResponse, RegisterPayload>(
    AUTH_ENDPOINTS.register,
    payload,
  );
}

export function requestPasswordReset(email: string): Promise<MessageResponse> {
  return postJson<MessageResponse, { email: string }>(AUTH_ENDPOINTS.forgotPassword, { email });
}

export function resetPassword(token: string, password: string): Promise<MessageResponse> {
  return postJson<MessageResponse, { token: string; password: string }>(
    AUTH_ENDPOINTS.resetPassword,
    { token, password },
  );
}

/**
 * Create a team invitation. The backend derives the brokerage and the inviter
 * from the access token, so only the email and role are sent.
 */
export function createInvitation(
  email: string,
  role: InvitableRole,
  accessToken: string,
  brokerId?: number,
): Promise<InvitationCreatedResponse> {
  return postJson<InvitationCreatedResponse, { email: string; role: InvitableRole; broker_id?: number }>(
    TEAM_ENDPOINTS.invitations,
    { email, role, ...(brokerId === undefined ? {} : { broker_id: brokerId }) },
    accessToken,
  );
}

export function getTeamDirectory(
  accessToken: string,
  signal?: AbortSignal,
): Promise<TeamDirectoryResponse> {
  return getJson<TeamDirectoryResponse>(
    TEAM_ENDPOINTS.directory,
    accessToken,
    signal,
  );
}

export function getBrokerageOverview(
  accessToken: string,
  signal?: AbortSignal,
): Promise<BrokerageOverview> {
  return getJson<BrokerageOverview>(TEAM_ENDPOINTS.overview, accessToken, signal);
}

export function getInvitation(token: string): Promise<InvitationValidation> {
  return getJson<InvitationValidation>(
    `${TEAM_ENDPOINTS.invitations}/${encodeURIComponent(token)}`,
  );
}

export function acceptInvitation(
  token: string,
  payload: { first_name: string; last_name: string; password: string },
): Promise<InvitationAcceptResponse> {
  return postJson<InvitationAcceptResponse, typeof payload>(
    `${TEAM_ENDPOINTS.invitations}/${encodeURIComponent(token)}/accept`,
    payload,
  );
}
