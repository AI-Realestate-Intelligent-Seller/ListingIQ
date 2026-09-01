"use client";

import Link from "next/link";

import { Brand } from "@/components/brand/brand";

export function SiteFooter() {
  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <footer className="lp-footer">
      <div className="lp-footer-inner">
        <div className="lp-footer-top">
          <div className="lp-footer-brand">
            <Brand />
            <p>AI listing intelligence for US brokerages.<br />Built to hand off, not take over.</p>
          </div>
          <div className="lp-footer-cols">
            <div className="lp-footer-col">
              <h5>Product</h5>
              <button onClick={() => scrollTo("features")}>Features</button>
              <button onClick={() => scrollTo("why-listingiq")}>Why us</button>
            </div>
            
            <div className="lp-footer-col">
              <h5>Account</h5>
              <Link href="/login">Log in</Link>
              <Link href="/register">Create account</Link>
            </div>
          </div>
        </div>
        <div className="lp-footer-bottom">
          <span>© 2026 ListingIQ. All rights reserved.</span>
        </div>
      </div>
    </footer>
  );
}
