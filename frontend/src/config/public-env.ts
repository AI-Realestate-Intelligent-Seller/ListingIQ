const DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1";

function removeTrailingSlash(value: string): string {
  return value.replace(/\/$/, "");
}

export const publicEnv = Object.freeze({
  apiBaseUrl: removeTrailingSlash(
    process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_BASE_URL,
  ),
});
