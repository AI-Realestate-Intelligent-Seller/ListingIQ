import { publicEnv } from "@/config/public-env";

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
    response = await fetch(buildApiUrl(path), {
      method,
      headers: {
        ...(payload === undefined ? {} : { "Content-Type": "application/json" }),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
      signal,
    });
  } catch (error) {
    // An aborted request is a navigation, not a failure worth showing.
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
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
    response = await fetch(buildApiUrl(path), {
      method: "POST",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      body,
    });
  } catch {
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
    response = await fetch(buildApiUrl(path), {
      method: "POST",
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      body,
    });
  } catch {
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
