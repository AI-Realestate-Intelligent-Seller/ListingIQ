import Link from "next/link";

import { Brand } from "@/components/brand/brand";

export function SiteFooter() {
  return (
    <footer>
      <div className="footer-main">
        <div>
          <Brand />
          <p>AI seller intelligence for modern brokerages.</p>
        </div>

        <div className="footer-links">
          <div>
            <strong>Product</strong>
            <a href="#product">Overview</a>
            <a href="#workflow">How it works</a>
            <a href="#security">Security</a>
          </div>
          <div>
            <strong>Company</strong>
            <a href="mailto:hello@ListingIQ.ai">Contact</a>
            <a href="#">Privacy</a>
            <a href="#">Terms</a>
          </div>
          <div>
            <strong>Access</strong>
            <Link href="/login">Log in</Link>
            <Link href="/register">Create account</Link>
          </div>
        </div>
      </div>

      <div className="footer-bottom">
        <span>© 2026 ListingIQ. All rights reserved.</span>
        <span>Built for better seller conversations.</span>
      </div>
    </footer>
  );
}
