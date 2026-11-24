import { publicEnv } from "@/config/public-env";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { ensureStoredSessionFresh } from "@/features/auth/lib/session-refresh";

type FastApiValidationError = {
  msg?: unknown;
};

type FastApiErrorBody = {
  detail?: unknown;
};

export class ApiRequestError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

type RequestOptions = {
  method?: HttpMethod;
  payload?: unknown;
  accessToken?: string;
  signal?: AbortSignal;
};

/** Single entry point for every API call: JSON in, JSON out, FastAPI errors unwrapped. */
export async function requestJson<TResponse>(
  path: string,
  { method = "GET", payload, accessToken, signal }: RequestOptions = {},
): Promise<TResponse> {
  let response: Response;

  try {
    response = await fetchWithCurrentSession(buildApiUrl(path), {
      method,
      headers: {
        ...(payload === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
      signal,
    }, accessToken);
  } catch (error) {
    // An aborted request is a navigation, not a failure worth showing.
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    if (error instanceof ApiRequestError) throw error;
    throw new ApiRequestError(
      "Unable to reach the server. Make sure the API is running and try again.",
    );
  }

  const responseBody = await readResponseBody(response);

  if (!response.ok) {
    throw new ApiRequestError(getApiErrorMessage(responseBody), response.status);
  }

  return responseBody as TResponse;
}

export async function postJson<TResponse, TRequest>(
  path: string,
  payload: TRequest,
  accessToken?: string,
): Promise<TResponse> {
  return requestJson<TResponse>(path, { method: "POST", payload, accessToken });
}

export async function getJson<TResponse>(
  path: string,
  accessToken?: string,
  signal?: AbortSignal,
): Promise<TResponse> {
  return requestJson<TResponse>(path, { method: "GET", accessToken, signal });
}

export async function patchJson<TResponse, TRequest>(
  path: string,
  payload: TRequest,
  accessToken?: string,
): Promise<TResponse> {
  return requestJson<TResponse>(path, { method: "PATCH", payload, accessToken });
}

/** Multipart upload. The browser sets its own Content-Type with the boundary. */
export async function postFile<TResponse>(
  path: string,
  file: File,
  accessToken?: string,
  field = "file",
  fields: Record<string, string> = {},
): Promise<TResponse> {
  const body = new FormData();
  body.append(field, file);
  for (const [key, value] of Object.entries(fields)) body.append(key, value);

  let response: Response;
  try {
    response = await fetchWithCurrentSession(buildApiUrl(path), {
      method: "POST",
      body,
    }, accessToken);
  } catch (error) {
    if (error instanceof ApiRequestError) throw error;
    throw new ApiRequestError(
      "Unable to reach the server. Make sure the API is running and try again.",
    );
  }

  const responseBody = await readResponseBody(response);
  if (!response.ok) {
    throw new ApiRequestError(getApiErrorMessage(responseBody), response.status);
  }
  return responseBody as TResponse;
}

/** Multipart POST with no file — used to act on an already-staged upload. */
export async function postForm<TResponse>(
  path: string,
  fields: Record<string, string>,
  accessToken?: string,
): Promise<TResponse> {
  const body = new FormData();
  for (const [key, value] of Object.entries(fields)) body.append(key, value);

  let response: Response;
  try {
    response = await fetchWithCurrentSession(buildApiUrl(path), {
      method: "POST",
      body,
    }, accessToken);
  } catch (error) {
    if (error instanceof ApiRequestError) throw error;
    throw new ApiRequestError(
      "Unable to reach the server. Make sure the API is running and try again.",
    );
  }

  const responseBody = await readResponseBody(response);
  if (!response.ok) {
    throw new ApiRequestError(getApiErrorMessage(responseBody), response.status);
  }
  return responseBody as TResponse;
}

export async function deleteJson<TResponse>(
  path: string,
  accessToken?: string,
): Promise<TResponse> {
  return requestJson<TResponse>(path, { method: "DELETE", accessToken });
}

function buildApiUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${publicEnv.apiBaseUrl}${normalizedPath}`;
}

/**
 * Every authenticated request resolves the token from shared browser storage.
 * Components may still hold the token from their first render; using it
 * directly is what previously caused a logout when that one-hour token expired.
 *
 * A 401 gets one forced refresh and retry, covering a browser that slept past
 * the proactive refresh interval. The refresh helper enforces the eight-hour
 * inactivity limit before issuing another token.
 */
export async function fetchWithCurrentSession(
  url: string,
  init: RequestInit,
  suppliedAccessToken?: string,
): Promise<Response> {
  if (!suppliedAccessToken) return fetch(url, init);

  const storedAtStart = readAuthSession();
  let accessToken = suppliedAccessToken;
  if (storedAtStart) {
    try {
      const current = await ensureStoredSessionFresh();
      if (!current) throw new ApiRequestError("Your session has expired.", 401);
      accessToken = current.access_token;
    } catch (error) {
      if (error instanceof ApiRequestError) throw error;
      // Keep the session during a transient refresh failure. The request may
      // still succeed if the existing access token has time remaining.
      accessToken = readAuthSession()?.access_token ?? accessToken;
    }
  }

  const send = (token: string) => fetch(url, {
    ...init,
    headers: { ...Object.fromEntries(new Headers(init.headers).entries()), Authorization: `Bearer ${token}` },
  });
  let response = await send(accessToken);
  if (response.status !== 401 || !readAuthSession()) return response;

  try {
    const refreshed = await ensureStoredSessionFresh(true);
    if (!refreshed) return response;
    response = await send(refreshed.access_token);
  } catch {
    // Preserve browser state on connectivity failures. Do not turn a failed
    // refresh request into a false "session expired" logout.
    throw new ApiRequestError(
      "Unable to refresh your session. Check your connection and try again.",
    );
  }
  return response;
}

async function readResponseBody(response: Response): Promise<unknown> {
  const rawBody = await response.text();

  if (!rawBody) {
    return null;
  }

  try {
    return JSON.parse(rawBody) as unknown;
  } catch {
    return rawBody;
  }
}

function getApiErrorMessage(body: unknown): string {
  if (!isRecord(body)) {
    return "The request could not be completed. Please try again.";
  }

  const detail = (body as FastApiErrorBody).detail;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const firstError = detail[0] as FastApiValidationError | undefined;

    if (typeof firstError?.msg === "string") {
      return firstError.msg;
    }
  }

  return "The request could not be completed. Please try again.";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
