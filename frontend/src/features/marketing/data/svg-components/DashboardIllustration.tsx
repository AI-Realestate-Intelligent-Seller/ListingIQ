/* ─── Dashboard UI Illustration ─────────────────────────────────────────────
   Used in: team-section
   Flat, minimal product-feel SVG — team pipeline view with lead rows,
   agent avatars, status badges, and an SMS conversation snippet.
────────────────────────────────────────────────────────────────────────────── */
export function DashboardIllustration() {
  const G      = "#1f5c4d";
  const GSOFT  = "#e4eeea";
  const GMID   = "#4a8c7a";
  const STROKE = "#e4e4de";
  const TEXT   = "#111111";
  const MUTED  = "#8a9a95";
  const WHITE  = "#ffffff";

  return (
    <svg
      viewBox="0 0 440 380"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="ListingIQ team dashboard illustration"
      role="img"
      style={{ width: "100%", height: "100%", display: "block" }}
    >
      {/* ── Outer card ── */}
      <rect x="8" y="8" width="424" height="364" rx="10" fill={WHITE} stroke={STROKE} strokeWidth="1" />

      {/* ── Sidebar ── */}
      <rect x="8" y="8" width="44" height="364" rx="10" fill={G} />
      <circle cx="30" cy="40"  r="5" fill="rgba(255,255,255,.9)" />
      <rect x="22" y="66"  width="16" height="3" rx="1.5" fill="rgba(255,255,255,.35)" />
      <rect x="22" y="78"  width="16" height="3" rx="1.5" fill="rgba(255,255,255,.25)" />
      <rect x="22" y="90"  width="16" height="3" rx="1.5" fill="rgba(255,255,255,.25)" />
      <rect x="22" y="102" width="16" height="3" rx="1.5" fill="rgba(255,255,255,.25)" />
      <circle cx="30" cy="340" r="10" fill="rgba(255,255,255,.15)" />
      <text x="30" y="340" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">MK</text>

      {/* ── Header row ── */}
      <text x="68" y="34" fontSize="11" fontWeight="700" fill={TEXT} fontFamily="system-ui,sans-serif" letterSpacing="-0.2">Team Pipeline</text>
      <text x="68" y="48" fontSize="9"  fill={MUTED} fontFamily="system-ui,sans-serif">3 agents active · 12 leads assigned this week</text>
      <rect x="346" y="24" width="74" height="22" rx="4" fill={G} />
      <text x="383" y="35" textAnchor="middle" dominantBaseline="middle" fontSize="9" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">+ Assign lead</text>
      <line x1="52" y1="58" x2="432" y2="58" stroke={STROKE} strokeWidth="1" />

      {/* ── Column headers ── */}
      <text x="68"  y="74" fontSize="8" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.08em">LEAD</text>
      <text x="220" y="74" fontSize="8" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.08em">SIGNAL</text>
      <text x="295" y="74" fontSize="8" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.08em">ASSIGNED TO</text>
      <text x="390" y="74" fontSize="8" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.08em">STATUS</text>

      {/* ── Lead row 1 ── */}
      <rect x="52" y="80" width="380" height="44" rx="6" fill={GSOFT} />
      <rect x="60" y="90" width="26" height="24" rx="4" fill={G} />
      <text x="73" y="102" textAnchor="middle" dominantBaseline="middle" fontSize="10" fontWeight="800" fill={WHITE} fontFamily="system-ui,sans-serif">94</text>
      <text x="96" y="97"  fontSize="10" fontWeight="700" fill={TEXT}  fontFamily="system-ui,sans-serif">2147 N Oakley Ave</text>
      <text x="96" y="110" fontSize="8"  fill={MUTED} fontFamily="system-ui,sans-serif">Elena Park · Chicago, IL</text>
      <rect x="212" y="91" width="52" height="16" rx="3" fill="#fff3e0" />
      <text x="238" y="99" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="#b45309" fontFamily="system-ui,sans-serif">High equity</text>
      <circle cx="308" cy="102" r="10" fill={G} />
      <text x="308" y="102" textAnchor="middle" dominantBaseline="middle" fontSize="7" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">JL</text>
      <text x="322" y="102" fontSize="9" fill={TEXT} dominantBaseline="middle" fontFamily="system-ui,sans-serif">Jordan Lee</text>
      <rect x="386" y="92" width="40" height="16" rx="3" fill="#dcfce7" />
      <text x="406" y="100" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="#166534" fontFamily="system-ui,sans-serif">Replied</text>

      {/* ── Lead row 2 ── */}
      <rect x="52" y="130" width="380" height="44" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <rect x="60" y="140" width="26" height="24" rx="4" fill={GMID} />
      <text x="73" y="152" textAnchor="middle" dominantBaseline="middle" fontSize="10" fontWeight="800" fill={WHITE} fontFamily="system-ui,sans-serif">88</text>
      <text x="96" y="147" fontSize="10" fontWeight="700" fill={TEXT}  fontFamily="system-ui,sans-serif">4821 W Byron St</text>
      <text x="96" y="160" fontSize="8"  fill={MUTED} fontFamily="system-ui,sans-serif">Daniel Reed · Chicago, IL</text>
      <rect x="212" y="141" width="42" height="16" rx="3" fill="#eff6ff" />
      <text x="233" y="149" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="#1d4ed8" fontFamily="system-ui,sans-serif">Expired</text>
      <circle cx="308" cy="152" r="10" fill={GMID} />
      <text x="308" y="152" textAnchor="middle" dominantBaseline="middle" fontSize="7" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">AR</text>
      <text x="322" y="152" fontSize="9" fill={TEXT} dominantBaseline="middle" fontFamily="system-ui,sans-serif">Aisha Rowe</text>
      <rect x="382" y="142" width="48" height="16" rx="3" fill="#fef9c3" />
      <text x="406" y="150" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="#854d0e" fontFamily="system-ui,sans-serif">Follow-up</text>

      {/* ── Lead row 3 ── */}
      <rect x="52" y="180" width="380" height="44" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <rect x="60" y="190" width="26" height="24" rx="4" fill="#8ab8ac" />
      <text x="73" y="202" textAnchor="middle" dominantBaseline="middle" fontSize="10" fontWeight="800" fill={WHITE} fontFamily="system-ui,sans-serif">83</text>
      <text x="96" y="197" fontSize="10" fontWeight="700" fill={TEXT}  fontFamily="system-ui,sans-serif">915 S Claremont Ave</text>
      <text x="96" y="210" fontSize="8"  fill={MUTED} fontFamily="system-ui,sans-serif">Nina Shah · Chicago, IL</text>
      <rect x="212" y="191" width="48" height="16" rx="3" fill="#f5f3ff" />
      <text x="236" y="199" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="#6d28d9" fontFamily="system-ui,sans-serif">Absentee</text>
      <circle cx="308" cy="202" r="10" fill="#8ab8ac" />
      <text x="308" y="202" textAnchor="middle" dominantBaseline="middle" fontSize="7" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">TM</text>
      <text x="322" y="202" fontSize="9" fill={TEXT} dominantBaseline="middle" fontFamily="system-ui,sans-serif">Tom Morris</text>
      <rect x="386" y="192" width="40" height="16" rx="3" fill="#dcfce7" />
      <text x="406" y="200" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="#166534" fontFamily="system-ui,sans-serif">Booked</text>

      {/* ── Divider ── */}
      <line x1="52" y1="234" x2="432" y2="234" stroke={STROKE} strokeWidth="1" strokeDasharray="4 3" />

      {/* ── SMS snippet ── */}
      <text x="68" y="250" fontSize="9" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.06em">LATEST CONVERSATION · Jordan Lee</text>

      {/* AI bubble */}
      <rect x="68" y="260" width="200" height="30" rx="6" fill={GSOFT} />
      <text x="80" y="271" fontSize="8.5" fill={G} fontWeight="600" fontFamily="system-ui,sans-serif">AI</text>
      <text x="96" y="271" fontSize="8.5" fill={TEXT} fontFamily="system-ui,sans-serif">&quot;Hi Elena, I came across your property</text>
      <text x="80" y="283" fontSize="8.5" fill={TEXT} fontFamily="system-ui,sans-serif">at Oakley Ave — would you consider an offer?&quot;</text>

      {/* Owner reply bubble */}
      <rect x="168" y="298" width="192" height="22" rx="6" fill={G} />
      <text x="264" y="309" textAnchor="middle" dominantBaseline="middle" fontSize="8.5" fill={WHITE} fontFamily="system-ui,sans-serif">&quot;Yes, I&apos;d be open to hearing more.&quot;</text>

      {/* Handoff badge */}
      <rect x="68" y="328" width="130" height="20" rx="4" fill={GSOFT} />
      <circle cx="81" cy="338" r="4" fill={G} />
      <text x="91" y="338" dominantBaseline="middle" fontSize="8" fontWeight="600" fill={G} fontFamily="system-ui,sans-serif">Handed off to Jordan Lee</text>
    </svg>
  );
}
