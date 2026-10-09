import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RetrievalProgress } from "./retrieval-progress";

afterEach(cleanup);

describe("RetrievalProgress", () => {
  it("renders exact determinate query counts", () => {
    render(
      <RetrievalProgress
        eyebrow="CAMPAIGNS"
        title="Retrieving campaigns"
        description="Preparing campaigns."
        status="Fetching campaigns for you"
        detail="Preparing totals"
        ariaLabel="Loading campaigns"
        progress={{ total: 20, loaded: 8, remaining: 12, percent: 40 }}
        progressUnit="campaigns"
      />,
    );

    const bar = screen.getByRole("progressbar", { name: "Loading campaigns" });
    expect(bar.getAttribute("aria-valuenow")).toBe("40");
    expect(screen.getByText("20 campaigns total · 8 loaded · 12 remaining")).toBeTruthy();
  });

  it("announces the real completed state", () => {
    render(
      <RetrievalProgress
        eyebrow="ASSIGNMENTS"
        title="Retrieving assignments"
        description="Preparing assignments."
        status="Fetching assignments for you"
        detail="Preparing totals"
        ariaLabel="Loading assignments"
        progress={{ total: 5, loaded: 5, remaining: 0, percent: 100 }}
      />,
    );

    expect(screen.getByText("Retrieval complete")).toBeTruthy();
    expect(screen.getByText("5 items total · 5 loaded · 0 remaining")).toBeTruthy();
  });

  it("shows an empty completed query as 100 percent", () => {
    render(
      <RetrievalProgress
        eyebrow="CAMPAIGNS"
        title="Retrieving campaigns"
        description="Preparing campaigns."
        status="Fetching campaigns for you"
        detail="Preparing totals"
        ariaLabel="Loading campaigns"
        progress={{ total: 0, loaded: 0, remaining: 0, percent: 100 }}
      />,
    );

    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("100");
    expect(screen.getByText("0 items total · 0 loaded · 0 remaining")).toBeTruthy();
  });
});
