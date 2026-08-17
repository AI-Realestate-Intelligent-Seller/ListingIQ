import Link from "next/link";

import { Brand } from "@/components/brand/brand";
import { ArrowIcon } from "@/components/icons/arrow-icon";

import { NAVIGATION_ITEMS } from "../data/landing-page-data";

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="nav-wrap">
        <Brand />

        <nav className="desktop-nav" aria-label="Primary navigation">
          {NAVIGATION_ITEMS.map((item) => (
            <a key={item.href} href={item.href}>
              {item.label}
            </a>
          ))}
        </nav>

        <div className="nav-actions">
          <Link className="login-link" href="/login">
            Log in
          </Link>
          <Link className="button button-small" href="/register">
            Get started
            <ArrowIcon />
          </Link>
        </div>

        <details className="mobile-nav">
          <summary aria-label="Open navigation">
            <span />
            <span />
          </summary>
          <div className="mobile-nav-panel">
            {NAVIGATION_ITEMS.map((item) => (
              <a key={item.href} href={item.href}>
                {item.label}
              </a>
            ))}
            <Link href="/login">Log in</Link>
            <Link className="button" href="/register">
              Get started
            </Link>
          </div>
        </details>
      </div>
    </header>
  );
}
