/* ─── Lead Intelligence Card SVG ────────────────────────────────────────────
   Used in: why-us-section
   A single, realistic ListingIQ lead card. Premium product-feel.
────────────────────────────────────────────────────────────────────────────── */
export function LeadIntelligenceCard() {
  const G      = "#1f5c4d";
  const GSOFT  = "#e4eeea";
  const STROKE = "#e4e4de";
  const TEXT   = "#111111";
  const MUTED  = "#8a9a95";
  const WHITE  = "#ffffff";

  return (
    <svg
      viewBox="0 0 440 380"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="ListingIQ lead intelligence card for Sarah Mitchell, ready for agent handoff"
      className="lp-why-svg"
    >
      {/* Outer card frame */}
      <rect x="20" y="20" width="400" height="340" rx="10" fill={WHITE} stroke={STROKE} strokeWidth="1" />

      {/* Top bar — card header */}
      <rect x="20" y="20" width="400" height="48" rx="10" fill={G} />
      <rect x="20" y="40" width="400" height="28" fill={G} />
      <text x="40" y="48" fontSize="11" fontWeight="700" fill={WHITE} fontFamily="system-ui, sans-serif" letterSpacing="0.06em">LEAD INTELLIGENCE</text>
      <circle cx="388" cy="44" r="4" fill="#7fd0bd" />

      {/* Score badge */}
      <rect x="40" y="84" width="56" height="56" rx="8" fill={G} />
      <text x="68" y="108" textAnchor="middle" dominantBaseline="middle" fontSize="22" fontWeight="900" fill={WHITE} fontFamily="system-ui, sans-serif" letterSpacing="-1">94</text>
      <text x="68" y="128" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="rgba(255,255,255,0.6)" fontFamily="system-ui, sans-serif" letterSpacing="0.06em">SCORE</text>

      {/* Name & property */}
      <text x="112" y="104" fontSize="18" fontWeight="800" fill={TEXT} fontFamily="system-ui, sans-serif" letterSpacing="-0.5">Sarah Mitchell</text>
      <text x="112" y="122" fontSize="12" fontWeight="500" fill={MUTED} fontFamily="system-ui, sans-serif">3BR · Austin, TX</text>

      {/* Divider */}
      <line x1="40" y1="156" x2="400" y2="156" stroke={STROKE} strokeWidth="1" />

      {/* Detail row — Budget */}
      <text x="40" y="182" fontSize="9" fontWeight="700" fill={MUTED} fontFamily="system-ui, sans-serif" letterSpacing="0.08em">BUDGET</text>
      <text x="40" y="198" fontSize="14" fontWeight="700" fill={TEXT} fontFamily="system-ui, sans-serif">$650,000</text>

      {/* Detail row — Timeline */}
      <text x="160" y="182" fontSize="9" fontWeight="700" fill={MUTED} fontFamily="system-ui, sans-serif" letterSpacing="0.08em">TIMELINE</text>
      <text x="160" y="198" fontSize="14" fontWeight="700" fill={TEXT} fontFamily="system-ui, sans-serif">30–60 days</text>

      {/* Detail row — Intent */}
      <text x="280" y="182" fontSize="9" fontWeight="700" fill={MUTED} fontFamily="system-ui, sans-serif" letterSpacing="0.08em">INTENT</text>
      <rect x="280" y="188" width="44" height="18" rx="4" fill="#dcfce7" />
      <text x="302" y="199" textAnchor="middle" dominantBaseline="middle" fontSize="10" fontWeight="700" fill="#166534" fontFamily="system-ui, sans-serif">High</text>

      {/* Divider */}
      <line x1="40" y1="220" x2="400" y2="220" stroke={STROKE} strokeWidth="1" />

      {/* Quote bubble */}
      <rect x="40" y="236" width="360" height="52" rx="6" fill={GSOFT} />
      <text x="56" y="256" fontSize="10" fontWeight="600" fill={G} fontFamily="system-ui, sans-serif">Latest reply</text>
      <text x="56" y="274" fontSize="12" fontWeight="500" fill={TEXT} fontFamily="system-ui, sans-serif">&ldquo;I&apos;d like to see it this weekend.&rdquo;</text>

      {/* Status bar */}
      <rect x="40" y="306" width="360" height="38" rx="6" fill={GSOFT} />
      <circle cx="60" cy="325" r="5" fill={G} />
      <text x="76" y="328" fontSize="11" fontWeight="700" fill={G} fontFamily="system-ui, sans-serif">READY FOR AGENT</text>
      <text x="380" y="328" fontSize="9" fontWeight="600" fill={MUTED} fontFamily="system-ui, sans-serif" textAnchor="end">Handoff pending</text>
    </svg>
  );
}
