"use client";

import { useState } from "react";

import type { ImportPreview } from "../types/leads.types";

/** The fields a broker can point at a column, in the order they are shown. */
const MAPPABLE: { field: string; label: string; hint?: string }[] = [
  { field: "owner_name", label: "Owner name" },
  { field: "phone", label: "Phone", hint: "Duplicate fallback when no property is selected" },
  { field: "property_address", label: "Property address", hint: "Select this to match duplicate properties" },
  { field: "area", label: "Area or city" },
  { field: "signals", label: "Signals" },
];

type ImportDialogProps = {
  file: File | null;
  preview: ImportPreview | null;
  isImporting: boolean;
  isRemapping: boolean;
  mappingError: string;
  onRemap: (mapping: Record<string, string>, sheet: string) => void;
  onClose: () => void;
  onConfirm: (options: {
    limit: number;
    mapping: Record<string, string>;
    sheet: string;
  }) => Promise<void>;
};

/**
 * Shown between choosing a file and importing it. The counts and the sample
 * come from a dry run on the server, so the row limit and the column mapping
 * are chosen against what the file actually contains rather than a guess.
 */
export function ImportDialog({
  file,
  preview,
  isImporting,
  isRemapping,
  mappingError,
  onRemap,
  onClose,
  onConfirm,
}: ImportDialogProps) {
  const [limitAll, setLimitAll] = useState(true);
  // 0 means "no cap", matching the API, so the default agrees with "All leads".
  const [limit, setLimit] = useState("0");
  const [mapping, setMapping] = useState<Record<string, string> | null>(null);
  const [sheet, setSheet] = useState("");
  const [errorMessage, setErrorMessage] = useState("");

  if (!file) return null;

  /** The mapping in force: the broker's edits, else whatever was detected. */
  const current: Record<string, string> =
    mapping ??
    Object.fromEntries(MAPPABLE.map(({ field }) => [field, preview?.mapping?.[field] ?? ""]));
  const currentSheet = sheet || preview?.selected_sheet || "";

  function chooseColumn(field: string, column: string): void {
    const next = { ...current, [field]: column };
    setMapping(next);
    onRemap(next, currentSheet);
  }

  function chooseSheet(nextSheet: string): void {
    setSheet(nextSheet);
    setMapping(null);
    onRemap({}, nextSheet);
  }

  const available = preview?.importable ?? 0;
  const requested = Math.max(0, Math.min(Number(limit) || 0, available));
  // A cap of 0 is no cap, whichever radio is selected.
  const capped = !limitAll && requested > 0;
  const takes = capped ? requested : available;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isImporting) return;
    setErrorMessage("");
    try {
      await onConfirm({ limit: capped ? requested : 0, mapping: current, sheet: currentSheet });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "The file could not be imported.");
    }
  }

  return (
    <div className="sms-dialog-scrim" role="presentation" onClick={onClose}>
      <section
        className="sms-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="sms-eyebrow">IMPORT LEADS</span>
            <h2 id="import-dialog-title">{file.name}</h2>
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        {preview === null ? (
          <p className="sms-muted">Reading the file…</p>
        ) : (
          <form onSubmit={handleSubmit}>
            {preview.worksheets.length > 1 ? (
              <fieldset className="import-fieldset import-sheet-fieldset">
                <legend>Worksheet</legend>
                <label>
                  <span>Choose the sheet containing the leads</span>
                  <select
                    value={currentSheet}
                    onChange={(event) => chooseSheet(event.target.value)}
                    disabled={isImporting || isRemapping}
                  >
                    {preview.worksheets.map((worksheet) => (
                      <option key={worksheet} value={worksheet}>{worksheet}</option>
                    ))}
                  </select>
                </label>
              </fieldset>
            ) : null}
            <ul className="import-summary two">
              <li>
                <strong>{preview.total_rows}</strong>
                <span>rows readable</span>
              </li>
              <li>
                <strong>{takes}</strong>
                <span>leads to add</span>
              </li>
            </ul>


            <fieldset className="import-fieldset">
              <legend>Columns</legend>
              <p className="import-mapping-note">
                Select the property-address column before importing. Other fields are
                detected from the header, and the counts update as you change them.
              </p>
              <div className={`import-mapping${isRemapping ? " busy" : ""}`}>
                {MAPPABLE.map(({ field, label, hint }) => (
                  <label key={field}>
                    <span>
                      {label}
                      {hint ? <small>{hint}</small> : null}
                    </span>
                    <select
                      value={current[field] ?? ""}
                      onChange={(event) => chooseColumn(field, event.target.value)}
                      disabled={isImporting || isRemapping}
                    >
                      <option value="">Not in this file</option>
                      {preview.columns.map((column) => (
                        <option key={column} value={column}>{column}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              {Object.keys(preview.signal_columns).length > 0 ? (
                <p className="import-mapping-note">
                  Signal flag columns: {Object.values(preview.signal_columns).join(", ")}
                </p>
              ) : null}
              {mappingError ? <p className="sms-error" role="alert">{mappingError}</p> : null}
            </fieldset>

            {preview.sample.length > 0 ? (
              <div className="import-sample">
                <strong>First {preview.sample.length} leads as read</strong>
                <div className="import-sample-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Owner</th>
                        <th>Phone</th>
                        <th>Property</th>
                        <th>Signals</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.sample.map((row, index) => (
                        <tr key={index}>
                          <td>{row.owner_name || "—"}</td>
                          <td>{row.phone || "—"}</td>
                          <td>
                            {row.property_address || "—"}
                            {row.area ? ` · ${row.area}` : ""}
                          </td>
                          <td>{row.signals.join(", ") || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}


            <fieldset className="import-fieldset">
              <legend>How many to import</legend>
              <label className="import-option">
                <input
                  type="radio"
                  name="limit_mode"
                  checked={limitAll}
                  onChange={() => setLimitAll(true)}
                  disabled={isImporting}
                />
                <span>
                  <strong>All {preview.importable} leads</strong>
                </span>
              </label>
              <label className="import-option">
                <input
                  type="radio"
                  name="limit_mode"
                  checked={!limitAll}
                  onChange={() => setLimitAll(false)}
                  disabled={isImporting}
                />
                <span>
                  <strong>Only the first</strong>
                  <input
                    className="import-number"
                    type="number"
                    min={0}
                    max={preview.importable}
                    value={limit}
                    onChange={(event) => setLimit(event.target.value)}
                    disabled={isImporting}
                    aria-label="Number of leads to import"
                  />
                  <small>Rows are taken from the top of the file. 0 imports all of them.</small>
                </span>
              </label>
            </fieldset>


            {preview.warnings.length > 0 ? (
              <div className="import-warnings">
                <strong>{preview.warnings.length} row(s) need attention</strong>
                <ul>
                  {preview.warnings.slice(0, 4).map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
                {preview.warnings.length > 4 ? (
                  <small>…and {preview.warnings.length - 4} more.</small>
                ) : null}
              </div>
            ) : null}

            {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}

            <div className="sms-dialog-actions">
              <button
                type="button"
                className="sms-button-secondary"
                onClick={onClose}
                disabled={isImporting}
              >
                Cancel
              </button>
              <button className="button" type="submit" disabled={isImporting || isRemapping}>
                {isImporting ? "Importing…" : `Import ${takes} lead${takes === 1 ? "" : "s"}`}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
