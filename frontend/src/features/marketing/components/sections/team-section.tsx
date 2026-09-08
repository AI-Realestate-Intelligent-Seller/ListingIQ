/* Team management — SVG left, copy right. */
import { DashboardIllustration } from "../../data/svg-components/DashboardIllustration";

export function TeamSection() {
  return (
    <section className="lp-team" id="team">
      <div className="lp-team-inner">

        {/* Left — SVG illustration */}
        <div className="lp-team-visual">
          <DashboardIllustration />
        </div>

        {/* Right — copy */}
        <div className="lp-team-copy">
          <p className="lp-eyebrow lp-eyebrow-dark">Team management</p>
          <h2>Your whole brokerage,<br />one platform.</h2>
          <p>
            Invite brokers and agents in seconds. Every member sees only
            what they own  their leads, their threads, their appointments.
            The head of brokerage controls who&apos;s in and what they can do.
          </p>

          <div className="lp-team-roles">
            <div className="lp-role-pill lp-role-hob">
              <strong>Head of Brokerage</strong>
              <span>Invite members, assign opportunities, see the full pipeline.</span>
            </div>
            <div className="lp-role-pill lp-role-broker">
              <strong>Broker</strong>
              <span>Import leads, launch campaigns, take over AI threads.</span>
            </div>
            <div className="lp-role-pill lp-role-agent">
              <strong>Agent</strong>
              <span>Work the queue assigned to you. Full thread before every call.</span>
            </div>
          </div>

          <a href="/register" className="lp-btn lp-btn-green lp-team-cta">
            Set up your team
          </a>
        </div>

      </div>
    </section>
  );
}
