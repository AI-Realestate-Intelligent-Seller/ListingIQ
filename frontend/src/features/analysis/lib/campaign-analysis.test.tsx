import { describe, expect, it } from "vitest";

import type { Campaign } from "@/features/campaigns/types/campaigns.types";
import type { Experiment } from "@/features/experiments/types/experiments.types";

import { campaignMetrics, groupCampaignsByExperiment, groupCampaignsByTemplate, normalizeTemplate } from "./campaign-analysis";

function campaign(overrides: Partial<Campaign> & Pick<Campaign, "id" | "message_template">): Campaign {
  return {
    name: `Campaign ${overrides.id}`,
    outreach_reason: "",
    group_reason_mode: false,
    status: "sent",
    sent_at: "2026-11-01T12:00:00Z",
    created_at: "2026-10-31T12:00:00Z",
    recipients: 100,
    delivered: 90,
    replied: 18,
    positive_replies: 4,
    no_reply: 82,
    not_sent: 10,
    broker_id: 1,
    broker_name: "Broker",
    broker_email: "broker@example.com",
    broker_role: "broker",
    ...overrides,
  };
}

describe("campaign template analysis", () => {
  it("keeps different messages together when campaigns belong to one experiment", () => {
    const first = campaign({ id: 11, message_template: "Direct message" });
    const second = campaign({ id: 12, message_template: "Conversational message" });
    const experiment = {
      id: 7, name: "Direct vs conversational", campaign_ids: [11, 12],
      variants: [
        { label: "Variant A", message_template: first.message_template, campaign_id: 11 },
        { label: "Variant B", message_template: second.message_template, campaign_id: 12 },
      ],
    } as Experiment;

    const groups = groupCampaignsByExperiment([first, second], [experiment]);

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ experimentId: 7, name: "Direct vs conversational" });
    expect(groups[0].campaigns).toHaveLength(2);
    expect(groups[0].messages.map((item) => item.template)).toEqual(["Direct message", "Conversational message"]);
  });

  it("treats added, removed, or repeated whitespace as one template", () => {
    expect(normalizeTemplate(" Hi   {{owner_name}}\nthere ")).toBe("Hi{{owner_name}}there");
    const variants = groupCampaignsByTemplate([
      campaign({ id: 1, message_template: "Hi {{owner_name}} there" }),
      campaign({ id: 2, message_template: "Hi{{owner_name}}there" }),
      campaign({ id: 3, message_template: " Hi  {{owner_name}}\nthere " }),
    ]);
    expect(variants).toHaveLength(1);
    expect(variants[0].campaigns).toHaveLength(3);
  });

  it("excludes drafts from results while keeping them in their template group", () => {
    const variants = groupCampaignsByTemplate([
      campaign({ id: 1, message_template: "A", recipients: 50, delivered: 40, replied: 8 }),
      campaign({ id: 2, message_template: "A", status: "draft", recipients: 500, delivered: 0, replied: 0 }),
    ]);
    expect(variants[0].campaigns).toHaveLength(2);
    expect(variants[0].draftCampaigns).toHaveLength(1);
    expect(variants[0].metrics).toMatchObject({ recipients: 50, delivered: 40, replied: 8, replyRate: 0.2 });
  });

  it("builds a mutually exclusive outcome distribution", () => {
    expect(campaignMetrics([
      campaign({ id: 1, message_template: "A", recipients: 100, delivered: 80, replied: 12 }),
    ])).toEqual({
      recipients: 100,
      delivered: 80,
      replied: 12,
      positiveReplies: 4,
      deliveredWithoutReply: 68,
      notReached: 20,
      deliveryRate: 0.8,
      replyRate: 0.15,
    });
  });
});
