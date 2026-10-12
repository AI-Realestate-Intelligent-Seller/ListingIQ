"use client";

/**
 * ListingIQ — Lead Pool card
 *
 * React + Tailwind only. Does NOT fetch data and contains NO mock data: the
 * pool's lead model is mapped onto these types by toLeadCardLead
 * (lead-card-adapter.ts). Optional fields render nothing or an empty state.
 * Row actions go through the "…" menu, wired to the pool's existing handlers.
 */

import * as React from "react";

/* ----------------------------------------------------------------------------
 * Types
 * ------------------------------------------------------------------------- */

export interface LeadPhone {
  number: string; // display-ready, e.g. "(312) 555-0144"
  type?: "Mobile" | "Landline" | "VoIP" | (string & {});
  dnc?: boolean;
}

export interface LeadOwner {
  name: string;
  role?: "Owner" | "Contact" | (string & {});
  phones: LeadPhone[];
  emails: string[];
}

export interface LeadSignalChip {
  key: string;
  label: string;
}

export interface Lead {
  id: string;
  street: string;
  city?: string;
  state?: string;
  zip?: string;
  /** The signal the lead was fetched for, e.g. Pre-Foreclosure. */
  primarySignal?: LeadSignalChip | null;
  listingStatus?: string | null;
  listPrice?: number | null;
  estimatedValue?: number | null;
  /** BatchData's own equity classification. */
  equity?: "High" | "Low" | null;
  mlsNumber?: string | null;
  beds?: number | null;
  baths?: number | null;
  livingAreaSqft?: number | null;
  homeType?: string | null;
  dateProcessed?: string | Date | null;
  /** False when the property has no coordinates yet, so the map cannot show it. */
  mapped?: boolean;
  /** Everything behind "Why this lead stands out". */
  signals?: LeadSignalChip[];
  owners: LeadOwner[];
}

export interface LeadCardProps {
  lead: Lead;
  selected?: boolean;
  onSelectedChange?: (leadId: string, selected: boolean) => void;
  onMore?: (lead: Lead, anchor: HTMLButtonElement) => void;
  /** Clicking the address, e.g. to show the property on the map. */
  onAddressClick?: (lead: Lead) => void;
  /** Phones per column and emails per person shown before that person's "+N more". */
  collapsedContactLimit?: { phones: number; emails: number };
  className?: string;
}

/* ----------------------------------------------------------------------------
 * Helpers
 * ------------------------------------------------------------------------- */

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const count = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

function formatMoney(v?: number | null) {
  return typeof v === "number" && !Number.isNaN(v) ? currency.format(v) : "—";
}

function formatDate(v?: string | Date | null) {
  if (!v) return null;
  const d = typeof v === "string" ? new Date(v) : v;
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function cx(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

function chunk<T>(items: T[], size: number): T[][] {
  const rows: T[][] = [];
  for (let i = 0; i < items.length; i += size) rows.push(items.slice(i, i + size));
  return rows;
}

function initials(name: string) {
  const parts = name.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

/** Every signal chip shares one quiet brand-green style. */
const SIGNAL_CHIP = "border-(--blue)/15 bg-(--accent-soft) text-(--blue-dark)";

const EQUITY_STYLES: Record<"High" | "Low", string> = {
  High: "text-emerald-700",
  Low: "text-amber-700",
};

const STATUS_STYLES: Record<string, string> = {
  expired: "bg-amber-100 text-amber-800",
  pending: "bg-sky-100 text-sky-800",
  active: "bg-emerald-100 text-emerald-800",
  canceled: "bg-slate-200 text-slate-700",
  cancelled: "bg-slate-200 text-slate-700",
  withdrawn: "bg-indigo-100 text-indigo-800",
  "off market": "bg-slate-200 text-slate-700",
  sold: "bg-violet-100 text-violet-800",
};

/* ----------------------------------------------------------------------------
 * Icons (inline, stroke = currentColor)
 * ------------------------------------------------------------------------- */

function Icon({ children, size = 15 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {children}
    </svg>
  );
}

const PhoneIcon = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <path d="M5 4h4l2 5-2.5 1.5a11 11 0 005 5L15 13l5 2v4a1 1 0 01-1 1A16 16 0 014 5a1 1 0 011-1z" />
  </Icon>
);
const MailIcon = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="M3 7l9 6 9-6" />
  </Icon>
);
const BanIcon = ({ size }: { size?: number }) => (
  <Icon size={size}>
    <circle cx="12" cy="12" r="9" />
    <path d="M5.6 5.6l12.8 12.8" />
  </Icon>
);
const PinIcon = () => (
  <Icon size={13}>
    <path d="M12 21s-7-6.2-7-11.5a7 7 0 0114 0C19 14.8 12 21 12 21z" />
    <circle cx="12" cy="9.5" r="2.5" />
  </Icon>
);
const BedIcon = () => (
  <Icon size={14}>
    <path d="M3 18V7M21 18v-5a3 3 0 00-3-3h-8v8M3 14h18" />
    <circle cx="6.5" cy="10.5" r="1.5" />
  </Icon>
);
const BathIcon = () => (
  <Icon size={14}>
    <path d="M4 12h16v2a5 5 0 01-5 5H9a5 5 0 01-5-5zM6 12V6a2 2 0 014 0M7 19l-1 2M17 19l1 2" />
  </Icon>
);
const AreaIcon = () => (
  <Icon size={14}>
    <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
  </Icon>
);
const HomeIcon = () => (
  <Icon size={14}>
    <path d="M3 11l9-7 9 7M5 10v10h14V10M10 20v-6h4v6" />
  </Icon>
);
const CalendarIcon = () => (
  <Icon size={13}>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </Icon>
);
const SparkIcon = () => (
  <Icon size={13}>
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />
  </Icon>
);
const MoreIcon = () => (
  <svg width={16} height={16} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
    <circle cx="5" cy="12" r="1.6" />
    <circle cx="12" cy="12" r="1.6" />
    <circle cx="19" cy="12" r="1.6" />
  </svg>
);

/* ----------------------------------------------------------------------------
 * Contact block: one person's numbers in one or two columns, "+N more" under
 * that person's list, then their emails
 * ------------------------------------------------------------------------- */

const AVATAR_STYLES = [
  "bg-emerald-100 text-emerald-800",
  "bg-sky-100 text-sky-800",
  "bg-violet-100 text-violet-800",
  "bg-amber-100 text-amber-800",
];

function PhoneRow({ phone }: { phone: LeadPhone }) {
  return (
    <li className="flex min-w-0 items-center gap-1.5 leading-5">
      <span
        className={cx(
          "flex h-4 w-4 shrink-0 items-center justify-center rounded-full",
          phone.dnc ? "bg-red-50 text-red-600" : "bg-emerald-50 text-emerald-600"
        )}
        title={phone.dnc ? "Do Not Call" : "Callable"}
      >
        {phone.dnc ? <BanIcon size={11} /> : <PhoneIcon size={10} />}
      </span>
      <span
        className={cx(
          "whitespace-nowrap font-mono text-[12px] tabular-nums",
          phone.dnc ? "text-gray-400 line-through decoration-red-300" : "text-gray-900"
        )}
      >
        {phone.number}
      </span>
      {phone.type && <span className="truncate text-[10.5px] text-gray-500">{phone.type}</span>}
      {phone.dnc && (
        <span
          className="shrink-0 rounded border border-red-200 bg-red-50 px-1 text-[10px] font-semibold leading-4 tracking-wide text-red-700"
          title="Do Not Call"
        >
          DNC
        </span>
      )}
    </li>
  );
}

function OwnerContacts({
  owner,
  index,
  columns,
  limit,
}: {
  owner: LeadOwner;
  index: number;
  /** 2 when this is the lead's only person, so the numbers fill both columns. */
  columns: 1 | 2;
  limit: { phones: number; emails: number };
}) {
  const [expanded, setExpanded] = React.useState(false);
  const phoneLimit = limit.phones * columns;
  const phones = expanded ? owner.phones : owner.phones.slice(0, phoneLimit);
  const emails = expanded ? owner.emails : owner.emails.slice(0, limit.emails);
  const hiddenCount =
    Math.max(0, owner.phones.length - phoneLimit) + Math.max(0, owner.emails.length - limit.emails);
  const noContact = owner.phones.length === 0 && owner.emails.length === 0;
  const dncCount = owner.phones.filter((p) => p.dnc).length;
  // Fill the first column before the second, so the list reads top to bottom.
  const phoneColumns = columns === 2 ? chunk(phones, Math.ceil(phones.length / 2) || 1) : [phones];

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex min-w-0 items-center gap-1.5">
        <span
          className={cx(
            "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[9px] font-bold",
            AVATAR_STYLES[index % AVATAR_STYLES.length]
          )}
          aria-hidden
        >
          {initials(owner.name)}
        </span>
        <span className="truncate text-[13.5px] font-semibold text-gray-900">{owner.name}</span>
        {owner.role && (
          <span className="shrink-0 rounded-full bg-gray-100 px-1.5 text-[10.5px] font-medium leading-4 text-gray-600">
            {owner.role}
          </span>
        )}
        {owner.phones.length > 0 && (
          <span className="shrink-0 text-[10.5px] text-gray-500">
            {owner.phones.length} {owner.phones.length === 1 ? "number" : "numbers"}
            {dncCount > 0 && <span className="text-red-600"> · {dncCount} DNC</span>}
          </span>
        )}
      </div>

      {phones.length > 0 && (
        <div className={cx("grid gap-x-5 pl-[26px]", columns === 2 && "sm:grid-cols-2")}>
          {phoneColumns.map((column, c) => (
            <ul key={c} className="flex min-w-0 flex-col">
              {column.map((p, i) => (
                <PhoneRow key={`${p.number}-${i}`} phone={p} />
              ))}
            </ul>
          ))}
        </div>
      )}

      {hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="ml-[26px] flex h-4 items-center self-start rounded text-(--blue) transition-colors duration-150 hover:text-(--blue-dark) focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--blue)"
        >
          {/* base.css gives buttons `font: inherit`, which beats text utilities on the button itself. */}
          <span className="text-[12px] font-medium leading-4">
            {expanded ? "Show less" : `+${hiddenCount} more`}
          </span>
        </button>
      )}

      {emails.length > 0 && (
        <ul className="flex min-w-0 flex-wrap gap-x-3 pl-[26px]">
          {emails.map((e, i) => (
            <li key={`${e}-${i}`} className="flex min-w-0 items-center gap-1 leading-5">
              <span className="shrink-0 text-violet-500">
                <MailIcon size={12} />
              </span>
              <a
                href={`mailto:${e}`}
                className="truncate text-[12.5px] text-gray-700 transition-colors duration-150 hover:text-(--blue)"
              >
                {e}
              </a>
            </li>
          ))}
        </ul>
      )}

      {noContact && <span className="pl-[26px] text-[12.5px] leading-5 text-gray-400">No contact info</span>}
    </div>
  );
}

/* ----------------------------------------------------------------------------
 * LeadCard
 * ------------------------------------------------------------------------- */

/** A label over its value; the property side has the height for it. */
function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <dt className="text-[11px] text-gray-500">{label}</dt>
      <dd className="text-[14.5px] font-semibold leading-6 text-gray-900">{children}</dd>
    </div>
  );
}

export function LeadCard({
  lead,
  selected = false,
  onSelectedChange,
  onMore,
  onAddressClick,
  collapsedContactLimit = { phones: 3, emails: 2 },
  className,
}: LeadCardProps) {
  const checkboxId = React.useId();
  const limit = collapsedContactLimit;

  const cityLine = [lead.city, [lead.state, lead.zip].filter(Boolean).join(" ")]
    .filter(Boolean)
    .join(", ");
  const processed = formatDate(lead.dateProcessed);
  const statusClass =
    (lead.listingStatus && STATUS_STYLES[lead.listingStatus.toLowerCase()]) || "bg-gray-100 text-gray-700";

  const homeFacts = [
    lead.beds != null && { key: "beds", icon: <BedIcon />, tone: "text-sky-600", text: `${count.format(lead.beds)} ${lead.beds === 1 ? "Bed" : "Beds"}` },
    lead.baths != null && { key: "baths", icon: <BathIcon />, tone: "text-cyan-600", text: `${count.format(lead.baths)} ${lead.baths === 1 ? "Bath" : "Baths"}` },
    lead.livingAreaSqft != null && { key: "area", icon: <AreaIcon />, tone: "text-amber-600", text: `${count.format(lead.livingAreaSqft)} Sq ft` },
    lead.homeType && { key: "type", icon: <HomeIcon />, tone: "text-violet-600", text: lead.homeType },
  ].filter(Boolean) as { key: string; icon: React.ReactNode; tone: string; text: string }[];

  return (
    <article
      aria-labelledby={`${checkboxId}-title`}
      className={cx(
        "flex flex-col overflow-hidden rounded-xl border bg-white transition-colors duration-150",
        selected ? "border-(--blue)/40 bg-(--accent-soft)/40" : "border-gray-200 hover:border-gray-300",
        className
      )}
    >
      {/* Body */}
      <div className="flex flex-wrap gap-x-6 gap-y-3 px-4 py-3">
        {/* Property */}
        <div className="flex min-w-0 flex-[1_1_300px] gap-2.5">
          <div className="pt-[3px]">
            <input
              id={checkboxId}
              type="checkbox"
              checked={selected}
              onChange={(e) => onSelectedChange?.(lead.id, e.target.checked)}
              aria-label={`Select lead ${lead.street}`}
              className="h-4 w-4 cursor-pointer rounded border-gray-300 accent-(--blue) focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--blue)"
            />
          </div>

          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
              <h3 id={`${checkboxId}-title`} className="text-[14.5px] font-semibold leading-5 text-gray-900">
                {onAddressClick ? (
                  <button
                    type="button"
                    onClick={() => onAddressClick(lead)}
                    title={lead.mapped === false ? "Map location pending" : "Show on map"}
                    className="cursor-pointer rounded text-left underline-offset-4 transition-colors duration-150 hover:text-(--blue) hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--blue)"
                  >
                    {lead.street}
                  </button>
                ) : (
                  lead.street
                )}
              </h3>
              {lead.primarySignal && (
                <span
                  className={cx(
                    "flex items-center gap-1 rounded-full border px-1.5 text-[11px] font-semibold leading-[18px]",
                    SIGNAL_CHIP
                  )}
                >
                  <SparkIcon />
                  {lead.primarySignal.label}
                </span>
              )}
              {lead.listingStatus &&
                lead.listingStatus.toLowerCase() !== lead.primarySignal?.label.toLowerCase() && (
                <span className={cx("rounded-full px-1.5 text-[11px] font-medium leading-[18px]", statusClass)}>
                  {lead.listingStatus}
                </span>
              )}
            </div>
            {cityLine && (
              <span className="flex items-center gap-1 text-[13px] text-gray-500">
                <PinIcon />
                {cityLine}
              </span>
            )}

            <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-2 tabular-nums">
              <div className="flex flex-col">
                <dt className="text-[11px] font-medium text-emerald-700">Est. value</dt>
                <dd className="text-[17px] font-bold leading-6 text-emerald-700">{formatMoney(lead.estimatedValue)}</dd>
              </div>
              <Stat label="List price">{formatMoney(lead.listPrice)}</Stat>
              {lead.equity && (
                <Stat label="Equity">
                  <span className={EQUITY_STYLES[lead.equity]}>{lead.equity}</span>
                </Stat>
              )}
              {lead.mlsNumber && (
                <Stat label="MLS ID">
                  <span className="font-mono text-[13.5px] text-indigo-700">{lead.mlsNumber}</span>
                </Stat>
              )}
            </dl>

            {homeFacts.length > 0 && (
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {homeFacts.map((f) => (
                  <li
                    key={f.key}
                    className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2 py-0.5 text-[12.5px] text-gray-800"
                  >
                    <span className={f.tone}>{f.icon}</span>
                    {f.text}
                  </li>
                ))}
              </ul>
            )}

            {processed && (
              <span className="mt-1.5 flex items-center gap-1 text-xs text-gray-500">
                <CalendarIcon />
                Processed {processed}
              </span>
            )}
          </div>
        </div>

        {/* Owners & contacts */}
        <div className="min-w-0 flex-[1.6_1_420px] border-gray-100 sm:border-l sm:pl-4">
          {lead.owners.length === 0 ? (
            <span className="text-[12.5px] text-gray-400">No owner information</span>
          ) : (
            // Two unlined columns: one per person, or one person's numbers across both.
            <div className="grid gap-x-5 gap-y-2 sm:grid-cols-2">
              {lead.owners.map((owner, i) => (
                <div key={`${owner.name}-${i}`} className={cx("min-w-0", lead.owners.length === 1 && "sm:col-span-2")}>
                  <OwnerContacts
                    owner={owner}
                    index={i}
                    columns={lead.owners.length === 1 ? 2 : 1}
                    limit={limit}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Footer: why it stands out, then actions */}
      <div className="flex flex-wrap items-center justify-between gap-x-2.5 gap-y-1 border-t border-gray-100 bg-gray-50/60 py-1.5 pl-4 pr-2.5 sm:pl-[42px]">
        <ul className="flex min-w-0 flex-wrap items-center gap-1" aria-label="Why this lead stands out">
          {(lead.signals ?? []).map((s) => (
            <li
              key={s.key}
              className={cx("rounded-full border px-1.5 text-[11px] font-medium leading-[18px]", SIGNAL_CHIP)}
            >
              {s.label}
            </li>
          ))}
        </ul>

        <div className="ml-auto flex items-center">
          <button
            type="button"
            aria-label="More actions"
            onClick={(e) => onMore?.(lead, e.currentTarget)}
            className="flex h-7 w-7 items-center justify-center rounded-md text-gray-600 transition-colors duration-150 hover:bg-(--accent-soft) hover:text-(--blue-dark) focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--blue)"
          >
            <MoreIcon />
          </button>
        </div>
      </div>
    </article>
  );
}

/* ----------------------------------------------------------------------------
 * Loading skeleton with the same footprint as the card
 * ------------------------------------------------------------------------- */

export function LeadCardSkeleton() {
  return (
    <div className="animate-pulse rounded-xl border border-gray-200 bg-white" aria-hidden>
      <div className="flex flex-wrap gap-x-6 gap-y-3 px-4 py-3">
        <div className="flex flex-[1_1_300px] gap-2.5">
          <div className="h-4 w-4 rounded bg-gray-200" />
          <div className="flex flex-1 flex-col gap-1.5">
            <div className="h-4 w-2/3 rounded bg-gray-200" />
            <div className="h-4 w-1/2 rounded bg-gray-100" />
            <div className="h-3 w-3/4 rounded bg-gray-100" />
          </div>
        </div>
        <div className="flex flex-[1.6_1_420px] flex-col gap-1.5 sm:border-l sm:border-gray-100 sm:pl-4">
          <div className="h-4 w-48 rounded bg-gray-200" />
          <div className="h-3 w-40 rounded bg-gray-100" />
          <div className="h-3 w-44 rounded bg-gray-100" />
          <div className="h-3 w-36 rounded bg-gray-100" />
        </div>
      </div>
      <div className="h-[38px] border-t border-gray-100 bg-gray-50/60" />
    </div>
  );
}

export default LeadCard;
