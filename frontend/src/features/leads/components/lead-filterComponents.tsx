"use client";

import { useCallback, useEffect, useMemo, useState } from "react";




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
  selected: string;
  onSelect: (value: string) => void;
  onClear: () => void;
  onClick?: () => void;
  onSubmit?: (query: string) => void;
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
}: LocationComboProps) {
  
    const [query, setQuery] = useState("");
    const [open, setOpen] = useState(false);
  
    const filtered = options
      .filter((option) =>
        option.toLowerCase().includes(query.toLowerCase())
      )
      .slice(0, 50);
  
    return (
      <div className="leads-location-combo">
        <span className="leads-combo-icon" aria-hidden="true">
          {icon}
        </span>
  
        <input
          type="text"
          className="leads-combo-input"
          placeholder={selected || placeholder}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => {
            onClick?.();
  
            if (options.length > 0) {
              setOpen(true);
            }
          }}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            const trimmed = query.trim();
            if (!onSubmit || !trimmed) return;
            event.preventDefault();
            onSubmit(trimmed);
            setOpen(false);
          }}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          aria-label={ariaLabel}
          aria-expanded={open}
          aria-haspopup="listbox"
        />
  
        {selected ? (
          <button
            type="button"
            className="leads-combo-clear"
            aria-label={`Clear ${ariaLabel}`}
            onMouseDown={(event) => {
              event.preventDefault();
              onClear();
              setQuery("");
            }}
          >
            ×
          </button>
        ) : null}
  
      {open && options.length > 0 ? (
    <ul
      className="leads-combo-list"
      role="listbox"
      aria-label={ariaLabel}
    >
      {filtered.length === 0 ? (
        <>
          {onSubmit && query.trim() ? (
            <li
              role="option"
              className="leads-combo-search"
              onMouseDown={(event) => {
                event.preventDefault();
  
                const trimmed = query.trim();
  
                onSubmit(trimmed);
                setQuery("");
                setOpen(false);
              }}
            >
              Search "{query.trim()}"
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
              aria-selected={selected === option}
              className={
                selected === option ? "active" : undefined
              }
              onMouseDown={() => {
                onSelect(option);
                setQuery("");
                setOpen(false);
              }}
            >
              {option}
            </li>
          ))}
  
          {onSubmit && query.trim() ? (
            <li
              role="option"
              className="leads-combo-search"
              onMouseDown={(event) => {
                event.preventDefault();
  
                const trimmed = query.trim();
  
                onSubmit(trimmed);
                setQuery("");
                setOpen(false);
              }}
            >
              Search "{query.trim()}"
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
  zip: string; // a single manually-picked zip
  matches: ResolvedLocation[]; // all matches resolved for `city`
  isResolving: boolean;
};

export  const EMPTY_LOCATION_FILTER: LocationFilterState = {
  state: "",
  city: "",
  zip: "",
  matches: [],
  isResolving: false,
};

export type LocationFilterAction =
  | { type: "SET_STATE"; value: string }
  | { type: "CLEAR_STATE" }
  | { type: "SET_ZIP"; value: string }
  | { type: "CLEAR_ZIP" }
  | { type: "RESOLVE_START" }
  | { type: "RESOLVE_SUCCESS"; city: string; matches: ResolvedLocation[] }
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
      return { ...state, state: action.value, city: "", zip: "", matches: [] };
    case "CLEAR_STATE":
      return { ...state, state: "" };
    case "SET_ZIP":
      // A manually chosen zip overrides any city-search ambiguity.
      return { ...state, zip: action.value, city: "", matches: [] };
    case "CLEAR_ZIP":
      return { ...state, zip: "" };
    case "RESOLVE_START":
      return { ...state, isResolving: true };
    case "RESOLVE_SUCCESS":
      return {
        ...state,
        city: action.city,
        matches: action.matches,
        zip: "",
        isResolving: false,
      };
    case "RESOLVE_FAILURE":
      return { ...state, matches: [], isResolving: false };
    case "CLEAR_CITY":
      return { ...state, city: "", matches: [] };
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

