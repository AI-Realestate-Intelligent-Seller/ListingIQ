"use client";

import { useEffect, useRef } from "react";

export type LeadRowMenuItem = {
  label: string;
  onSelect: () => void;
  tone?: "danger";
  disabled?: boolean;
  title?: string;
};

/**
 * The card's "More actions" menu. It only hosts the row actions the pool
 * already had (details, map, remove) and opens under the button that asked.
 */
export function LeadRowMenu({
  anchor,
  items,
  onClose,
}: {
  anchor: HTMLElement;
  items: LeadRowMenuItem[];
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const rect = anchor.getBoundingClientRect();

  useEffect(() => {
    menuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();

    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !anchor.contains(target)) onClose();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      onClose();
      anchor.focus();
    };
    // The menu is pinned to where the button was; any scroll would leave it behind.
    const closeOnScroll = () => onClose();

    window.addEventListener("pointerdown", closeOutside);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("scroll", closeOnScroll, true);
    window.addEventListener("resize", closeOnScroll);
    return () => {
      window.removeEventListener("pointerdown", closeOutside);
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("scroll", closeOnScroll, true);
      window.removeEventListener("resize", closeOnScroll);
    };
  }, [anchor, onClose]);

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label="Lead actions"
      style={{ top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right) }}
      className="fixed z-50 flex min-w-44 flex-col rounded-lg border border-gray-200 bg-white p-1 shadow-lg"
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          disabled={item.disabled}
          title={item.title}
          onClick={() => {
            onClose();
            item.onSelect();
          }}
          className={`rounded-md px-3 py-1.5 text-left transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-(--blue) disabled:cursor-not-allowed disabled:text-gray-300 disabled:hover:bg-transparent ${
            item.tone === "danger"
              ? "text-red-700 hover:bg-red-50"
              : "text-gray-700 hover:bg-(--accent-soft) hover:text-(--blue-dark)"
          }`}
        >
          <span className="text-[13px]">{item.label}</span>
        </button>
      ))}
    </div>
  );
}
