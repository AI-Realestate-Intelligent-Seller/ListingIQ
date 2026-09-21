"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

type Provider = "propertyradar" | "batchdata" | "dealmachine";
type StatusTone = "on" | "neutral" | "off";

const providerIcons: Record<Provider, string> = {
  propertyradar: "/integrations/propertyradar.svg",
  batchdata: "/integrations/batchdata.png",
  dealmachine: "/integrations/dealmachine.svg",
};

export function ProviderIcon({ provider }: { provider: Provider }) {
  return (
    <span className={`provider-card-icon ${provider}`} aria-hidden="true">
      <Image src={providerIcons[provider]} alt="" width={32} height={32} />
    </span>
  );
}

export function ProviderIntegrationCard({
  provider,
  name,
  href,
  status,
  statusTone,
  description,
  metrics,
  notice,
  noticeTone = "neutral",
  children,
}: {
  provider: Provider;
  name: string;
  href: string;
  status: string;
  statusTone: StatusTone;
  description: string;
  metrics: Array<{ label: string; value: ReactNode }>;
  notice: ReactNode;
  noticeTone?: "neutral" | "error";
  children?: ReactNode;
}) {
  const router = useRouter();

  return (
    <article
      className="platform-card pr-integration-card provider-card"
      onClick={(event) => {
        if (!(event.target as HTMLElement).closest("button,input,label,a")) {
          router.push(href);
        }
      }}
    >
      {children}
      <div className="provider-card-header">
        <Link className="provider-card-brand" href={href}>
          <ProviderIcon provider={provider} />
          <span>
            <h2>{name}</h2>
            <small>
              Open integration <span aria-hidden="true">↗</span>
            </small>
          </span>
        </Link>
        <span className={`platform-status ${statusTone}`}>{status}</span>
      </div>

      <p className="provider-card-description">{description}</p>

      <dl className="provider-card-metrics">
        {metrics.map((metric) => (
          <div key={metric.label}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
          </div>
        ))}
      </dl>

      <div className={`provider-card-notice ${noticeTone}`}>{notice}</div>

      <div className="provider-card-footer">
        <Link className="button" href={href}>
          Configure
        </Link>
      </div>
    </article>
  );
}
