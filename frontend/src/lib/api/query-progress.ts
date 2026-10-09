import { ApiRequestError, getJson, postJson } from "./http-client";

export type QueryProgress<T> = {
  job_id: string;
  operation: string;
  status: "queued" | "processing" | "completed" | "failed";
  total: number;
  loaded: number;
  remaining: number;
  percent: number;
  result: T | null;
  error: string | null;
};

type StartResponse = { job_id: string };

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, milliseconds);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("The request was aborted.", "AbortError"));
    }, { once: true });
  });
}

/** Runs a Redis-backed query job, with the legacy GET as an outage fallback. */
export async function loadQueryWithProgress<T>({
  startPath,
  fallback,
  accessToken,
  signal,
  onProgress,
  fetchResultAfterCompletion = false,
}: {
  startPath: string;
  fallback: () => Promise<T>;
  accessToken: string;
  signal?: AbortSignal;
  onProgress: (progress: QueryProgress<T>) => void;
  fetchResultAfterCompletion?: boolean;
}): Promise<T> {
  try {
    const { job_id } = await postJson<StartResponse, Record<string, never>>(
      startPath,
      {},
      accessToken,
    );
    while (!signal?.aborted) {
      const progress = await getJson<QueryProgress<T>>(
        `/query-progress/${job_id}`,
        accessToken,
        signal,
      );
      onProgress(progress);
      if (progress.status === "completed" && progress.result !== null) {
        // Let the truthful 100% state paint before the overlay is removed.
        await wait(250, signal);
        return fetchResultAfterCompletion ? fallback() : progress.result;
      }
      if (progress.status === "failed") {
        // Progress must never prevent the underlying view from loading. The
        // established endpoint remains the source-of-truth fallback.
        return fallback();
      }
      await wait(150, signal);
    }
    throw new DOMException("The request was aborted.", "AbortError");
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof ApiRequestError && error.status !== 404 && error.status !== 503) throw error;
    return fallback();
  }
}
