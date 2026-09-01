/* ─── Product Dashboard SVG ──────────────────────────────────────────────────
   
   Four-card dashboard: Leads, AI Activity, Calendar, Persona Context.
   Flat, minimal, product-feel. Same palette as DashboardIllustration.
────────────────────────────────────────────────────────────────────────────── */
export function ProductDashboard() {
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
      role="img"
      aria-label="ListingIQ product dashboard preview showing leads, AI conversation, calendar, and team controls"
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

      {/* ── Top header ── */}
      <text x="68" y="32" fontSize="11" fontWeight="700" fill={TEXT} fontFamily="system-ui,sans-serif" letterSpacing="-0.2">ListingIQ</text>
      <circle cx="412" cy="24" r="10" fill={GSOFT} />
      <text x="412" y="24" textAnchor="middle" dominantBaseline="middle" fontSize="7" fontWeight="700" fill={G} fontFamily="system-ui,sans-serif">MK</text>
      <line x1="52" y1="44" x2="432" y2="44" stroke={STROKE} strokeWidth="1" />

      {/* ═══ CARD 1 — Lead Management (top-left) ═══ */}
      <rect x="64" y="56" width="176" height="152" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <text x="76" y="74" fontSize="9" fontWeight="700" fill={TEXT} fontFamily="system-ui,sans-serif">Leads</text>
      <rect x="212" y="64" width="18" height="14" rx="3" fill={GSOFT} />
      <text x="221" y="73" textAnchor="middle" dominantBaseline="middle" fontSize="9" fontWeight="700" fill={G} fontFamily="system-ui,sans-serif">+</text>

      {/* Lead row 1 */}
      <rect x="72" y="84" width="26" height="20" rx="4" fill={G} />
      <text x="85" y="97" textAnchor="middle" dominantBaseline="middle" fontSize="9" fontWeight="800" fill={WHITE} fontFamily="system-ui,sans-serif">94</text>
      <text x="104" y="92"  fontSize="9" fontWeight="700" fill={TEXT}  fontFamily="system-ui,sans-serif">2147 N Oakley Ave</text>
      <text x="104" y="102" fontSize="7.5" fill={MUTED} fontFamily="system-ui,sans-serif">Elena Park · Chicago, IL</text>
      <rect x="72" y="110" width="46" height="14" rx="2" fill="#fff3e0" />
      <text x="95" y="119" textAnchor="middle" dominantBaseline="middle" fontSize="6.5" fontWeight="600" fill="#b45309" fontFamily="system-ui,sans-serif">High equity</text>
      <rect x="122" y="110" width="36" height="14" rx="2" fill="#dcfce7" />
      <text x="140" y="119" textAnchor="middle" dominantBaseline="middle" fontSize="6.5" fontWeight="600" fill="#166534" fontFamily="system-ui,sans-serif">Replied</text>
      <circle cx="222" cy="104" r="8" fill={G} />
      <text x="222" y="104" textAnchor="middle" dominantBaseline="middle" fontSize="7" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">JL</text>
      <line x1="72" y1="132" x2="232" y2="132" stroke={STROKE} strokeWidth="1" />

      {/* Lead row 2 */}
      <rect x="72" y="140" width="26" height="20" rx="4" fill={GMID} />
      <text x="85" y="153" textAnchor="middle" dominantBaseline="middle" fontSize="9" fontWeight="800" fill={WHITE} fontFamily="system-ui,sans-serif">88</text>
      <text x="104" y="148" fontSize="9" fontWeight="700" fill={TEXT}  fontFamily="system-ui,sans-serif">4821 W Byron St</text>
      <text x="104" y="158" fontSize="7.5" fill={MUTED} fontFamily="system-ui,sans-serif">Daniel Reed · Chicago, IL</text>
      <rect x="72" y="166" width="38" height="14" rx="2" fill="#eff6ff" />
      <text x="91" y="175" textAnchor="middle" dominantBaseline="middle" fontSize="6.5" fontWeight="600" fill="#1d4ed8" fontFamily="system-ui,sans-serif">Expired</text>
      <rect x="114" y="166" width="42" height="14" rx="2" fill="#fef9c3" />
      <text x="135" y="175" textAnchor="middle" dominantBaseline="middle" fontSize="6.5" fontWeight="600" fill="#854d0e" fontFamily="system-ui,sans-serif">Follow-up</text>
      <circle cx="222" cy="160" r="8" fill={GMID} />
      <text x="222" y="160" textAnchor="middle" dominantBaseline="middle" fontSize="7" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">AR</text>

      {/* ═══ CARD 2 — AI Activity (top-right) ═══ */}
      <rect x="256" y="56" width="176" height="152" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <text x="268" y="74" fontSize="9" fontWeight="700" fill={TEXT} fontFamily="system-ui,sans-serif">AI Activity</text>
      <circle cx="416" cy="70" r="3" fill={G} />
      <rect x="268" y="84" width="120" height="32" rx="5" fill={GSOFT} />
      <text x="278" y="94" fontSize="7.5" fontWeight="700" fill={G} fontFamily="system-ui,sans-serif">AI</text>
      <text x="294" y="94" fontSize="8" fill={TEXT} fontFamily="system-ui,sans-serif">&quot;Hi Elena, would you</text>
      <text x="278" y="106" fontSize="8" fill={TEXT} fontFamily="system-ui,sans-serif">consider an offer?&quot;</text>
      <rect x="312" y="122" width="108" height="22" rx="5" fill={G} />
      <text x="366" y="136" textAnchor="middle" dominantBaseline="middle" fontSize="8" fill={WHITE} fontFamily="system-ui,sans-serif">&quot;Yes, I&apos;d be open to it.&quot;</text>
      <rect x="268" y="154" width="96" height="16" rx="3" fill={GSOFT} />
      <path d="M274,158 Q278,154 282,158" fill="none" stroke={G} strokeWidth="1" strokeLinecap="round" />
      <path d="M276,160 Q278,157 280,160" fill="none" stroke={G} strokeWidth="1" strokeLinecap="round" />
      <text x="286" y="164" fontSize="7" fontWeight="600" fill={G} fontFamily="system-ui,sans-serif">Voice call scheduled</text>
      <rect x="268" y="176" width="84" height="16" rx="3" fill="#dcfce7" />
      <circle cx="276" cy="184" r="2" fill="#166534" />
      <text x="284" y="186" fontSize="7" fontWeight="600" fill="#166534" fontFamily="system-ui,sans-serif">AI Cloning active</text>

      {/* ═══ CARD 3 — Calendar (bottom-left) ═══ */}
      <rect x="64" y="220" width="176" height="140" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <text x="76" y="238" fontSize="9" fontWeight="700" fill={TEXT} fontFamily="system-ui,sans-serif">Calendar</text>
      <text x="76" y="254" fontSize="7" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.06em">S  M  T  W  T  F  S</text>
      <text x="76" y="268" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">1</text>
      <text x="96" y="268" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">2</text>
      <text x="116" y="268" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">3</text>
      <text x="136" y="268" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">4</text>
      <text x="156" y="268" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">5</text>
      <text x="176" y="268" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">6</text>
      <text x="196" y="268" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">7</text>
      <text x="76" y="282" fontSize="7" fill={TEXT} fontFamily="system-ui,sans-serif">8</text>
      <text x="96" y="282" fontSize="7" fill={TEXT} fontFamily="system-ui,sans-serif">9</text>
      <text x="114" y="282" fontSize="7" fill={TEXT} fontFamily="system-ui,sans-serif">10</text>
      <text x="134" y="282" fontSize="7" fill={TEXT} fontFamily="system-ui,sans-serif">11</text>
      <text x="154" y="282" fontSize="7" fill={TEXT} fontFamily="system-ui,sans-serif">12</text>
      <circle cx="176" cy="278" r="8" fill={G} />
      <text x="176" y="282" textAnchor="middle" dominantBaseline="middle" fontSize="7" fontWeight="700" fill={WHITE} fontFamily="system-ui,sans-serif">13</text>
      <text x="196" y="282" fontSize="7" fill={TEXT} fontFamily="system-ui,sans-serif">14</text>
      <line x1="76" y1="296" x2="232" y2="296" stroke={STROKE} strokeWidth="1" strokeDasharray="3 2" />
      <circle cx="82" cy="310" r="3" fill={G} />
      <text x="92" y="312" fontSize="8" fontWeight="600" fill={TEXT} fontFamily="system-ui,sans-serif">Today · 3:30 PM</text>
      <text x="92" y="322" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">Oakley Ave — Jordan Lee</text>
      <rect x="76" y="334" width="72" height="16" rx="3" fill={GSOFT} />
      <path d="M82,338 h6 M85,336 v4" fill="none" stroke={G} strokeWidth="1" strokeLinecap="round" />
      <text x="94" y="344" fontSize="7" fontWeight="600" fill={G} fontFamily="system-ui,sans-serif">Synced with GCal</text>

      {/* ═══ CARD 4 — Persona Context (bottom-right) ═══ */}
      <rect x="256" y="220" width="176" height="140" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <text x="268" y="238" fontSize="9" fontWeight="700" fill={TEXT} fontFamily="system-ui,sans-serif">Persona Context</text>
      <text x="268" y="270" fontSize="28" fontWeight="900" fill={G} fontFamily="system-ui,sans-serif" letterSpacing="-1">94</text>
      <text x="268" y="282" fontSize="7" fill={MUTED} fontFamily="system-ui,sans-serif">Lead score</text>
      <rect x="268" y="290" width="60" height="4" rx="2" fill={GSOFT} />
      <rect x="268" y="290" width="52" height="4" rx="2" fill={G} />
      <rect x="268" y="304" width="50" height="14" rx="2" fill="#f5f3ff" />
      <text x="293" y="313" textAnchor="middle" dominantBaseline="middle" fontSize="6.5" fontWeight="600" fill="#6d28d9" fontFamily="system-ui,sans-serif">Absentee</text>
      <rect x="324" y="304" width="56" height="14" rx="2" fill="#fff3e0" />
      <text x="352" y="313" textAnchor="middle" dominantBaseline="middle" fontSize="6.5" fontWeight="600" fill="#b45309" fontFamily="system-ui,sans-serif">High equity</text>
      <rect x="268" y="324" width="68" height="14" rx="2" fill="#fef2f2" />
      <text x="302" y="333" textAnchor="middle" dominantBaseline="middle" fontSize="6.5" fontWeight="600" fill="#991b1b" fontFamily="system-ui,sans-serif">Pre-foreclosure</text>
      <rect x="268" y="344" width="96" height="16" rx="3" fill={GSOFT} />
      <path d="M274,348 L276,352 L280,352" fill="none" stroke={G} strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
      <text x="286" y="354" fontSize="7" fontWeight="600" fill={G} fontFamily="system-ui,sans-serif">Head of Brokerage</text>
    </svg>
  );
}
