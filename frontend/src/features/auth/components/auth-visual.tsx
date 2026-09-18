import Image from "next/image";

import { Brand } from "@/components/brand/brand";

import { AUTH_COPY } from "../constants/auth-copy";
import type { AuthMode } from "../types/auth.types";

type AuthVisualProps = {
  mode: AuthMode;
};

export function AuthVisual({ mode }: AuthVisualProps) {
  const copy = AUTH_COPY[mode];

  return (
    <section className="auth-visual">
      <Image
        src="/images/home-exterior.jpg"
        alt="Residential home at dusk"
        fill
        priority
        sizes="(max-width: 800px) 0vw, 48vw"
      />
      <div className="auth-image-wash" />

      <div className="auth-visual-inner">
        <Brand light />

        <div className="auth-quote">
          <span>FROM SIGNAL TO CONVERSATION</span>
          <h2>{copy.visualTitle}</h2>
          <p>{copy.visualDescription}</p>
        </div>

        <div className="auth-proof-card">
          <div className="auth-proof-top">
            <span className="live-dot" />
            <strong>ListingIQ pulse</strong>
            <small>Live</small>
          </div>
          <div className="auth-proof-row">
            <span>New high-intent signal</span>
            <strong>+12</strong>
          </div>
          <div className="auth-proof-row">
            <span>Qualified conversations</span>
            <strong>08</strong>
          </div>
          <div className="auth-proof-row">
            <span>Appointments booked</span>
            <strong>04</strong>
          </div>
        </div>

      </div>
    </section>
  );
}
