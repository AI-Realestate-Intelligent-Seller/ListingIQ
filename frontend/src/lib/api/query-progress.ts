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

/**
 * A `/load` call either starts a job to poll, or — when the server already has
 * a current cached result — answers immediately with `status: "completed"`.
 */
type StartResponse<T> =
  | { job_id: string; status?: undefined; result?: undefined }
  | { job_id: null; status: "completed"; result: T };

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
    const started = await postJson<StartResponse<T>, Record<string, never>>(
      startPath,
      {},
      accessToken,
    );
    if (started.status === "completed") return started.result;
    const jobId = started.job_id;
    /** Only pause on 100% if a progress bar was actually on screen. */
    let showedProgress = false;
    while (!signal?.aborted) {
      const progress = await getJson<QueryProgress<T>>(
        `/query-progress/${jobId}`,
        accessToken,
        signal,
      );
      onProgress(progress);
      if (progress.status === "completed" && progress.result !== null) {
        // Let the truthful 100% state paint before the overlay is removed.
        if (showedProgress) await wait(250, signal);
        return fetchResultAfterCompletion ? fallback() : progress.result;
      }
      if (progress.status === "failed") {
        // Progress must never prevent the underlying view from loading. The
        // established endpoint remains the source-of-truth fallback.
        return fallback();
      }
      showedProgress = true;
      await wait(150, signal);
    }
    throw new DOMException("The request was aborted.", "AbortError");
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof ApiRequestError && error.status !== 404 && error.status !== 503) throw error;
    return fallback();
  }
}
