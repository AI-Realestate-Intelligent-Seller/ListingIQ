"use client";

import { useId, useState } from "react";




/** Normalise whatever shape the API returns into a plain string[]. */
export function toStringArray(data: unknown): string[] {
  if (!Array.isArray(data)) return [];

  return data.map((item) => {
    if (typeof item === "string") return item;

    const obj = item as Record<string, string>;

    return (
      obj.zip ??
      obj.zipcode ??
      obj.city ??
      obj.name ??
      obj.state ??
      obj.code ??
      String(item)
    );
  });
}

/** property_address is "street, city, STATE, ZIP" — pull city/state/zip off the end. */
export function parseAddress(
  address: string | null,
): { city: string; state: string; zip: string } {
  if (!address) return { city: "", state: "", zip: "" };

  const parts = address
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);

  if (parts.length < 2) {
    return { city: "", state: "", zip: "" };
  }

  const zip = parts[parts.length - 1];
  const state = parts[parts.length - 2];
  const city = parts.length >= 3 ? parts[parts.length - 3] : "";

  return { city, state, zip };
}

export type LocationComboProps = {
  icon: React.ReactNode;
  ariaLabel: string;
  placeholder: string;
  options: string[];
  selected: string | string[];
  onSelect: (value: string) => void;
  onClear: (value?: string) => void;
  onClick?: () => void;
  onSubmit?: (query: string) => void;
  /** Limits very large nationwide menus; omit after State/City narrows the options. */
  optionLimit?: number;
};

export function LocationCombo({
  icon,
  ariaLabel,
  placeholder,
  options,
  selected,
  onSelect,
  onClear,
  onClick,
  onSubmit,
  optionLimit,
}: LocationComboProps) {
    const listId = useId();
    const [query, setQuery] = useState("");
    const [open, setOpen] = useState(false);
    const [editing, setEditing] = useState(false);
    const isMulti = Array.isArray(selected);
    const selectedValues = isMulti ? selected : selected ? [selected] : [];
    const singleSelected = typeof selected === "string" ? selected : "";
  
    const matchingOptions = options.filter((option) =>
      option.toLowerCase().includes(query.toLowerCase()) &&
      (!Array.isArray(selected) || !selectedValues.includes(option))
    );
    const filtered = optionLimit === undefined
      ? matchingOptions
      : matchingOptions.slice(0, optionLimit);
  
    return (
      <div className="leads-location-combo">
        <div className={`leads-combo-field${Array.isArray(selected) ? " multi" : ""}`}>
          <span className="leads-combo-icon" aria-hidden="true">
            {icon}
          </span>

          {Array.isArray(selected) && selected.length > 0 ? (
            <div className="leads-combo-values" aria-label={`${ariaLabel} selections`}>
              {selected.map((value) => (
                <span key={value} className="leads-combo-value">
                  {value}
                  <button
                    type="button"
                    aria-label={`Remove ${value}`}
                    onClick={() => onClear(value)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : null}

          <input
          type="text"
          role="combobox"
          className="leads-combo-input"
          placeholder={Array.isArray(selected) && selected.length > 0 ? "Add ZIP…" : placeholder}
          value={editing ? query : singleSelected}
          onChange={(event) => {
            setEditing(true);
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={(event) => {
            onClick?.();
            if (singleSelected) event.currentTarget.select();
            if (options.length > 0) {
              setOpen(true);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setOpen(false);
              setEditing(false);
              setQuery("");
              return;
            }
            if (event.key !== "Enter") return;

            const trimmed = query.trim();
            if (onSubmit && trimmed) {
              event.preventDefault();
              onSubmit(trimmed);
              setQuery("");
              setEditing(false);
              setOpen(false);
              return;
            }
            if (!onSubmit && filtered[0]) {
              event.preventDefault();
              onSelect(filtered[0]);
              setQuery("");
              setEditing(false);
              setOpen(false);
            }
          }}
          onBlur={() => window.setTimeout(() => {
            setOpen(false);
            setEditing(false);
            setQuery("");
          }, 120)}
          aria-label={ariaLabel}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-controls={listId}
          aria-autocomplete="list"
          />

          {singleSelected ? (
            <button
              type="button"
              className="leads-combo-clear"
              aria-label={`Clear ${ariaLabel}`}
              onMouseDown={(event) => {
                event.preventDefault();
                onClear(singleSelected);
                setQuery("");
                setEditing(false);
                setOpen(false);
              }}
            >
              ×
            </button>
          ) : null}
        </div>
  
      {open && options.length > 0 ? (
    <ul
      id={listId}
      className="leads-combo-list"
      role="listbox"
      aria-label={ariaLabel}
    >
      {filtered.length === 0 ? (
        <>
          {onSubmit && query.trim() ? (
            <li
              role="option"
              aria-selected={false}
              className="leads-combo-search"
              onMouseDown={(event) => {
                event.preventDefault();
  
                const trimmed = query.trim();
  
                onSubmit(trimmed);
                setQuery("");
                setEditing(false);
                setOpen(false);
              }}
            >
              Search &quot;{query.trim()}&quot;
            </li>
          ) : (
            <li className="leads-combo-empty">
              No matches
            </li>
          )}
        </>
      ) : (
        <>
          {filtered.map((option) => (
            <li
              key={option}
              role="option"
              aria-selected={selectedValues.includes(option)}
              className={
                selectedValues.includes(option) ? "active" : undefined
              }
              onMouseDown={(event) => {
                event.preventDefault();
                onSelect(option);
                setQuery("");
                setEditing(false);
                // Multi-select (e.g. ZIPs): keep the list open so the user
                // can pick the next one without refocusing the field.
                if (!isMulti) setOpen(false);
              }}
            >
              {option}
            </li>
          ))}
  
          {onSubmit && query.trim() ? (
            <li
              role="option"
              aria-selected={false}
              className="leads-combo-search"
              onMouseDown={(event) => {
                event.preventDefault();
  
                const trimmed = query.trim();
  
                onSubmit(trimmed);
                setQuery("");
                setEditing(false);
                setOpen(false);
              }}
            >
              Search &quot;{query.trim()}&quot;
            </li>
          ) : null}
        </>
      )}
    </ul>
  ) : null}</div>
    );
}


export const PIN_ICON = (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" />
    <circle cx="12" cy="9" r="2.5" />
  </svg>
);

export const ZIP_ICON = (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <line x1="4" y1="9" x2="20" y2="9" />
    <line x1="4" y1="15" x2="20" y2="15" />
    <line x1="10" y1="3" x2="8" y2="21" />
    <line x1="16" y1="3" x2="14" y2="21" />
  </svg>
);

export const CITY_ICON = (
  <svg
    width="12"
    height="12"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M3 21h18" />
    <path d="M5 21V7l7-4 7 4v14" />
    <path d="M9 21v-6h6v6" />
  </svg>
);





/**
 * A single resolved match for a free-typed city search. A query like
 * "Charleston" is ambiguous — there are ~22 Charlestons across different
 * states — so resolveLocation() can return multiple rows for one query.
 * We keep all of them instead of collapsing to the first result.
 */
export type ResolvedLocation = {
  city: string;
  state: string;
  zip: string;
};

// ── Location filter (selected values) ──────────────────────────────────────
// Everything here changes together as one unit — picking a zip clears the
// city-search matches, changing state clears the zip, etc. — so it's a
// reducer instead of four-plus independent useState calls that each handler
// had to keep in sync by hand.
export type LocationFilterState = {
  state: string; // "State Name, XX" combo value from LocationCombo
  city: string; // raw text the user searched
  cityKeys: string[]; // canonical exact-city keys, e.g. "greenleaf|WI"
  addressQuery: string; // street/address text, kept separate from advanced search
  zips: string[]; // manually-picked ZIPs; selections widen the ZIP filter
  matches: ResolvedLocation[]; // all matches resolved for `city`
  isResolving: boolean;
};

export  const EMPTY_LOCATION_FILTER: LocationFilterState = {
  state: "",
  city: "",
  cityKeys: [],
  addressQuery: "",
  zips: [],
  matches: [],
  isResolving: false,
};

export type LocationFilterAction =
  | { type: "SET_STATE"; value: string }
  | { type: "CLEAR_STATE" }
  | { type: "SET_ZIP"; value: string }
  | { type: "REMOVE_ZIP"; value: string }
  | { type: "CLEAR_ZIPS" }
  | { type: "RESOLVE_START" }
  | { type: "RESOLVE_SUCCESS"; city: string; matches: ResolvedLocation[]; cityKeys?: string[]; addressQuery?: string }
  | { type: "RESOLVE_FAILURE" }
  | { type: "CLEAR_CITY" }
  | { type: "CLEAR_ALL" };

export function locationFilterReducer(
  state: LocationFilterState,
  action: LocationFilterAction,
): LocationFilterState {
  switch (action.type) {
    case "SET_STATE":
      // State is the parent filter: changing it invalidates any city/ZIP
      // that belonged to the previously selected state.
      return { ...state, state: action.value, city: "", cityKeys: [], addressQuery: "", zips: [], matches: [] };
    case "CLEAR_STATE":
      return { ...state, state: "" };
    case "SET_ZIP":
      // A manually chosen zip overrides any free-text/geocoded ambiguity,
      // but an already-resolved city (picked from the dropdown) stays
      // selected - zips widen that selection instead of replacing it.
      return {
        ...state,
        zips: state.zips.includes(action.value) ? state.zips : [...state.zips, action.value],
        addressQuery: "",
        matches: [],
      };
    case "REMOVE_ZIP":
      return { ...state, zips: state.zips.filter((zip) => zip !== action.value) };
    case "CLEAR_ZIPS":
      return { ...state, zips: [] };
    case "RESOLVE_START":
      return { ...state, isResolving: true };
    case "RESOLVE_SUCCESS":
      return {
        ...state,
        city: action.city,
        cityKeys: action.cityKeys ?? [],
        addressQuery: action.addressQuery ?? "",
        matches: action.matches,
        zips: [],
        isResolving: false,
      };
    case "RESOLVE_FAILURE":
      return { ...state, cityKeys: [], addressQuery: "", matches: [], isResolving: false };
    case "CLEAR_CITY":
      return { ...state, city: "", cityKeys: [], addressQuery: "", matches: [] };
    case "CLEAR_ALL":
      return EMPTY_LOCATION_FILTER;
    default:
      return state;
  }
}

// ── Location options (dropdown data) ────────────────────────────────────────
// These are just fetched lists with no cross-field rules, so one object with
// a merge-setter is enough — no need for a reducer here.
export type LocationOptions = {
  states: string[];   // narrowed set — by city when no state is picked
  cities: string[];   // narrowed by state/zip
  zips: string[];     // narrowed by state, or by city when no state is picked
  allCities: string[];
  allZips: string[];
  allStates: string[]; // full list, loaded once on mount
};

export const EMPTY_LOCATION_OPTIONS: LocationOptions = {
  states: [],
  cities: [],
  zips: [],
  allCities: [],
  allZips: [],
  allStates: [],
};
