import { afterEach, describe, expect, it, vi } from "vitest";

import { loadQueryWithProgress } from "./query-progress";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("./http-client", () => ({
  ApiRequestError: class ApiRequestError extends Error {
    status: number;
    constructor(message: string, status: number) { super(message); this.status = status; }
  },
  getJson: mocks.get,
  postJson: mocks.post,
}));

const progress = (status: string, percent: number, result: unknown = null) => ({
  job_id: "job-1", operation: "lead-pool", status, total: 10,
  loaded: percent / 10, remaining: 10 - percent / 10, percent, result, error: null,
});

function load(onProgress = vi.fn()) {
  return loadQueryWithProgress({
    startPath: "/leads/load",
    fallback: () => Promise.resolve(["fallback"]),
    accessToken: "token",
    onProgress,
  });
}

afterEach(() => {
  vi.useRealTimers();
  mocks.get.mockReset();
  mocks.post.mockReset();
});

describe("loadQueryWithProgress", () => {
  it("uses a cached result returned by the start call without polling", async () => {
    mocks.post.mockResolvedValue({ job_id: null, status: "completed", result: ["cached"] });
    const onProgress = vi.fn();
    await expect(load(onProgress)).resolves.toEqual(["cached"]);
    expect(mocks.get).not.toHaveBeenCalled();
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("returns at once when the first poll is already complete (no 100% pause)", async () => {
    vi.useFakeTimers();
    mocks.post.mockResolvedValue({ job_id: "job-1" });
    mocks.get.mockResolvedValue(progress("completed", 100, ["rows"]));
    let settled = false;
    const pending = load().then((rows) => { settled = true; return rows; });
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(true);
    await expect(pending).resolves.toEqual(["rows"]);
  });

  it("polls a running job and lets the 100% state paint before resolving", async () => {
    vi.useFakeTimers();
    mocks.post.mockResolvedValue({ job_id: "job-1" });
    mocks.get
      .mockResolvedValueOnce(progress("processing", 40))
      .mockResolvedValueOnce(progress("completed", 100, ["rows"]));
    const onProgress = vi.fn();
    let settled = false;
    const pending = load(onProgress).then((rows) => { settled = true; return rows; });
    await vi.advanceTimersByTimeAsync(150);   // second poll: completed
    expect(settled).toBe(false);              // still showing 100%
    await vi.advanceTimersByTimeAsync(250);
    await expect(pending).resolves.toEqual(["rows"]);
    expect(onProgress).toHaveBeenCalledTimes(2);
    expect(mocks.get).toHaveBeenCalledWith("/query-progress/job-1", "token", undefined);
  });
});
