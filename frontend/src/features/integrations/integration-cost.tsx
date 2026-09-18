import type { ReactNode } from "react";

export type IntegrationCostKind = "free" | "paid";

export function IntegrationCostBadge({ kind }: { kind: IntegrationCostKind }) {
  return (
    <span className={`integration-cost-badge ${kind}`}>
      {kind === "paid" ? "Paid" : "Free"}
    </span>
  );
}

export function IntegrationActionGuidance({
  kind,
  children,
}: {
  kind: IntegrationCostKind;
  children?: ReactNode;
}) {
  return (
    <div className={`integration-action-guidance ${kind}`}>
      <strong>
        {kind === "paid" ? "Paid manual action" : "Free manual action"}
      </strong>
      <span>
        {children ||
          (kind === "paid"
            ? "Nothing runs automatically. Review the cost and confirm before provider credits are used."
            : "Nothing runs automatically, and this action does not use provider credits.")}
      </span>
    </div>
  );
}
