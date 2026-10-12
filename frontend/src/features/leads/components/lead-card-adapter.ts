import type { Lead as PoolLead } from "../types/leads.types";
import type { Lead as LeadCardLead, LeadOwner, LeadSignalChip } from "./lead-card";
import { parseAddress } from "./lead-filterComponents";
import { opportunityLabel } from "./opportunity-signals";

/** Used only when the provider sent no listing status of its own. */
const LISTING_STATUS_SIGNALS: [key: string, status: string][] = [
  ["expired", "Expired"],
  ["cancelled_mls_listing", "Canceled"],
  ["withdrawn", "Withdrawn"],
  ["pending", "Pending"],
];

function digitsOf(value: string): string {
  return value.replace(/\D/g, "");
}

/** Same display rule as the details drawer: US numbers as (312) 555-0144. */
function formatPhone(value: string): string {
  const digits = digitsOf(value);
  const local = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
  return local.length === 10
    ? `(${local.slice(0, 3)}) ${local.slice(3, 6)}-${local.slice(6)}`
    : value;
}

/** Prices and counts arrive as numbers or numeric strings; anything else is "none". */
function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Equity exactly as BatchData classifies it (its highEquity / lowEquity flags).
 * BatchData has no middle level, and "unknownEquity" leaves the field out.
 */
function equityLevel(lead: PoolLead): "High" | "Low" | undefined {
  const flags = lead.opportunity_flags ?? [];
  if (flags.includes("highEquity")) return "High";
  if (flags.includes("lowEquity")) return "Low";
  return undefined;
}

/** "MULTI_FAMILY" → "Multi family"; "Apartments" stays as it is. */
function homeTypeLabel(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  if (!/^[A-Z0-9_ ]+$/.test(value)) return value;
  const words = value.toLowerCase().replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The pool row's address is one string: "123 Main St, Chicago, IL, 60601". */
function splitAddress(address: string | null) {
  const { city, state, zip } = parseAddress(address);
  const street = address?.split(",")[0]?.trim();
  return {
    street: street || "No address on file",
    city: city || undefined,
    state: state || undefined,
    zip: zip || undefined,
  };
}

/**
 * One block per person: the lead's owner first, then every other name the
 * skip-traced phones and emails carry, in the order they appear. Numbers and
 * emails with no name belong to the owner. Other names are skip-trace
 * contacts — the provider does not say they co-own the property.
 */
function groupOwners(lead: PoolLead): LeadOwner[] {
  const primaryName = lead.owner_name?.trim() || "";
  const primaryKey = primaryName.toLowerCase();
  const primary: LeadOwner = { name: primaryName || "Unnamed owner", role: "Owner", phones: [], emails: [] };
  const contacts = new Map<string, LeadOwner>();

  const ownerFor = (rawName: string | undefined): LeadOwner => {
    const name = rawName?.trim() || "";
    const key = name.toLowerCase();
    if (!name || key === primaryKey) return primary;
    const owner = contacts.get(key) ?? { name, role: "Contact", phones: [], emails: [] };
    contacts.set(key, owner);
    return owner;
  };

  // The headline phone is normally one of phone_numbers; if not, its DNC state is unknown.
  const phones: { phone: string; dnc?: boolean; owner_name?: string; type?: string }[] = [...lead.phone_numbers];
  const headline = lead.phone;
  if (headline && !phones.some((item) => digitsOf(item.phone) === digitsOf(headline))) {
    phones.unshift({ phone: headline });
  }
  for (const item of phones) {
    ownerFor(item.owner_name).phones.push({ number: formatPhone(item.phone), type: item.type, dnc: item.dnc });
  }
  for (const item of lead.emails ?? []) {
    ownerFor(item.owner_name).emails.push(item.email);
  }

  const hasPrimary = primaryName || primary.phones.length > 0 || primary.emails.length > 0;
  return hasPrimary ? [primary, ...contacts.values()] : [...contacts.values()];
}

/**
 * Everything "Why this lead stands out" shows: the lead's own signals, then
 * the provider's flags, one chip per label (Pre-Foreclosure = Pre-foreclosure).
 */
function standoutSignals(lead: PoolLead): LeadSignalChip[] {
  const chips: LeadSignalChip[] = [
    ...lead.signals.map((signal) => ({ key: signal.key, label: signal.label })),
    ...(lead.opportunity_flags ?? []).map((key) => ({ key, label: opportunityLabel(key) })),
  ];
  return chips.filter(
    (chip, index) => chips.findIndex((other) => other.label.toLowerCase() === chip.label.toLowerCase()) === index,
  );
}

/** Maps a Lead Pool row onto the card's Lead. Fields the pool lacks stay undefined. */
export function toLeadCardLead(lead: PoolLead): LeadCardLead {
  const signalKeys = new Set(lead.signals.map((signal) => signal.key));
  const signalStatus = LISTING_STATUS_SIGNALS.find(([key]) => signalKeys.has(key))?.[1];
  const primary = lead.signals[0];

  return {
    id: String(lead.id),
    ...splitAddress(lead.property_address),
    // The first signal is the category the lead was fetched or imported for.
    primarySignal: primary ? { key: primary.key, label: primary.label } : undefined,
    listingStatus: lead.listing_status || signalStatus,
    listPrice: toNumber(lead.listing_price),
    estimatedValue: toNumber(lead.estimated_value),
    equity: equityLevel(lead),
    mlsNumber: lead.mls_number || undefined,
    beds: toNumber(lead.beds),
    baths: toNumber(lead.baths),
    livingAreaSqft: toNumber(lead.living_area_sqft),
    homeType: homeTypeLabel(lead.home_type),
    dateProcessed: lead.created_at,
    mapped: lead.latitude != null && lead.longitude != null,
    signals: standoutSignals(lead),
    owners: groupOwners(lead),
  };
}
