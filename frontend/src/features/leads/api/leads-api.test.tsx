import { beforeEach, describe, expect, it, vi } from "vitest";

import { getJson } from "@/lib/api/http-client";
import { fetchLeadPool } from "./leads-api";


vi.mock("@/lib/api/http-client", () => ({
  getJson: vi.fn(() => Promise.resolve({})),
  postFile: vi.fn(),
  postForm: vi.fn(),
  postJson: vi.fn(),
}));

const request = vi.mocked(getJson);

beforeEach(() => request.mockClear());

describe("Lead Pool map query", () => {
  it("combines explicit map bounds with existing filters", async () => {
    await fetchLeadPool({
      signals: ["expired"],
      states: ["IL"],
      bounds: { north: 42, south: 41, east: -87, west: -88 },
      polygon: [[42, -88], [42, -87], [41, -87]],
    }, "token");

    const url = String(request.mock.calls[0][0]);
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("signal")).toBe("expired");
    expect(params.get("state")).toBe("IL");
    expect(params.get("north")).toBe("42");
    expect(params.get("south")).toBe("41");
    expect(params.get("east")).toBe("-87");
    expect(params.get("west")).toBe("-88");
    expect(JSON.parse(params.get("polygon") || "[]")).toEqual([
      [42, -88], [42, -87], [41, -87],
    ]);
  });

  it("clears the geographic filter by omitting all bound parameters", async () => {
    await fetchLeadPool({ signals: ["expired"] }, "token");
    const url = String(request.mock.calls[0][0]);
    expect(url).toBe("/leads?signal=expired");
  });
});
