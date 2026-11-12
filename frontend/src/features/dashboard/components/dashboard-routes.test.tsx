import { describe, expect, it } from "vitest";

import {
  actionFromDashboardPath,
  allowedTarget,
  dashboardPath,
  legacyActionFromSearch,
  pathForRole,
  resolveDestination,
} from "./dashboard-routes";

describe("dashboardPath / actionFromDashboardPath", () => {
  const cases = [
    [{ view: "overview" }, "/dashboard/broker/overview"],
    [{ view: "leads" }, "/dashboard/broker/leads"],
    [{ view: "leads", lead_id: 45 }, "/dashboard/broker/leads/45"],
    [{ view: "assignments", lead_id: 9 }, "/dashboard/broker/assignments/9"],
    [{ view: "campaigns", campaign_id: 12 }, "/dashboard/broker/campaigns/12"],
    [{ view: "followups", conversation_id: 123 }, "/dashboard/broker/followups?conversation_id=123"],
    [{ view: "followups", campaign_id: 7 }, "/dashboard/broker/followups?campaign_id=7"],
    [{ view: "followups", conversation_id: 3, campaign_id: 7 }, "/dashboard/broker/followups?conversation_id=3&campaign_id=7"],
    [{ view: "calendar", booking_id: 5 }, "/dashboard/broker/calendar/5"],
    [{ view: "directory" }, "/dashboard/broker/directory"],
    [{ view: "persona" }, "/dashboard/broker/persona"],
  ] as const;

  it.each(cases)("round-trips %j", (action, path) => {
    expect(dashboardPath("broker", action)).toBe(path);
    const [pathname, search = ""] = path.split("?");
    expect(actionFromDashboardPath(pathname, search)).toEqual(action);
  });

  it("still opens the earlier /followups/{id} links", () => {
    expect(actionFromDashboardPath("/dashboard/broker/followups/18")).toEqual({ view: "followups", conversation_id: 18 });
  });

  it("returns null when the path has no section", () => {
    expect(actionFromDashboardPath("/dashboard/broker")).toBeNull();
    expect(actionFromDashboardPath("/dashboard")).toBeNull();
  });

  it("ignores unknown sections and ids a section does not use", () => {
    expect(actionFromDashboardPath("/dashboard/broker/nope")).toBeNull();
    expect(actionFromDashboardPath("/dashboard/broker/overview/4")).toEqual({ view: "overview" });
  });
});

describe("role rules", () => {
  it("keeps the rules navigateDashboard always applied", () => {
    expect(allowedTarget("assignments", "agent")).toBe("leads");
    expect(allowedTarget("assignments", "hob")).toBe("overview");
    expect(allowedTarget("assignments", "broker")).toBe("assignments");
    expect(allowedTarget("directory", "broker")).toBe("overview");
    expect(allowedTarget("directory", "hob")).toBe("directory");
    expect(allowedTarget("campaigns", "agent")).toBe("overview");
  });

  it("drops ids when the section was redirected", () => {
    expect(resolveDestination({ view: "campaigns", campaign_id: 4 }, "agent")).toEqual({ view: "overview" });
    expect(resolveDestination({ view: "followups", conversation_id: 2, booking_id: 9 }, "broker"))
      .toEqual({ view: "followups", conversation_id: 2 });
  });
});

describe("legacy links and role scoping", () => {
  it("reads old ?view= push links", () => {
    expect(legacyActionFromSearch("/dashboard", "?view=followups&conversation_id=8"))
      .toEqual({ view: "followups", conversation_id: 8 });
    expect(legacyActionFromSearch("/dashboard/broker", "")).toBeNull();
  });

  it("moves a URL under the signed-in role", () => {
    expect(pathForRole("/dashboard", "hob")).toBe("/dashboard/hob");
    expect(pathForRole("/dashboard/agent", "hob")).toBe("/dashboard/hob");
    expect(pathForRole("/dashboard/agent/followups/3", "hob")).toBe("/dashboard/hob/followups/3");
  });
});
