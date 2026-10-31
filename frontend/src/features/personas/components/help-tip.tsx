"use client";

import { useId, useRef, useState } from "react";

export type Help = { what: string; enter: string; why: string; example?: string };

/**
 * A small "?" button that explains a field. It opens on click or tap, on
 * keyboard focus, and with Enter or Space; Escape, blur or a second click
 * closes it. Styled like ListingIQ's existing info tip.
 */
export function HelpTip({ label, help }: { label: string; help: Help }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const pointer = useRef(false);
  return (
    <span className="persona-help">
      <button
        type="button"
        className="persona-help-button"
        aria-label={`Help: ${label}`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onPointerDown={() => { pointer.current = true; }}
        onFocus={() => { if (!pointer.current) setOpen(true); }}
        onClick={() => { pointer.current = false; setOpen((value) => !value); }}
        onBlur={() => { pointer.current = false; setOpen(false); }}
        onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); setOpen(false); } }}
      >
        ?
      </button>
      {open ? (
        <span id={id} role="tooltip" className="persona-help-bubble">
          <span><b>What it means:</b> {help.what}</span>
          <span><b>What to enter:</b> {help.enter}</span>
          <span><b>Why it matters:</b> {help.why}</span>
          {help.example ? <span><b>Example:</b> {help.example}</span> : null}
        </span>
      ) : null}
    </span>
  );
}
