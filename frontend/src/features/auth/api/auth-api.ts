import { getJson, postJson } from "@/lib/api/http-client";

import type {
  AuthResponse,
  InvitableRole,
  InvitationAcceptResponse,
  InvitationCreatedResponse,
  InvitationValidation,
  LoginPayload,
  RegisterPayload,
  RegistrationResponse,
} from "../types/auth.types";

const AUTH_ENDPOINTS = {
  login: "/auth/login",
  register: "/auth/register",
} as const;

const TEAM_ENDPOINTS = {
  invitations: "/team/invitations",
} as const;

export function login(payload: LoginPayload): Promise<AuthResponse> {
  return postJson<AuthResponse, LoginPayload>(AUTH_ENDPOINTS.login, payload);
}

export function register(payload: RegisterPayload): Promise<RegistrationResponse> {
  return postJson<RegistrationResponse, RegisterPayload>(
    AUTH_ENDPOINTS.register,
    payload,
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
): Promise<InvitationCreatedResponse> {
  return postJson<InvitationCreatedResponse, { email: string; role: InvitableRole }>(
    TEAM_ENDPOINTS.invitations,
    { email, role },
    accessToken,
  );
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
