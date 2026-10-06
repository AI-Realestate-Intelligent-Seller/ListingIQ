"use client";

import { useEffect, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";

import { fetchLead } from "../api/leads-api";
import type { LeadDetail, LeadStage } from "../types/leads.types";

const PropertySatelliteMap = dynamic(
  () => import("./property-satellite-map").then((module) => module.PropertySatelliteMap),
  { ssr: false, loading: () => <div className="leads-property-map-loading">Loading satellite map…</div> },
);

const STAGE_CLASS: Record<LeadStage, string> = {
  ready: "ready",
  in_campaign: "campaign",
  needs_review: "review",
  dnc: "dnc",
};

const LEAD_STATUS_LABELS: Record<string, string> = {
  processing: "In progress",
  want_more_info: "Wants info",
  interested: "Interested",
  ready_to_sell: "Ready to sell",
  location_discussion: "Location discussion",
  not_interested: "Not interested",
  no_response: "No response",
  dnc: "Do not contact",
};

/** "listing_price" -> "Listing price"; the vendor's own keys stay recognisable. */
function attributeLabel(key: string): string {
  const acronyms: Record<string, string> = {
    akas: "Aliases",
    dnc: "DNC",
    dob: "DOB",
    id: "ID",
    tcpa: "TCPA",
  };
  const known = acronyms[key.toLowerCase()];
  if (known) return known;
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Imported CSV facts are usually strings, while provider-distributed leads
 * can contain booleans, arrays and nested objects. Convert every JSON value to
 * readable text before it reaches JSX so React never receives a raw object.
 */
function scalarValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number" || typeof value === "bigint") {
    return String(value);
  }
  if (typeof value === "string") {
    // Trailing ".0" on every number is spreadsheet noise, not data.
    return /^-?\d+\.0$/.test(value) ? value.slice(0, -2) : value;
  }
  return String(value);
}

function attributeValue(value: unknown): ReactNode {
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    return (
      <div className="leads-detail-array">
        {value.map((item, index) => {
          const isGroup = item !== null && typeof item === "object";
          return isGroup ? (
            <details className="leads-detail-nested-group leads-detail-array-item" key={index}>
              <summary>Record {index + 1}</summary>
              <div className="leads-detail-group-content">{attributeValue(item)}</div>
            </details>
          ) : (
            <div className="leads-detail-array-item" key={index}>{attributeValue(item)}</div>
          );
        })}
      </div>
    );
  }
  if (value !== null && typeof value === "object") {
    const fields = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== false);
    if (fields.length === 0) return "—";
    return (
      <dl className="leads-detail-nested">
        {fields.map(([key, item]) => {
          const isGroup = item !== null && typeof item === "object";
          return isGroup ? (
            <div className="leads-detail-nested-row" key={key}>
              <details className="leads-detail-nested-group">
                <summary>{attributeLabel(key)}</summary>
                <div className="leads-detail-group-content">{attributeValue(item)}</div>
              </details>
            </div>
          ) : (
            <div key={key}>
              <dt>{attributeLabel(key)}</dt>
              <dd>{attributeValue(item)}</dd>
            </div>
          );
        })}
      </dl>
    );
  }
  return scalarValue(value);
}

type DetailSectionProps = {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  fullWidth?: boolean;
  summaryValue?: ReactNode;
};

function DetailSection({
  title,
  children,
  defaultOpen = false,
  fullWidth = false,
  summaryValue,
}: DetailSectionProps) {
  return (
    <details
      className={`leads-drawer-section leads-detail-section${fullWidth ? " leads-detail-full" : ""}`}
      open={defaultOpen}
    >
      <summary>
        <span>{title}</span>
        {summaryValue ? <span className="leads-detail-summary-value">{summaryValue}</span> : null}
      </summary>
      <div className="leads-detail-section-content">{children}</div>
    </details>
  );
}

function formatMoment(value: string | null): string {
  if (!value) return "—";
  const stamp = new Date(/[Z+]/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(stamp.getTime())) return "—";
  return stamp.toLocaleString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function objectValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function formatDateOnly(value: unknown): string {
  if (typeof value !== "string" || !value) return "—";
  const isoDate = value.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
  if (isoDate) return isoDate;
  const stamp = new Date(value);
  return Number.isNaN(stamp.getTime()) ? "—" : stamp.toISOString().slice(0, 10);
}

function formatMoney(value: unknown): string {
  const amount = typeof value === "number" ? value : Number(value);
  return Number.isFinite(amount)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(amount)
    : "—";
}

function formatCount(value: unknown): string {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-US") : "—";
}

function formatPhone(value: unknown): string {
  const raw = typeof value === "string" ? value : "";
  const digits = raw.replace(/\D/g, "");
  const local = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
  return local.length === 10
    ? `(${local.slice(0, 3)}) ${local.slice(3, 6)}-${local.slice(6)}`
    : scalarValue(value);
}

function propertyType(value: unknown): string {
  return typeof value === "string" && value
    ? attributeLabel(value.toLowerCase().replaceAll("_", " "))
    : "Property";
}

function safeWebUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

const QUICK_LIST_LABELS: Record<string, string> = {
  activeAuction: "Active auction",
  activeListing: "Actively listed",
  cashBuyer: "Cash buyer",
  corporateOwned: "Corporate owned",
  expiredListing: "Expired listing",
  failedListing: "Failed listing",
  fixAndFlip: "Fix & flip",
  forSaleByOwner: "For sale by owner",
  freeAndClear: "Free & clear",
  highEquity: "High equity",
  inherited: "Inherited property",
  listedBelowMarketPrice: "Below market price",
  noticeOfDefault: "Notice of default",
  noticeOfLisPendens: "Lis pendens filed",
  noticeOfSale: "Notice of sale",
  onMarket: "On market",
  outOfStateOwner: "Out-of-state owner",
  ownerOccupied: "Owner occupied",
  pendingListing: "Pending listing",
  preforeclosure: "Pre-foreclosure",
  recentlySold: "Recently sold",
  taxDefault: "Tax default",
  tiredLandlord: "Tired landlord",
  trustOwned: "Trust owned",
  vacant: "Vacant",
  vacantLot: "Vacant lot",
};

function positiveQuickLists(value: unknown): { key: string; label: string }[] {
  return Object.entries(objectValue(value))
    .filter(([, enabled]) => enabled === true)
    .map(([key]) => ({ key, label: QUICK_LIST_LABELS[key] ?? attributeLabel(key) }));
}

function hasRealEstateProfile(details: Record<string, unknown>): boolean {
  const provider = objectValue(details.provider_property_details);
  const record = Object.keys(provider).length > 0 ? provider : details;
  return Object.keys(objectValue(record.listing)).length > 0
    || positiveQuickLists(record.quickLists).length > 0;
}

function PropertyMapSection({ latitude, longitude }: { latitude: number; longitude: number }) {
  return (
    <DetailSection title="Property location" defaultOpen fullWidth>
      <div className="leads-property-map">
        <PropertySatelliteMap latitude={latitude} longitude={longitude} />
      </div>
      <p className="leads-map-coordinates">{latitude.toFixed(6)}, {longitude.toFixed(6)} · Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community</p>
    </DetailSection>
  );
}

export function RealEstateProfile({ details, latitude, longitude, leadSignals }: {
  details: Record<string, unknown>;
  latitude: number | null;
  longitude: number | null;
  leadSignals: { key: string; label: string }[];
}) {
  const provider = objectValue(details.provider_property_details);
  const record = Object.keys(provider).length > 0 ? provider : details;
  const address = objectValue(record.address);
  const listing = objectValue(record.listing);
  const owner = objectValue(record.owner);
  const mailingAddress = objectValue(owner.mailingAddress);
  const ids = objectValue(record.ids);
  const brokerage = objectValue(listing.brokerage);
  const agents = arrayValue(listing.agents).map(objectValue);
  const offices = arrayValue(listing.agentOffices).map(objectValue);
  const taxes = arrayValue(listing.taxes).map(objectValue).filter((row) => row.year && row.amount != null);
  const opportunities = [...leadSignals, ...positiveQuickLists(record.quickLists)]
    .filter((signal, index, rows) => rows.findIndex((row) => row.label.toLowerCase() === signal.label.toLowerCase()) === index);
  const listingUrl = safeWebUrl(listing.listingUrl);
  const mapLatitude = latitude ?? (typeof address.latitude === "number" ? address.latitude : null);
  const mapLongitude = longitude ?? (typeof address.longitude === "number" ? address.longitude : null);
  const hasProfile = Object.keys(listing).length > 0 || opportunities.length > 0;
  if (!hasProfile) {
    return mapLatitude !== null && mapLongitude !== null
      ? <PropertyMapSection latitude={mapLatitude} longitude={mapLongitude} />
      : null;
  }
  const currentPrice = Number(listing.price);
  const maximumPrice = Number(listing.maxListPrice);
  const priceReduction = Number.isFinite(currentPrice) && Number.isFinite(maximumPrice) && maximumPrice > currentPrice
    ? maximumPrice - currentPrice
    : null;
  const ownerMailing = [mailingAddress.street, mailingAddress.city, mailingAddress.state, mailingAddress.zip]
    .filter(Boolean).join(", ");
  const facts: [string, ReactNode][] = [
    ["Property type", propertyType(listing.propertyType ?? details.property_type)],
    ["Bedrooms", formatCount(listing.bedroomCount ?? details.beds)],
    ["Bathrooms", formatCount(listing.bathroomCount ?? details.baths)],
    ["Living area", listing.livingArea ? `${formatCount(listing.livingArea)} sq ft` : "—"],
    ["Lot size", listing.lotSizeSquareFeet ? `${formatCount(listing.lotSizeSquareFeet)} sq ft` : "—"],
    ["Year built", scalarValue(listing.yearBuilt)],
    ["Construction", scalarValue(listing.exteriorConstruction)],
    ["Heating", arrayValue(listing.heatingTypes).map(scalarValue).join(", ") || "—"],
    ["Roof", arrayValue(listing.roofTypes).map(scalarValue).join(", ") || "—"],
    ["Outdoor space", scalarValue(listing.patio)],
    ["Parking spaces", scalarValue(listing.parkingSpaceCount)],
    ["APN", scalarValue(ids.apn)],
    ["Owner mailing", ownerMailing || "—"],
  ];

  return (
    <>
      <section className="leads-property-snapshot leads-detail-full" aria-label="Property snapshot">
        <div>
          <span className={`leads-listing-status ${String(listing.statusCategory ?? listing.status ?? "unknown").toLowerCase()}`}>
            {scalarValue(listing.statusCategory ?? listing.status ?? "Status unavailable")}
          </span>
          <h4>{propertyType(listing.propertyType ?? details.property_type)}</h4>
          <p>{[address.city, address.state, address.zip].filter(Boolean).join(", ") || "Location not on file"}</p>
        </div>
        <div className="leads-snapshot-price"><span>Current asking price</span><strong>{formatMoney(listing.price ?? details.listing_price)}</strong>{priceReduction ? <small>{formatMoney(priceReduction)} below the recorded high</small> : null}</div>
        <div className="leads-snapshot-stat"><strong>{formatCount(listing.bedroomCount ?? details.beds)}</strong><span>Beds</span></div>
        <div className="leads-snapshot-stat"><strong>{formatCount(listing.bathroomCount ?? details.baths)}</strong><span>Baths</span></div>
        <div className="leads-snapshot-stat"><strong>{formatCount(listing.livingArea)}</strong><span>Sq ft</span></div>
        <div className="leads-snapshot-stat"><strong>{formatCount(listing.daysOnMarket)}</strong><span>Days listed</span></div>
      </section>

      {opportunities.length > 0 ? (
        <DetailSection title="Why this lead stands out" defaultOpen fullWidth>
          <div className="leads-opportunity-signals">
            {opportunities.map((signal) => <span key={signal.key}>{signal.label}</span>)}
          </div>
        </DetailSection>
      ) : null}

      <DetailSection title="Listing & price history" defaultOpen fullWidth>
        <div className="leads-data-table-wrap">
          <table className="leads-data-table"><thead><tr><th>Milestone</th><th>Date</th><th>Price</th><th>Details</th></tr></thead><tbody>
            <tr><td>Current listing</td><td>{formatDateOnly(listing.originalListingDate)}</td><td>{formatMoney(listing.price)}</td><td>{scalarValue(listing.status)} · {formatCount(listing.daysOnMarket)} days</td></tr>
            <tr><td>Highest list price</td><td>{formatDateOnly(listing.maxListPriceDate)}</td><td>{formatMoney(listing.maxListPrice)}</td><td>Recorded maximum</td></tr>
            <tr><td>Lowest list price</td><td>{formatDateOnly(listing.minListPriceDate)}</td><td>{formatMoney(listing.minListPrice)}</td><td>Recorded minimum</td></tr>
            <tr><td>Last sale</td><td>{formatDateOnly(listing.soldDate)}</td><td>{formatMoney(listing.soldPrice)}</td><td>{listing.salePriceIsEstimated ? "Estimated" : "Recorded sale"}</td></tr>
          </tbody></table>
        </div>
      </DetailSection>

      <DetailSection title="Home facts" defaultOpen fullWidth>
        <dl className="leads-property-facts">
          {facts.filter(([, value]) => value !== "—").map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>
      </DetailSection>

      {(agents.length > 0 || Object.keys(brokerage).length > 0 || offices.length > 0) ? (
        <DetailSection title="Listing contacts" fullWidth>
          <div className="leads-data-table-wrap"><table className="leads-data-table"><thead><tr><th>Name</th><th>Role</th><th>Phone / office</th></tr></thead><tbody>
            {agents.map((agent, index) => <tr key={`agent-${index}`}><td>{scalarValue(agent.name)}</td><td>{arrayValue(agent.roles).map(scalarValue).join(", ") || "Agent"}</td><td>{formatPhone(agent.primaryPhoneNumber)}</td></tr>)}
            {Object.keys(brokerage).length > 0 ? <tr><td>{scalarValue(brokerage.name)}</td><td>Brokerage</td><td>{formatPhone(brokerage.phoneNumber)}</td></tr> : null}
            {offices.map((office, index) => <tr key={`office-${index}`}><td>{scalarValue(office.name)}</td><td>Office</td><td>{scalarValue(office.officeAddress)}</td></tr>)}
          </tbody></table></div>
        </DetailSection>
      ) : null}

      {taxes.length > 0 ? (
        <DetailSection title="Property tax history" fullWidth>
          <div className="leads-data-table-wrap"><table className="leads-data-table compact"><thead><tr><th>Year</th><th>Tax amount</th></tr></thead><tbody>
            {taxes.map((tax, index) => <tr key={`${tax.year}-${index}`}><td>{scalarValue(tax.year)}</td><td>{formatMoney(tax.amount)}</td></tr>)}
          </tbody></table></div>
        </DetailSection>
      ) : null}

      {mapLatitude !== null && mapLongitude !== null
        ? <PropertyMapSection latitude={mapLatitude} longitude={mapLongitude} />
        : null}

      {listingUrl ? (
        <DetailSection title="Live listing" fullWidth>
          <div className="leads-listing-preview">
            <div><span>Listing source</span><strong>{new URL(listingUrl).hostname.replace(/^www\./, "")}</strong></div>
            <a href={listingUrl} target="_blank" rel="noreferrer">Open live listing ↗</a>
          </div>
        </DetailSection>
      ) : null}

    </>
  );
}

/** Fallback wording when the caller has no stage catalog of its own to pass. */
const DEFAULT_STAGE_LABELS: Record<string, string> = {
  ready: "Ready",
  in_campaign: "In campaign",
  needs_review: "Needs review",
  dnc: "Do not contact",
};

type LeadDetailDrawerProps = {
  /** The lead to show, or null when the drawer is closed. */
  leadId: number | null;
  accessToken: string;
  /** From the pool's stage catalog. Omitted outside the pool, where the
      built-in wording is enough. */
  stageLabels?: Record<string, string>;
  onClose: () => void;
  /** Omit where the caller is already the place threads are read: the panel
      then shows the property alone, with no action that leads back to itself. */
  onOpenConversation?: (conversationId: number) => void;
  /** Drafts a one-lead campaign and opens it in the Campaigns tab to write. */
  onStartCampaign?: (leadId: number) => void;
  onRemove?: (lead: LeadDetail) => void;
};

/**
 * A large in-page modal with everything known about one property and owner.
 * It leaves the underlying workspace in place and owns its own scrolling.
 */
export function LeadDetailDrawer({
  leadId,
  accessToken,
  stageLabels = DEFAULT_STAGE_LABELS,
  onClose,
  onOpenConversation,
  onStartCampaign,
  onRemove,
}: LeadDetailDrawerProps) {
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    if (leadId === null) return;
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset request state when the selected lead changes
    setIsLoading(true);
    setErrorMessage("");
    fetchLead(leadId, accessToken, controller.signal)
      .then((detail) => setLead(detail))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setErrorMessage(error instanceof Error ? error.message : "Could not load the lead.");
      })
      .finally(() => setIsLoading(false));
    return () => controller.abort();
  }, [leadId, accessToken]);

  // Escape closes it, as with the other overlays.
  useEffect(() => {
    if (leadId === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [leadId, onClose]);

  useEffect(() => {
    if (leadId === null) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [leadId]);

  const isOpen = leadId !== null;
  // Keep the previous lead mounted while a new one loads so the panel never blinks.
  const shown = lead && lead.id === leadId ? lead : null;

  return (
    <>
      <div
        className={`leads-drawer-scrim${isOpen ? " open" : ""}`}
        role="presentation"
        onClick={onClose}
      />
      <aside
        className={`leads-drawer leads-detail-modal${isOpen ? " open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="Lead details"
        aria-hidden={!isOpen}
      >
        <header className="leads-drawer-header">
          <div>
            <span className="sms-eyebrow">REAL ESTATE LEAD BRIEF</span>
            <h3>{shown?.property_address || shown?.owner_name || shown?.phone || "Lead"}</h3>
            {shown?.owner_name ? <p className="leads-drawer-owner">Owner: {shown.owner_name}</p> : null}
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close details">
            ✕
          </button>
        </header>

        <div className="leads-drawer-body">
          {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}
          {isLoading && !shown ? <p className="sms-muted">Loading…</p> : null}

          {shown ? (
            <>
              <DetailSection title="Property" defaultOpen>
                <dl className="leads-facts">
                  <div>
                    <dt>Address</dt>
                    <dd>{shown.property_address || "Not on file"}</dd>
                  </div>
                  <div>
                    <dt>Area</dt>
                    <dd>{shown.area || "Not on file"}</dd>
                  </div>
                </dl>
              </DetailSection>

              <DetailSection title="Ownership & contacts" defaultOpen>
                <dl className="leads-facts">
                  <div>
                    <dt>Property owners</dt>
                    <dd>{shown.owner_name || "Not on file"}</dd>
                  </div>
                  <div>
                    <dt>Contacts</dt>
                    <dd className="leads-owner-contacts">
                      {shown.phone_numbers.length > 0 ? shown.phone_numbers.map((contact) => (
                        <span className="leads-owner-contact" key={contact.phone}>
                          <span>{contact.owner_name || shown.owner_name || "Owner not identified"}</span>
                          {contact.dnc ? (
                            <span>{contact.phone} <strong>DNC</strong></span>
                          ) : (
                            <a href={`tel:${contact.phone}`}>{formatPhone(contact.phone)}</a>
                          )}
                        </span>
                      )) : "Not on file"}
                    </dd>
                  </div>
                </dl>
              </DetailSection>

              <RealEstateProfile details={shown.details} latitude={shown.latitude} longitude={shown.longitude} leadSignals={shown.signals} />

              {Object.keys(shown.details).length > 0 && !hasRealEstateProfile(shown.details) ? (
                <DetailSection title="Property details" fullWidth>
                  <dl className="leads-facts">
                    {Object.entries(shown.details).map(([key, value]) => (
                      <div key={key}>
                        <dt>{attributeLabel(key)}</dt>
                        <dd>{attributeValue(value)}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="leads-drawer-note">
                    From the provider/import. Raw contact and provider evidence is for broker review;
                    Bobbie is grounded only in the approved property facts.
                  </p>
                </DetailSection>
              ) : null}

              {!hasRealEstateProfile(shown.details) ? (
                <DetailSection title="Signals" defaultOpen>
                  {shown.signals.length === 0 ? (
                    <p className="leads-drawer-note">No signals on this record.</p>
                  ) : (
                    <div className="leads-signals">
                      {shown.signals.map((signal) => (
                        <span key={signal.key} className="leads-signal">{signal.label}</span>
                      ))}
                    </div>
                  )}
                </DetailSection>
              ) : null}

              {shown.outreach_reason ? (
                <DetailSection title="Outreach reason">
                  <p className="leads-reason">{shown.outreach_reason}</p>
                  <p className="leads-drawer-note">
                    Came with the lead. Bobbie opens with this rather than a generic line.
                  </p>
                </DetailSection>
              ) : null}

              <DetailSection
                title="Score"
                summaryValue={(
                  <span className={`leads-score ${shown.score >= 80 ? "strong" : shown.score >= 70 ? "fair" : "plain"}`}>
                    {shown.score}
                  </span>
                )}
              >
                <ul className="leads-score-breakdown">
                  {shown.score_breakdown.map((part) => (
                    <li key={part.label}>
                      <span>{part.label}</span>
                      <span>+{part.points}</span>
                    </li>
                  ))}
                </ul>
                <p className="leads-drawer-note">
                  A transparent sum of signals and contactability — it orders your review
                  queue, it does not predict a sale.
                </p>
              </DetailSection>

              <DetailSection title="Status" defaultOpen>
                <dl className="leads-facts">
                  <div>
                    <dt>Stage</dt>
                    <dd>
                      <span className={`leads-stage ${STAGE_CLASS[shown.stage]}`}>
                        {stageLabels[shown.stage] ?? shown.stage}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Last activity</dt>
                    <dd>{shown.last_activity_at ? formatMoment(shown.last_activity_at) : "Never contacted"}</dd>
                  </div>
                  <div>
                    <dt>Added</dt>
                    <dd>{formatMoment(shown.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Last refreshed</dt>
                    <dd>{formatMoment(shown.refreshed_at)}</dd>
                  </div>
                </dl>
              </DetailSection>

              {shown.conversation ? (
                <DetailSection title="Conversation">
                  <dl className="leads-facts">
                    <div>
                      <dt>Handled by</dt>
                      <dd>{shown.conversation.handled_by === "bobbie" ? "✦ Bobbie" : "You"}</dd>
                    </div>
                    <div>
                      <dt>Lead status</dt>
                      <dd>
                        {LEAD_STATUS_LABELS[shown.conversation.lead_status] ??
                          shown.conversation.lead_status}
                      </dd>
                    </div>
                    <div>
                      <dt>Messages</dt>
                      <dd>{shown.conversation.message_count}</dd>
                    </div>
                    {shown.conversation.meeting_booked ? (
                      <div>
                        <dt>Meeting</dt>
                        <dd>Booked</dd>
                      </div>
                    ) : null}
                  </dl>
                  {shown.conversation.latest_message ? (
                    <blockquote className="leads-last-message">
                      {shown.conversation.latest_message}
                      <cite>{formatMoment(shown.conversation.latest_message_at)}</cite>
                    </blockquote>
                  ) : null}
                </DetailSection>
              ) : null}
            </>
          ) : null}
        </div>

        {shown && (onOpenConversation || onStartCampaign || onRemove) ? (
          <footer className="leads-drawer-actions">
            {shown.conversation ? (
              onOpenConversation ? (
                <button
                  className="button"
                  type="button"
                  onClick={() => onOpenConversation(shown.conversation!.id)}
                >
                  Open conversation
                </button>
              ) : null
            ) : onStartCampaign ? (
              <button
                className="button"
                type="button"
                onClick={() => onStartCampaign(shown.id)}
                disabled={shown.stage === "dnc" || !shown.phone}
                title={
                  shown.stage === "dnc"
                    ? "This owner is on the do-not-contact list."
                    : !shown.phone
                      ? "No phone number to text."
                      : undefined
                }
              >
                ✦ Create campaign
              </button>
            ) : null}
            {onRemove ? (
              <button type="button" className="sms-button-secondary" onClick={() => onRemove(shown)}>
                Remove
              </button>
            ) : null}
          </footer>
        ) : null}
      </aside>
    </>
  );
}
