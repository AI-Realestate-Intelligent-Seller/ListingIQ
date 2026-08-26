"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { LocationFacets, LocationOption } from "../types/leads.types";

/** Which of the three lists an option came from, and how it reads on its chip. */
type Kind = "states" | "cities" | "zips";

const GROUPS: { kind: Kind; label: string }[] = [
  { kind: "states", label: "State" },
  { kind: "cities", label: "City / town" },
  { kind: "zips", label: "ZIP" },
];

/** How many matches each group shows before the rest are summarised. */
const VISIBLE_PER_GROUP = 6;

export type LocationSelection = {
  states: string[];
  cities: string[];
  zips: string[];
};

export const NO_LOCATION: LocationSelection = { states: [], cities: [], zips: [] };

export function locationCount(selection: LocationSelection): number {
  return selection.states.length + selection.cities.length + selection.zips.length;
}

type Entry = LocationOption & { kind: Kind };

type LocationFilterProps = {
  /** The menu as the server last counted it, under the current selection. */
  facets: LocationFacets;
  selection: LocationSelection;
  onChange: (next: LocationSelection) => void;
  advancedSearch: string;
  onAdvancedSearchChange: (next: string) => void;
};

/**
 * One box for all three location questions.
 *
 * A broker knows one of three things about where they want to prospect — the
 * state, the town, or the ZIP — and rarely the same one twice. Three cascading
 * dropdowns would make them answer in an order the data does not require, so
 * this takes whichever is typed and turns it into a chip. Chips of the same
 * kind widen the result; chips of different kinds narrow it.
 *
 * The counts are the server's, recomputed per request under the *other* two
 * filters (see location_facets), which is what makes picking Illinois shorten
 * the town list without hiding the other states.
 */
export function LocationFilter({
  facets,
  selection,
  onChange,
  advancedSearch,
  onAdvancedSearchChange,
}: LocationFilterProps) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [isAdvancedOpen, setIsAdvancedOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listId = useId();

  const chips = useMemo(() => {
    const byKey = new Map<string, Entry>();
    for (const { kind } of GROUPS) {
      for (const option of facets[kind]) byKey.set(`${kind}:${option.key}`, { ...option, kind });
    }
    // A selected key the server no longer lists still gets a chip, or the
    // filter would be in force with nothing on screen to lift it.
    return GROUPS.flatMap(({ kind }) =>
      selection[kind].map(
        (key) =>
          byKey.get(`${kind}:${key}`) ?? { key, label: key.split("|")[0], count: 0, kind },
      ),
    );
  }, [facets, selection]);

  /** Matches for what has been typed, grouped, with the already-chosen removed. */
  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return GROUPS.map(({ kind, label }) => {
      const matches = facets[kind].filter(
        (option) =>
          !selection[kind].includes(option.key) &&
          (needle === "" ||
            option.label.toLowerCase().includes(needle) ||
            option.key.toLowerCase().includes(needle)),
      );
      return { kind, label, matches };
    }).filter((group) => group.matches.length > 0);
  }, [facets, selection, query]);

  /** The options actually rendered, in order, so the arrow keys can walk them. */
  const walkable = useMemo(
    () =>
      groups.flatMap(({ kind, matches }) =>
        matches.slice(0, VISIBLE_PER_GROUP).map((option): Entry => ({ ...option, kind })),
      ),
    [groups],
  );

  useEffect(() => {
    setCursor(0);
  }, [query]);

  // A click anywhere else closes the menu; the chips stay, so nothing is lost.
  useEffect(() => {
    if (!isOpen && !isAdvancedOpen) return;
    const onDown = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
        setIsAdvancedOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [isOpen, isAdvancedOpen]);

  function add(entry: Entry): void {
    if (selection[entry.kind].includes(entry.key)) return;
    onChange({ ...selection, [entry.kind]: [...selection[entry.kind], entry.key] });
    setQuery("");
    setCursor(0);
    inputRef.current?.focus();
  }

  function remove(kind: Kind, key: string): void {
    onChange({ ...selection, [kind]: selection[kind].filter((item) => item !== key) });
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setIsOpen(true);
      if (walkable.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setCursor((current) => (current + step + walkable.length) % walkable.length);
      return;
    }
    if (event.key === "Enter" && isOpen && walkable[cursor]) {
      event.preventDefault();
      add(walkable[cursor]);
      return;
    }
    if (event.key === "Escape") {
      setIsOpen(false);
      return;
    }
    // Backspace on an empty box takes back the last chip, as in a mail client.
    if (event.key === "Backspace" && query === "" && chips.length > 0) {
      const last = chips[chips.length - 1];
      remove(last.kind, last.key);
    }
  }

  const active = walkable[cursor];

  return (
    <div className="leads-location" ref={boxRef}>
      <div className="leads-location-box" onClick={() => inputRef.current?.focus()}>
        {chips.map((chip) => (
          <span key={`${chip.kind}:${chip.key}`} className={`leads-location-chip ${chip.kind}`}>
            {chip.label}
            <button
              type="button"
              onClick={() => remove(chip.kind, chip.key)}
              aria-label={`Remove the ${chip.label} filter`}
            >
              ✕
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          value={query}
          placeholder={chips.length === 0 ? "State, city or ZIP" : ""}
          aria-label="Filter by location"
          aria-expanded={isOpen}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active ? `${listId}-${active.kind}-${active.key}` : undefined}
          onFocus={() => setIsOpen(true)}
          onChange={(event) => {
            setQuery(event.target.value);
            setIsOpen(true);
          }}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className={`leads-advanced-button${advancedSearch ? " active" : ""}`}
          aria-label="Open advanced lead search"
          aria-expanded={isAdvancedOpen}
          title="Advanced search"
          onClick={(event) => {
            event.stopPropagation();
            setIsOpen(false);
            setIsAdvancedOpen((current) => !current);
          }}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3 5h14M6 10h8M8.5 15h3" />
            <circle cx="8" cy="5" r="1.5" />
            <circle cx="12" cy="10" r="1.5" />
            <circle cx="10" cy="15" r="1.5" />
          </svg>
        </button>
      </div>

      {isAdvancedOpen ? (
        <div className="leads-advanced-menu">
          <label htmlFor={`${listId}-advanced`}>Advanced search</label>
          <div className="leads-advanced-menu-field">
            <input
              id={`${listId}-advanced`}
              type="search"
              autoFocus
              value={advancedSearch}
              placeholder="Phone, DNC, signal, property detail, ID…"
              onChange={(event) => onAdvancedSearchChange(event.target.value)}
            />
            {advancedSearch ? (
              <button
                type="button"
                onClick={() => onAdvancedSearchChange("")}
                aria-label="Clear advanced search"
              >
                ✕
              </button>
            ) : null}
          </div>
          <small>Searches all visible fields and imported lead metadata.</small>
        </div>
      ) : null}

      {isOpen && !isAdvancedOpen ? (
        <div className="leads-location-menu" id={listId} role="listbox">
          {groups.length === 0 ? (
            <p className="leads-location-none">
              {query.trim()
                ? `No state, town or ZIP in your pool matches “${query.trim()}”.`
                : "Import leads with addresses to filter by location."}
            </p>
          ) : (
            groups.map(({ kind, label, matches }) => (
              <div key={kind} className="leads-location-group">
                <p className="leads-location-group-label">{label}</p>
                {matches.slice(0, VISIBLE_PER_GROUP).map((option) => (
                  <button
                    key={option.key}
                    id={`${listId}-${kind}-${option.key}`}
                    type="button"
                    role="option"
                    aria-selected={active?.kind === kind && active?.key === option.key}
                    className={`leads-location-option${
                      active?.kind === kind && active?.key === option.key ? " on" : ""
                    }`}
                    onMouseEnter={() =>
                      setCursor(walkable.findIndex((item) => item.kind === kind && item.key === option.key))
                    }
                    onClick={() => add({ ...option, kind })}
                  >
                    <span>{option.label}</span>
                    <span className="leads-location-count">{option.count}</span>
                  </button>
                ))}
                {matches.length > VISIBLE_PER_GROUP ? (
                  // Say what is being held back rather than truncating quietly:
                  // an unlisted town looks like a town with no leads.
                  <p className="leads-location-more">
                    {matches.length - VISIBLE_PER_GROUP} more — keep typing to narrow.
                  </p>
                ) : null}
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
