"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { LeadDetailDrawer } from "@/features/leads/components/lead-detail-drawer";
import { Toast, type ToastTone } from "@/components/toast/toast";

import type {
  CampaignDetail,
  CampaignFilters,
  CampaignReasonSuggestions,
  CampaignSelection,
  CampaignSendResult,
} from "../types/campaigns.types";
import {
  fetchCampaign,
  removeCampaignRecipients,
  sendCampaign,
  suggestReasons,
  updateCampaign,
} from "../api/campaigns-api";

type CampaignComposerProps = {
  campaign: CampaignDetail;
  accessToken: string;
  /** Replaces the draft in the parent as the server re-renders the preview. */
  onChange: (campaign: CampaignDetail) => void;
  onSent: (result: CampaignSendResult) => void;
  onDiscard: () => void;
  onBack: () => void;
  /** Opens one recipient's thread wherever threads are read. */
  onOpenConversation: (conversationId: number) => void;
};

/** Placeholders are inserted at the cursor, so nobody has to type the braces. */
const TOKEN_ORDER = [
  "first_name",
  "owner_name",
  "address",
  "area",
  "reason",
  "brokerage",
  "agent_name",
];

function displaySkipReason(reason: string): string {
  const normalized = reason.toLowerCase().replaceAll(/[-_]/g, " ");
  return normalized.includes("landline") || normalized.includes("fixed line")
    ? "Landline"
    : reason;
}

/**
 * Writes one campaign: its name, its message, and the per-recipient preview of
 * what that message becomes.
 *
 * The preview is rendered by the server rather than in the browser. The same
 * code then does the sending, so each visible preview page exactly matches the
 * stored template — a second, client-side renderer would be a second chance to
 * disagree.
 */
export function CampaignComposer({
  campaign,
  accessToken,
  onChange,
  onSent,
  onDiscard,
  onBack,
  onOpenConversation,
}: CampaignComposerProps) {
  const [name, setName] = useState(campaign.name);
  const [template, setTemplate] = useState(campaign.message_template);
  const [reason, setReason] = useState(campaign.outreach_reason);
  const [ideas, setIdeas] = useState<CampaignReasonSuggestions | null>(null);
  const [isSuggesting, setIsSuggesting] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isLoadingPage, setIsLoadingPage] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [filters, setFilters] = useState<CampaignFilters>({});
  const [filterDraft, setFilterDraft] = useState<CampaignFilters>({});
  const [showFilters, setShowFilters] = useState(false);
  const [filterAction, setFilterAction] = useState<"apply" | "clear" | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectAllFiltered, setSelectAllFiltered] = useState(false);
  const [isBulkSaving, setIsBulkSaving] = useState(false);
  const [isRemovingUnresolved, setIsRemovingUnresolved] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null);
  const [reasonSignalSearch, setReasonSignalSearch] = useState("");
  const [groupReasonEdits, setGroupReasonEdits] = useState<Record<string, string>>({});
  const [groupSuggestionResults, setGroupSuggestionResults] = useState<Record<string, CampaignReasonSuggestions>>({});
  const [suggestingGroup, setSuggestingGroup] = useState<string | null>(null);
  const [savingGroup, setSavingGroup] = useState<string | null>(null);
  /** The recipient whose property details are open, as in the Lead Pool. */
  const [detailId, setDetailId] = useState<number | null>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);

  const isSendingCampaign = campaign.status === "sending";
  const isSent = campaign.status === "sent";
  const isReadOnly = isSendingCampaign || isSent;
  const preview = campaign.preview;
  const recipients = preview.recipients;
  const recipientPages = preview.total_pages;
  const currentRecipientPage = preview.page;
  const dirtyGroups = Object.keys(groupReasonEdits);

  // Re-render the visible preview page a beat after typing stops rather than
  // on every key; the stored template remains campaign-wide.
  useEffect(() => {
    if (isReadOnly) return;
    if (
      name === campaign.name &&
      template === campaign.message_template &&
      reason === campaign.outreach_reason
    ) {
      return;
    }

    const timer = setTimeout(() => {
      setIsSaving(true);
      setErrorMessage("");
      updateCampaign(
        campaign.id,
        { name, message_template: template, outreach_reason: reason },
        accessToken,
        currentRecipientPage,
      )
        .then(onChange)
        .catch((error: unknown) => {
          // A template the server rejects is normal while typing — it comes back
          // as a sentence, and Send stays disabled until it renders.
          setErrorMessage(error instanceof Error ? error.message : "Could not save the draft.");
        })
        .finally(() => setIsSaving(false));
    }, 600);
    return () => clearTimeout(timer);
  }, [
    name,
    template,
    reason,
    campaign.id,
    campaign.name,
    campaign.message_template,
    campaign.outreach_reason,
    accessToken,
    isReadOnly,
    onChange,
    currentRecipientPage,
  ]);

  async function loadRecipientPage(page: number): Promise<void> {
    if (isLoadingPage || page === currentRecipientPage) return;
    setIsLoadingPage(true);
    setErrorMessage("");
    try {
      onChange(await fetchCampaign(campaign.id, accessToken, undefined, page, filters));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not load that preview page.");
    } finally {
      setIsLoadingPage(false);
    }
  }

  async function applyFilters(
    next: CampaignFilters,
    action: "apply" | "clear" = "apply",
  ): Promise<void> {
    if (filterAction) return;
    setFilterAction(action);
    setIsLoadingPage(true);
    setErrorMessage("");
    try {
      const loaded = await fetchCampaign(campaign.id, accessToken, undefined, 1, next);
      setFilters(next);
      setSelectedIds([]);
      setSelectAllFiltered(false);
      setShowFilters(false);
      onChange(loaded);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not filter recipients.");
    } finally {
      setFilterAction(null);
      setIsLoadingPage(false);
    }
  }

  /** Asks the server what this set of leads has in common and how to say it. */
  async function fetchIdeas(): Promise<void> {
    if (isSuggesting) return;
    setIsSuggesting(true);
    setErrorMessage("");
    try {
      setIdeas(await suggestReasons(campaign.id, accessToken));
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Could not suggest a reason.",
      );
    } finally {
      setIsSuggesting(false);
    }
  }

  async function suggestGroupReason(signal: string): Promise<void> {
    if (suggestingGroup) return;
    setSuggestingGroup(signal);
    setErrorMessage("");
    try {
      const result = await suggestReasons(campaign.id, accessToken, signal);
      setGroupSuggestionResults((current) => ({ ...current, [signal]: result }));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not suggest a reason.");
    } finally {
      setSuggestingGroup(null);
    }
  }

  async function saveGroup(signal: string): Promise<void> {
    const persisted = preview.reason_groups.find((group) => group.key === signal)?.reason ?? "";
    const nextReason = (groupReasonEdits[signal] ?? persisted).trim();
    if (!nextReason || savingGroup) return;
    setSavingGroup(signal);
    setIsSaving(true);
    setErrorMessage("");
    try {
      const updated = await updateCampaign(
        campaign.id,
        { reason_group: { signal, reason: nextReason, confirmed: true } },
        accessToken,
        currentRecipientPage,
      );
      onChange(updated);
      setGroupReasonEdits((current) => {
        const next = { ...current };
        delete next[signal];
        return next;
      });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not save that reason.");
    } finally {
      setSavingGroup(null);
      setIsSaving(false);
    }
  }

  function selection(): CampaignSelection {
    return selectAllFiltered
      ? { mode: "filtered", filters }
      : { mode: "ids", lead_ids: selectedIds };
  }

  async function removeSelected(): Promise<void> {
    if (isBulkSaving || isRemovingUnresolved || (!selectAllFiltered && selectedIds.length === 0) ||
        !window.confirm("Return only the selected contacts to the Lead Pool?")) return;
    setIsBulkSaving(true);
    setErrorMessage("");
    try {
      const updated = await removeCampaignRecipients(
        campaign.id, selection(), accessToken, currentRecipientPage, filters,
      );
      onChange(updated.campaign);
      setSelectedIds([]);
      setSelectAllFiltered(false);
      setToast({
        tone: "success",
        message: `${updated.result.removed_contacts} recipients returned to Lead Pool. ${updated.result.remaining_contacts} recipients remain.`,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not remove those contacts.";
      setErrorMessage(message);
      setToast({ tone: "error", message });
    } finally {
      setIsBulkSaving(false);
    }
  }

  async function removeUnresolved(): Promise<void> {
    if (isRemovingUnresolved || isBulkSaving || preview.needs_review_count === 0 ||
        !window.confirm("Return every genuinely unresolved recipient to the Lead Pool?")) return;
    setIsRemovingUnresolved(true);
    setErrorMessage("");
    try {
      const updated = await removeCampaignRecipients(
        campaign.id, { mode: "unresolved" }, accessToken, currentRecipientPage, filters,
      );
      onChange(updated.campaign);
      setSelectedIds([]);
      setSelectAllFiltered(false);
      setToast({
        tone: "success",
        message: `${updated.result.removed_contacts} recipients returned to Lead Pool. ${updated.result.remaining_contacts} recipients remain.`,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not return unresolved recipients.";
      setErrorMessage(message);
      setToast({ tone: "error", message });
    } finally {
      setIsRemovingUnresolved(false);
    }
  }

  /** Drops `{{token}}` where the cursor is, or at the end if the box is unfocused. */
  function insertToken(token: string): void {
    const editor = editorRef.current;
    const snippet = `{{${token}}}`;
    if (!editor) {
      setTemplate((current) => `${current}${snippet}`);
      return;
    }
    const start = editor.selectionStart ?? template.length;
    const end = editor.selectionEnd ?? template.length;
    const next = `${template.slice(0, start)}${snippet}${template.slice(end)}`;
    setTemplate(next);
    // Put the caret after the inserted token so typing continues naturally.
    requestAnimationFrame(() => {
      editor.focus();
      editor.setSelectionRange(start + snippet.length, start + snippet.length);
    });
  }

  async function handleSend(): Promise<void> {
    if (isSending || isReadOnly) return;
    setIsSending(true);
    setErrorMessage("");
    try {
      onSent(await sendCampaign(campaign.id, accessToken));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not send the campaign.");
    } finally {
      setIsSending(false);
    }
  }

  const longCount = useMemo(
    () => recipients.filter((item) => item.is_long).length,
    [recipients],
  );
  // Edits that have not reached the server yet are not in the preview, and the
  // send uses the *stored* template — so Send waits for the draft to settle.
  // A rejected template never saves, which is exactly when this stays true.
  const isDirty =
    name !== campaign.name ||
    template !== campaign.message_template ||
    reason !== campaign.outreach_reason;
  const canSend =
    !isReadOnly && campaign.recipients > 0 && !preview.template_error &&
    preview.needs_review_count === 0 && dirtyGroups.length === 0 &&
    !isSaving && !isLoadingPage && !isDirty && !isBulkSaving &&
    !isRemovingUnresolved && !suggestingGroup && !savingGroup && !filterAction;

  const signalOptions = preview.signal_options;
  const visibleReasonGroups = useMemo(() => {
    const query = reasonSignalSearch.trim().toLowerCase();
    if (!query) return preview.reason_groups;
    return preview.reason_groups.filter((group) =>
      group.label.toLowerCase().includes(query) || group.key.toLowerCase().includes(query),
    );
  }, [preview.reason_groups, reasonSignalSearch]);

  return (
    <section className="campaign-composer">
      {toast ? (
        <Toast message={toast.message} tone={toast.tone} onDone={() => setToast(null)} />
      ) : null}
      <header className="campaigns-header">
        <div>
          <button type="button" className="campaigns-back" onClick={onBack}>
            ← All campaigns
          </button>
          <span className="sms-eyebrow">{isSendingCampaign ? "SENDING CAMPAIGN" : isSent ? "SENT CAMPAIGN" : "NEW CAMPAIGN"}</span>
          <h2>{isReadOnly ? campaign.name : "Write the opening message"}</h2>
          <p>
            {isSendingCampaign
              ? "Messages are queued and will continue sending in the background."
              : isSent
              ? `${campaign.replied} of ${campaign.delivered} ${
                  campaign.delivered === 1 ? "owner has" : "owners have"
                } replied. These leads live here now, not in the Lead Pool.`
              : "Every recipient gets this message with their own address and reason filled in."}
          </p>
        </div>
        {!isReadOnly ? (
          <div className="campaign-composer-actions">
            <button type="button" className="sms-button-secondary" onClick={onDiscard}>
              Discard draft
            </button>
            <button
              className="button"
              type="button"
              onClick={() => void handleSend()}
              disabled={!canSend || isSending}
              title={
                preview.template_error ||
                (preview.needs_review_count > 0
                  ? `${preview.needs_review_count} properties need a grounded reason.`
                  : undefined) ||
                (campaign.recipients === 0
                  ? "No lead in this draft can be texted."
                  : isDirty
                    ? "Saving your changes…"
                    : undefined)
              }
            >
              {isSending
                ? "Sending…"
                : `Send to ${campaign.recipients} ${campaign.recipients === 1 ? "lead" : "leads"}`}
            </button>
          </div>
        ) : null}
      </header>

      <div className="campaign-composer-layout">
        <div className="campaign-editor">
          <label className="sms-field">
            <span>Campaign name</span>
            <input
              value={name}
              maxLength={200}
              disabled={isReadOnly}
              onChange={(event) => setName(event.target.value)}
              placeholder="Expired listings — Hoffman Estates"
            />
          </label>

          {campaign.group_reason_mode ? (
            <section className="campaign-reason-groups" aria-labelledby="campaign-reason-heading">
              <div className="campaign-reason-heading">
                <div>
                  <strong id="campaign-reason-heading">Reason suggestions</strong>
                  <small>{preview.reason_groups.length} signals in this campaign</small>
                </div>
                <label>
                  <span>Signal Filter</span>
                  <input
                    type="search"
                    value={reasonSignalSearch}
                    onChange={(event) => setReasonSignalSearch(event.target.value)}
                    placeholder="All Signals"
                    aria-label="Filter reason signals"
                  />
                </label>
              </div>
              {preview.needs_review_count > 0 ? (
                <div className="campaigns-warning">
                  <span>{preview.needs_review_count} properties need a grounded reason before Send.</span>
                  {!isReadOnly ? (
                    <button
                      type="button"
                      className="sms-button-secondary"
                      disabled={Boolean(
                        isRemovingUnresolved || isBulkSaving || isLoadingPage || filterAction ||
                        isSaving || suggestingGroup || savingGroup || dirtyGroups.length || isDirty || isSending
                      )}
                      onClick={() => void removeUnresolved()}
                    >{isRemovingUnresolved ? "Returning…" : "Return unresolved to Lead Pool"}</button>
                  ) : null}
                </div>
              ) : null}
              <div className="campaign-reason-table-scroll">
                <table className="campaign-reason-table">
                  <thead>
                    <tr><th scope="col">Lead Signal</th><th scope="col">Suggested Reason</th></tr>
                  </thead>
                  <tbody>
                    {visibleReasonGroups.map((group) => {
                      const draftReason = groupReasonEdits[group.key] ?? group.reason;
                      const isDirtyGroup = Object.hasOwn(groupReasonEdits, group.key);
                      const suggestionResult = groupSuggestionResults[group.key];
                      const suggestionOptions = suggestionResult?.options?.length
                        ? suggestionResult.options
                        : (suggestionResult?.suggestions ?? []).map((text) => ({
                            text,
                            source: suggestionResult?.source === "ai" ? "ai" as const : "catalog" as const,
                          }));
                      return (
                        <tr key={group.key}>
                          <th scope="row"><strong>{group.label}</strong><small>{group.count} {group.count === 1 ? "lead" : "leads"}</small></th>
                          <td>
                            <textarea
                              aria-label={`${group.label} suggested reason`}
                              value={draftReason}
                              rows={2}
                              maxLength={300}
                              readOnly={isReadOnly}
                              placeholder="Add or suggest a reason"
                              onChange={(event) => {
                                const value = event.target.value;
                                setGroupReasonEdits((current) => {
                                  const next = { ...current };
                                  if (value === group.reason) delete next[group.key];
                                  else next[group.key] = value;
                                  return next;
                                });
                              }}
                            />
                            {suggestionResult ? (
                              <div className="campaign-reason-options">
                                {suggestionOptions.length > 0 ? (
                                  <ol>
                                    {suggestionOptions.slice(0, 3).map((option, index) => (
                                      <li key={`${option.source}-${option.text}`}>
                                        <button
                                          type="button"
                                          onClick={() => setGroupReasonEdits((current) => ({
                                            ...current, [group.key]: option.text,
                                          }))}
                                        >
                                          <span>{index + 1}</span>
                                          <em>{option.text}</em>
                                        </button>
                                      </li>
                                    ))}
                                  </ol>
                                ) : null}
                                {suggestionResult.note ? <p role="status">{suggestionResult.note}</p> : null}
                              </div>
                            ) : null}
                            <div className="campaign-reason-row-actions">
                              <span className={isDirtyGroup ? "needs-confirmation" : group.confirmed ? "confirmed" : undefined}>
                                {isDirtyGroup ? "Needs confirmation" : group.confirmed ? "Confirmed" : "Not confirmed"}
                              </span>
                              {!isReadOnly ? (
                                <>
                                  <button
                                    type="button"
                                    className="sms-button-secondary"
                                    disabled={suggestingGroup !== null || savingGroup !== null}
                                    onClick={() => void suggestGroupReason(group.key)}
                                  >{suggestingGroup === group.key ? "Thinking…" : "✦ Suggest with AI"}</button>
                                  <button
                                    type="button"
                                    className="campaign-reason-confirm"
                                    disabled={!draftReason.trim() || savingGroup !== null || suggestingGroup !== null}
                                    onClick={() => void saveGroup(group.key)}
                                  >{savingGroup === group.key ? "Saving…" : "✓ Save & Confirm"}</button>
                                </>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                    {visibleReasonGroups.length === 0 ? (
                      <tr><td colSpan={2} className="campaign-reason-empty">No signals match this filter.</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>
          ) : <div className="campaign-reason">
            <div className="campaign-reason-head">
              <label htmlFor="campaign-reason-input">Reason</label>
              {!isReadOnly ? (
                <button
                  type="button"
                  className="sms-button-secondary campaign-suggest"
                  onClick={() => void fetchIdeas()}
                  disabled={isSuggesting}
                >
                  {isSuggesting ? "Thinking…" : "✦ Suggest from signals"}
                </button>
              ) : null}
            </div>
            <textarea
              id="campaign-reason-input"
              value={reason}
              rows={2}
              maxLength={300}
              disabled={isReadOnly}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Leave blank to use each lead's own signal"
            />
            <small className="campaigns-note">
              This fills {"{{reason}}"} for every recipient. Blank is right for a mixed
              selection: an expired listing and a probate lead need different wording.
            </small>

            {ideas ? (
              <div className="campaign-ideas">
                {ideas.signals.length > 0 ? (
                  <p className="campaign-ideas-signals">
                    {ideas.signals.map((item) => (
                      <span key={item.key} className="campaign-signal-chip">
                        {item.label} · {Math.round(item.share * 100)}%
                      </span>
                    ))}
                  </p>
                ) : null}
                {ideas.note ? <p className="campaigns-note">{ideas.note}</p> : null}
                <ul className="campaign-ideas-list">
                  {ideas.suggestions.map((text) => (
                    <li key={text}>
                      <button type="button" onClick={() => setReason(text)}>
                        {text}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>}

          <label className="sms-field">
            <span>Message</span>
            <textarea
              ref={editorRef}
              value={template}
              rows={7}
              maxLength={1600}
              disabled={isReadOnly}
              onChange={(event) => setTemplate(event.target.value)}
            />
          </label>

          {!isReadOnly ? (
            <>
              <div className="campaign-tokens">
                <span className="campaign-tokens-label">Insert:</span>
                {TOKEN_ORDER.filter((token) => token in preview.tokens).map((token) => (
                  <button
                    key={token}
                    type="button"
                    className="campaign-token"
                    title={preview.tokens[token]}
                    onClick={() => insertToken(token)}
                  >
                    {`{{${token}}}`}
                  </button>
                ))}
              </div>
              <p className="campaigns-note">
                {isSaving ? "Saving…" : "Each placeholder is filled from the lead before sending."}
              </p>
            </>
          ) : null}

          {preview.template_error ? (
            <p className="sms-error" role="alert">{preview.template_error}</p>
          ) : null}
          {errorMessage && errorMessage !== preview.template_error ? (
            <p className="sms-error" role="alert">{errorMessage}</p>
          ) : null}
          {longCount > 0 ? (
            <p className="campaigns-warning">
              {longCount} {longCount === 1 ? "message on this page runs" : "messages on this page run"} past two SMS
              segments and will be billed as three.
            </p>
          ) : null}

          {preview.skipped.length > 0 ? (
            <details className="campaign-skipped">
              <summary>
                <strong>
                  {preview.skipped.length} {preview.skipped.length === 1 ? "lead" : "leads"} on this
                  page will not be texted
                </strong>
                <span className="campaign-skipped-toggle" aria-hidden="true">
                  <span className="campaign-skipped-show">Show reasons</span>
                  <span className="campaign-skipped-hide">Hide reasons</span>
                </span>
              </summary>
              <ul>
                {preview.skipped.map((item) => (
                  <li key={item.lead_id}>
                    <span>
                      {!isReadOnly ? (
                        <input
                          type="checkbox"
                          aria-label={`Select ${item.owner_name ?? `Lead ${item.lead_id}`}`}
                          checked={selectedIds.includes(item.lead_id)}
                          onChange={() => setSelectedIds((current) =>
                            current.includes(item.lead_id)
                              ? current.filter((id) => id !== item.lead_id)
                              : [...current, item.lead_id])}
                        />
                      ) : null}
                      {item.owner_name ?? `Lead ${item.lead_id}`}
                    </span>
                    <em><span className="sr-only">Reason: </span>{displaySkipReason(item.reason)}</em>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>

      </div>

      <div className="campaign-people">
        <div className="campaign-people-head">
          <h3>{isSendingCampaign ? "Who is queued" : isSent ? "Who was contacted" : "Who this will reach"}</h3>
          <span className="sms-count">{campaign.recipients}</span>
          {!isReadOnly ? (
            <div className="campaign-filter-wrap">
              <button
                type="button"
                className="sms-icon-button campaign-filter-button"
                aria-label="Filter campaign recipients"
                title="Filter recipients"
                onClick={() => setShowFilters((value) => !value)}
              >
                <svg viewBox="0 0 18 18" aria-hidden="true">
                  <path d="M3 5h12M5 9h8M7 13h4" />
                </svg>
              </button>
              {showFilters ? (
                <div className="campaign-filter-popover" aria-busy={filterAction !== null}>
                  <input
                    aria-label="Search owners or properties"
                    placeholder="Owner or property"
                    value={filterDraft.search ?? ""}
                    disabled={filterAction !== null}
                    onChange={(event) => setFilterDraft({ ...filterDraft, search: event.target.value })}
                  />
                  <input
                    aria-label="Filter area or ZIP"
                    placeholder="Area or ZIP"
                    value={filterDraft.area ?? ""}
                    disabled={filterAction !== null}
                    onChange={(event) => setFilterDraft({ ...filterDraft, area: event.target.value })}
                  />
                  <select
                    aria-label="Filter by signal"
                    value={filterDraft.signals?.[0] ?? ""}
                    disabled={filterAction !== null}
                    onChange={(event) => setFilterDraft({
                      ...filterDraft,
                      signals: event.target.value ? [event.target.value] : [],
                    })}
                  >
                    <option value="">All signals</option>
                    {signalOptions.map((signal) => <option key={signal.key} value={signal.key}>{signal.label}</option>)}
                  </select>
                  <label><input type="checkbox" checked={Boolean(filterDraft.no_signal)} disabled={filterAction !== null} onChange={(event) => setFilterDraft({ ...filterDraft, no_signal: event.target.checked })} /> No signal</label>
                  <div className="campaign-filter-actions">
                    {filterAction ? (
                      <span className="campaign-filter-loading" role="status">
                        <span aria-hidden="true" />
                        {filterAction === "clear" ? "Clearing…" : "Applying…"}
                      </span>
                    ) : <span />}
                    <button
                      type="button"
                      className="campaign-filter-clear"
                      disabled={filterAction !== null}
                      onClick={() => {
                        setFilterDraft({});
                        void applyFilters({}, "clear");
                      }}
                    >Clear</button>
                    <button
                      type="button"
                      className="campaign-filter-apply"
                      disabled={filterAction !== null}
                      onClick={() => void applyFilters(filterDraft)}
                    >Apply</button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        {!isReadOnly && (selectedIds.length > 0 || selectAllFiltered) ? (
          <div className="campaign-bulk-bar">
            <strong>{selectAllFiltered ? `${preview.total_items} filtered contacts` : `${selectedIds.length} selected`}</strong>
            <button type="button" className="sms-button-secondary" disabled={Boolean(isBulkSaving || isRemovingUnresolved || isLoadingPage || filterAction || isSaving || suggestingGroup || savingGroup || isSending)} onClick={() => void removeSelected()}>Return to Lead Pool</button>
          </div>
        ) : null}

        {recipients.length === 0 ? (
          <p className="sms-muted">
            {isSendingCampaign
              ? "This campaign is still being processed."
              : isSent
              ? "This campaign did not reach anyone."
              : campaign.recipients > 0
                ? "No recipients match the current filters."
                : "This draft has zero recipients. You can keep editing it or discard it."}
          </p>
        ) : (
          <div className="leads-table-wrap">
            <table className="leads-table campaign-table">
              <thead>
                <tr>
                  {!isReadOnly ? <th scope="col"><input type="checkbox" aria-label="Select all contacts on this page" checked={recipients.length > 0 && recipients.every((item) => selectedIds.includes(item.lead_id))} onChange={(event) => setSelectedIds(event.target.checked ? [...new Set([...selectedIds, ...recipients.map((item) => item.lead_id)])] : selectedIds.filter((id) => !recipients.some((item) => item.lead_id === id)))} /></th> : null}
                  <th scope="col">Owner / Property</th>
                  <th scope="col">Signals</th>
                  <th scope="col" className="leads-numeric">Score</th>
                  <th scope="col">{isSendingCampaign ? "Message queued" : isSent ? "Message sent" : "Message"}</th>
                  <th scope="col">{isSendingCampaign ? "Status" : isSent ? "Reply" : "Length"}</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {recipients.map((item) => (
                  <tr key={item.lead_id}>
                    {!isReadOnly ? <td><input type="checkbox" aria-label={`Select ${item.owner_name ?? "contact"}`} checked={selectedIds.includes(item.lead_id)} onChange={() => setSelectedIds((current) => current.includes(item.lead_id) ? current.filter((id) => id !== item.lead_id) : [...current, item.lead_id])} /></td> : null}
                    <th scope="row">
                      <strong>{item.owner_name ?? "Unnamed owner"}</strong>
                      <small>{[item.property_address, item.area].filter(Boolean).join(" · ")}</small>
                    </th>
                    <td>
                      <span className="leads-signals">
                        {item.signals.length === 0 ? (
                          <span className="leads-none">—</span>
                        ) : (
                          item.signals.map((signal) => (
                            <span key={signal.key} className="leads-signal">{signal.label}</span>
                          ))
                        )}
                      </span>
                    </td>
                    <td className="leads-numeric">{item.score}</td>
                    <td className="campaign-message-cell">
                      {item.text}
                      {!isReadOnly && item.needs_review ? <small className="campaign-reason-needed">Reason review required</small> : null}
                    </td>
                    <td>
                      {isSendingCampaign ? (
                        <span className="campaign-reply-badge">Queued</span>
                      ) : isSent ? (
                        <span className={`campaign-reply-badge${item.replied ? " replied" : ""}`}>
                          {item.replied ? "Replied" : "No reply Yet"}
                        </span>
                      ) : (
                        <small className={item.is_long ? "campaign-long" : undefined}>
                          {item.text.length} chars
                          {item.is_long ? " · 3 segments" : null}
                        </small>
                      )}
                    </td>
                    <td className="campaign-open-cell">
                      <button
                        type="button"
                        className="sms-icon-button"
                        onClick={() => setDetailId(item.lead_id)}
                        aria-label={`View details for ${item.owner_name ?? "this owner"}`}
                        title="View property details"
                      >
                        ⓘ
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!isReadOnly && preview.total_items > recipients.length ? (
          <label className="campaign-select-filtered">
            <input type="checkbox" checked={selectAllFiltered} onChange={(event) => setSelectAllFiltered(event.target.checked)} />
            Select all {preview.total_items.toLocaleString()} filtered contacts across every page
          </label>
        ) : null}
        {recipientPages > 1 ? (
          <nav className="campaigns-pagination campaign-recipients-pagination" aria-label="Campaign recipient pages">
            <button
              type="button"
              onClick={() => void loadRecipientPage(Math.max(1, currentRecipientPage - 1))}
              disabled={currentRecipientPage === 1 || isLoadingPage || isSaving || isDirty}
            >
              Previous
            </button>
            <span>
              Page {currentRecipientPage} of {recipientPages} · {preview.total_items.toLocaleString()} leads
            </span>
            <button
              type="button"
              onClick={() => void loadRecipientPage(Math.min(recipientPages, currentRecipientPage + 1))}
              disabled={currentRecipientPage === recipientPages || isLoadingPage || isSaving || isDirty}
            >
              Next
            </button>
          </nav>
        ) : null}
      </div>

      {/* The same panel the Lead Pool opens: a lead handed to a campaign leaves
          the pool, so this is the only place its property record can be read. */}
      <LeadDetailDrawer
        leadId={detailId}
        accessToken={accessToken}
        onClose={() => setDetailId(null)}
        onOpenConversation={(conversationId) => {
          setDetailId(null);
          onOpenConversation(conversationId);
        }}
      />
    </section>
  );
}
