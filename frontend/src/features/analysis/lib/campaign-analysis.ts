import type { Campaign } from "@/features/campaigns/types/campaigns.types";
import type { Experiment } from "@/features/experiments/types/experiments.types";

export type AnalysisMetrics = {
  recipients: number;
  delivered: number;
  replied: number;
  positiveReplies: number;
  deliveredWithoutReply: number;
  notReached: number;
  deliveryRate: number | null;
  replyRate: number | null;
};

export type TemplateVariant = {
  key: string;
  experimentId: number | null;
  name: string;
  template: string;
  messages: { label: string; template: string; campaignId: number | null }[];
  campaigns: Campaign[];
  testedCampaigns: Campaign[];
  draftCampaigns: Campaign[];
  metrics: AnalysisMetrics;
  latestAt: number;
};

/** Any whitespace difference is formatting only and never creates a new variant. */
export function normalizeTemplate(template: string): string {
  return template.replace(/\s+/g, "");
}

export function campaignMetrics(campaigns: Campaign[]): AnalysisMetrics {
  const totals = campaigns.reduce(
    (sum, campaign) => ({
      recipients: sum.recipients + campaign.recipients,
      delivered: sum.delivered + campaign.delivered,
      replied: sum.replied + campaign.replied,
      positiveReplies: sum.positiveReplies + campaign.positive_replies,
    }),
    { recipients: 0, delivered: 0, replied: 0, positiveReplies: 0 },
  );
  const delivered = Math.min(totals.delivered, totals.recipients);
  const replied = Math.min(totals.replied, delivered);

  return {
    recipients: totals.recipients,
    delivered,
    replied,
    positiveReplies: Math.min(totals.positiveReplies, replied),
    deliveredWithoutReply: Math.max(delivered - replied, 0),
    notReached: Math.max(totals.recipients - delivered, 0),
    deliveryRate: totals.recipients ? delivered / totals.recipients : null,
    // Template performance is measured only among owners the provider reached.
    replyRate: delivered ? replied / delivered : null,
  };
}

function campaignTime(campaign: Campaign): number {
  const value = campaign.sent_at ?? campaign.created_at;
  const time = value ? new Date(value).getTime() : 0;
  return Number.isNaN(time) ? 0 : time;
}

export function groupCampaignsByTemplate(campaigns: Campaign[]): TemplateVariant[] {
  const groups = new Map<string, Campaign[]>();

  for (const campaign of campaigns) {
    const normalized = normalizeTemplate(campaign.message_template);
    const key = normalized || "__empty_template__";
    groups.set(key, [...(groups.get(key) ?? []), campaign]);
  }

  return [...groups.entries()]
    .map(([key, rows]) => {
      const sorted = [...rows].sort((a, b) => campaignTime(b) - campaignTime(a) || b.id - a.id);
      const testedCampaigns = sorted.filter((campaign) => campaign.status !== "draft");
      return {
        key,
        experimentId: null,
        name: "Legacy message group",
        template: sorted[0]?.message_template.trim() || "No message template yet",
        messages: [{ label: "Message", template: sorted[0]?.message_template.trim() || "No message template yet", campaignId: sorted[0]?.id ?? null }],
        campaigns: sorted,
        testedCampaigns,
        draftCampaigns: sorted.filter((campaign) => campaign.status === "draft"),
        metrics: campaignMetrics(testedCampaigns),
        latestAt: Math.max(0, ...sorted.map(campaignTime)),
      };
    })
    .sort((a, b) => b.latestAt - a.latestAt || a.key.localeCompare(b.key));
}

/**
 * New A/B/n campaigns are grouped by their persisted experiment relationship.
 * Message text is deliberately irrelevant: Variant A, B, and N belong to the
 * same experiment even when every outreach message is different.
 * Older campaigns without an experiment retain the whitespace-normalized
 * fallback so historical analysis remains available.
 */
export function groupCampaignsByExperiment(
  campaigns: Campaign[],
  experiments: Experiment[],
): TemplateVariant[] {
  const experimentByCampaign = new Map<number, Experiment>();
  experiments.forEach((experiment) => experiment.campaign_ids.forEach((campaignId) => {
    experimentByCampaign.set(campaignId, experiment);
  }));

  const groups = new Map<string, Campaign[]>();
  for (const campaign of campaigns) {
    const experiment = experimentByCampaign.get(campaign.id);
    const normalized = normalizeTemplate(campaign.message_template) || "__empty_template__";
    const key = experiment ? `experiment:${experiment.id}` : `legacy:${normalized}`;
    groups.set(key, [...(groups.get(key) ?? []), campaign]);
  }

  return [...groups.entries()].map(([key, rows]) => {
    const sorted = [...rows].sort((a, b) => campaignTime(b) - campaignTime(a) || b.id - a.id);
    const experiment = experimentByCampaign.get(sorted[0]?.id);
    const testedCampaigns = sorted.filter((campaign) => campaign.status !== "draft");
    const messages = experiment
      ? experiment.variants.map((variant) => ({
          label: variant.label,
          template: variant.message_template,
          campaignId: variant.campaign_id,
        }))
      : [...new Map(sorted.map((campaign) => [normalizeTemplate(campaign.message_template), {
          label: campaign.name,
          template: campaign.message_template.trim() || "No message template yet",
          campaignId: campaign.id,
        }])).values()];
    return {
      key,
      experimentId: experiment?.id ?? null,
      name: experiment?.name ?? sorted[0]?.name ?? "Legacy campaign group",
      template: messages[0]?.template ?? "No message template yet",
      messages,
      campaigns: sorted,
      testedCampaigns,
      draftCampaigns: sorted.filter((campaign) => campaign.status === "draft"),
      metrics: campaignMetrics(testedCampaigns),
      latestAt: Math.max(0, ...sorted.map(campaignTime)),
    };
  }).sort((a, b) => b.latestAt - a.latestAt || a.key.localeCompare(b.key));
}
