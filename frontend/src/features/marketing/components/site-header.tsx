"use client";

import Link from "next/link";
import { useState } from "react";

import { Brand } from "@/components/brand/brand";

export function SiteHeader() {
  const [menuOpen, setMenuOpen] = useState(false);

  const scrollTo = (id: string) => {
    setMenuOpen(false);
    setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  };

  return (
    <header className="lp-nav">
      <div className="lp-nav-inner">
        {/* Logo — always visible */}
        <Brand />

        {/* Desktop nav links */}
        <nav className="lp-nav-links" aria-label="Primary">
          <button onClick={() => scrollTo("features")}>Features</button>
          <button onClick={() => scrollTo("why-listingiq")}>Why us</button>
        </nav>

        {/* Desktop actions */}
        <div className="lp-nav-actions">
          <Link href="/login" className="lp-nav-login">Log in</Link>
          <Link href="/register" className="lp-btn lp-btn-sm lp-btn-green">
            Register
          </Link>
        </div>

        {/* Hamburger — mobile only */}
        <button
          className="lp-nav-hamburger"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
        >
          <span className={`lp-ham-line${menuOpen ? " lp-ham-open" : ""}`} />
          <span className={`lp-ham-line${menuOpen ? " lp-ham-open" : ""}`} />
          <span className={`lp-ham-line${menuOpen ? " lp-ham-open" : ""}`} />
        </button>
      </div>

      {/* Mobile drawer */}
      {menuOpen && (
        <div className="lp-mobile-menu">
          <button onClick={() => scrollTo("features")}>Features</button>
          <button onClick={() => scrollTo("why-listingiq")}>Why us</button>
          <div className="lp-mobile-sep" />
          <Link href="/login" onClick={() => setMenuOpen(false)}>Log in</Link>
          <Link
            href="/register"
            className="lp-btn lp-btn-green lp-mobile-cta"
            onClick={() => setMenuOpen(false)}
          >
            Get early access
          </Link>
        </div>
      )}
    </header>
  );
}
