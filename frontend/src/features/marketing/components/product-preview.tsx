import {
  PREVIEW_LEADS,
  PREVIEW_METRICS,
} from "../data/landing-page-data";

export function ProductPreview() {
  return (
    <div
      className="product-stage"
      aria-label="ListingIQ product dashboard preview"
    >
      <div className="stage-glow" />

      <div className="dashboard-card">
        <DashboardSidebar />

        <div className="dash-main">
          <div className="dash-top">
            <div>
              <span className="dash-kicker">OPPORTUNITIES</span>
              <h3>Good morning, Michael.</h3>
            </div>
            <button type="button" aria-label="Add opportunity">
              +
            </button>
          </div>

          <div className="metric-row">
            {PREVIEW_METRICS.map((metric) => (
              <div key={metric.label}>
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
                <small>{metric.detail}</small>
              </div>
            ))}
          </div>

          <div className="opportunity-head">
            <h4>Seller opportunities</h4>
            <span>
              Priority first <b>⌄</b>
            </span>
          </div>

          <div className="lead-list">
            {PREVIEW_LEADS.map((lead) => (
              <div
                className={`lead-row${lead.featured ? " featured" : ""}`}
                key={lead.address}
              >
                <span className="lead-score">{lead.score}</span>
                <div className="lead-address">
                  <strong>{lead.address}</strong>
                  <span>{lead.owner}</span>
                </div>
                <span className={`signal-tag ${lead.signalClassName}`}>
                  {lead.signal}
                </span>
                <span className={`status ${lead.statusClassName}`.trim()}>
                  <i /> {lead.status}
                </span>
                <span className="lead-arrow">↗</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="float-card float-card-one">
        <div className="float-icon">✓</div>
        <div>
          <strong>Appointment booked</strong>
          <span>Today · 3:30 PM</span>
        </div>
      </div>

      <div className="float-card float-card-two">
        <span className="wave-dot" />
        <div>
          <strong>Seller replied</strong>
          <span>“Yes, I’d consider an offer.”</span>
        </div>
      </div>

      <div className="float-card float-card-three">
        <div className="agent-stack">
          <span>JL</span>
          <span>AR</span>
        </div>
        <div>
          <strong>Lead assigned</strong>
          <span>Jordan Lee · North Side</span>
        </div>
      </div>
    </div>
  );
}

function DashboardSidebar() {
  return (
    <div className="dash-sidebar">
      <div className="mini-logo">
        <span />
        <span />
        <span />
      </div>
      <div className="side-pill active" />
      <div className="side-pill" />
      <div className="side-pill" />
      <div className="side-pill" />
      <div className="side-avatar">MK</div>
    </div>
  );
}
