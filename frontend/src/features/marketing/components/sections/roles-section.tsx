import { CheckIcon } from "@/components/icons/check-icon";

import { SectionLabel } from "../section-label";

export function RolesSection() {
  return (
    <section className="roles section">
      <div className="roles-head">
        <SectionLabel number="05">Built for the whole brokerage</SectionLabel>
        <h2>
          One platform.
          <br />
          <em>The right view for every role.</em>
        </h2>
      </div>

      <div className="role-grid">
        <article className="role-card dark-role">
          <span className="role-number">BROKERAGE / 01</span>
          <h3>Lead with visibility.</h3>
          <p>
            See every lead, set access, assign opportunities, and understand
            follow-up across the brokerage.
          </p>
          <ul>
            <li>
              <CheckIcon /> Flexible team permissions
            </li>
            <li>
              <CheckIcon /> Lead and appointment oversight
            </li>
            <li>
              <CheckIcon /> Performance visibility
            </li>
          </ul>
          <div className="role-mini-ui">
            <span className="role-avatar">BF</span>
            <div>
              <strong>Brokerage overview</strong>
              <small>42 active opportunities</small>
            </div>
            <b>↗</b>
          </div>
        </article>

        <article className="role-card light-role">
          <span className="role-number">AGENT / 02</span>
          <h3>Focus on conversations.</h3>
          <p>
            See assigned opportunities, understand each seller, and move
            confidently through the next best actions.
          </p>
          <ul>
            <li>
              <CheckIcon /> Clean daily priority list
            </li>
            <li>
              <CheckIcon /> Complete seller context
            </li>
            <li>
              <CheckIcon /> Simple follow-up actions
            </li>
          </ul>
          <div className="role-mini-ui white">
            <span className="role-avatar blue">JL</span>
            <div>
              <strong>Your next opportunity</strong>
              <small>Call Elena · 2:00 PM</small>
            </div>
            <b>↗</b>
          </div>
        </article>
      </div>
    </section>
  );
}
