"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Brand } from "@/components/brand/brand";
import { clearAuthSession, readAuthSession } from "@/features/auth/lib/auth-storage";
import { endSession, markActivity, watchSession } from "@/features/auth/lib/session-guard";
import * as api from "../api";
import type { AuditEvent, Engineering, FeatureFlag, Operations, Organization, PlatformOverview, PlatformUser } from "../types";

type Tab = "overview" | "organizations" | "users" | "product" | "operations" | "support" | "security" | "engineering";
const nav: {id: Tab; label: string; group: string}[] = [
  {id:"overview",label:"Overview",group:"Workspace"}, {id:"organizations",label:"Organizations",group:"Customers"},
  {id:"users",label:"Users & access",group:"Customers"}, {id:"product",label:"Feature flags",group:"Product"},
  {id:"operations",label:"Operations",group:"Operations"}, {id:"support",label:"Customer lookup",group:"Support"},
  {id:"security",label:"Audit log",group:"Security"}, {id:"engineering",label:"Environments",group:"Engineering"},
];

export function PlatformAdminView() {
  const router = useRouter();
  const [tab,setTab]=useState<Tab>("overview"); const [loading,setLoading]=useState(true); const [error,setError]=useState("");
  const [overview,setOverview]=useState<PlatformOverview|null>(null); const [organizations,setOrganizations]=useState<Organization[]>([]);
  const [users,setUsers]=useState<PlatformUser[]>([]); const [flags,setFlags]=useState<FeatureFlag[]>([]); const [operations,setOperations]=useState<Operations|null>(null);
  const [events,setEvents]=useState<AuditEvent[]>([]); const [engineering,setEngineering]=useState<Engineering|null>(null); const [query,setQuery]=useState("");
  const [flagKey,setFlagKey]=useState(""); const [flagDescription,setFlagDescription]=useState(""); const [notice,setNotice]=useState("");

  async function load(activeTab=tab, search=query) {
    const session=readAuthSession();
    if (!session || session.user.role!=="platform_admin") { clearAuthSession(); router.replace("/login"); return; }
    setLoading(true); setError("");
    try {
      if(activeTab==="overview") setOverview(await api.getPlatformOverview(session.access_token));
      if(activeTab==="organizations") setOrganizations((await api.getOrganizations(session.access_token)).organizations);
      if(activeTab==="users"||activeTab==="support") setUsers((await api.getPlatformUsers(session.access_token,search)).users);
      if(activeTab==="product") setFlags((await api.getFeatureFlags(session.access_token)).flags);
      if(activeTab==="operations") setOperations(await api.getOperations(session.access_token));
      if(activeTab==="security") setEvents((await api.getAuditLog(session.access_token)).events);
      if(activeTab==="engineering") setEngineering(await api.getEngineering(session.access_token));
    } catch(reason) { setError(reason instanceof Error?reason.message:"Could not load platform data."); } finally { setLoading(false); }
  }

  useEffect(()=>{
    const session=readAuthSession();
    if(!session){router.replace("/login");return;}
    if(session.user.role!=="platform_admin"){
      router.replace(`/dashboard/${session.user.role}`);
      return;
    }
    markActivity();
    void load("overview","");
    return watchSession(()=>{clearAuthSession();router.replace("/login?expired=1")});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[router]);

  function choose(next:Tab){setTab(next);setNotice("");void load(next,query)}
  async function toggleOrganization(item:Organization){const session=readAuthSession();if(!session)return;const reason=window.prompt(`Reason to ${item.is_active?"suspend":"activate"} ${item.name}:`);if(!reason)return;await api.setOrganizationActive(session.access_token,item.id,!item.is_active,reason);setNotice(`${item.name} updated.`);await load("organizations",query)}
  async function toggleUser(item:PlatformUser){const session=readAuthSession();if(!session)return;const reason=window.prompt(`Reason to ${item.is_active?"suspend":"activate"} ${item.email}:`);if(!reason)return;await api.setUserActive(session.access_token,item.id,!item.is_active,reason);setNotice(`${item.email} updated.`);await load(tab,query)}
  async function addFlag(event:React.FormEvent){event.preventDefault();const session=readAuthSession();if(!session)return;await api.saveFeatureFlag(session.access_token,{key:flagKey,description:flagDescription,enabled:false,brokerage_id:null});setFlagKey("");setFlagDescription("");setNotice("Feature flag created.");await load("product",query)}
  async function toggleFlag(flag:FeatureFlag){const session=readAuthSession();if(!session)return;await api.saveFeatureFlag(session.access_token,{key:flag.key,description:flag.description||"",enabled:!flag.enabled,brokerage_id:flag.brokerage_id});setNotice(`${flag.key} updated.`);await load("product",query)}
  function logout(){endSession();clearAuthSession();router.replace("/login")}

  return <main className="platform-shell">
    <aside className="platform-sidebar"><Brand/><div className="platform-badge">INTERNAL CONSOLE</div><nav>{[...new Set(nav.map(i=>i.group))].map(group=><section key={group}><span>{group}</span>{nav.filter(i=>i.group===group).map(i=><button key={i.id} className={tab===i.id?"active":""} onClick={()=>choose(i.id)}>{i.label}</button>)}</section>)}</nav><button className="platform-logout" onClick={logout}>Sign out</button></aside>
    <section className="platform-main"><header><div><span>LISTINGIQ PLATFORM</span><h1>{nav.find(i=>i.id===tab)?.label}</h1><p>Internal product and customer operations.</p></div><button className="button secondary" onClick={()=>void load()}>Refresh</button></header>
      {notice?<div className="platform-notice">{notice}</div>:null}{error?<div className="platform-error" role="alert">{error}</div>:null}{loading?<div className="platform-loading">Loading platform data…</div>:null}
      {!loading&&!error&&tab==="overview"&&overview?<><div className="platform-metrics">{[["Organizations",overview.organizations],["Active customers",overview.active_organizations],["Customer users",overview.users],["Total leads",overview.leads],["Campaigns",overview.campaigns],["Messages",overview.messages]].map(([l,v])=><article key={l}><span>{l}</span><strong>{Number(v).toLocaleString()}</strong></article>)}</div><div className="platform-card"><h2>Platform pulse</h2><div className="platform-pulse"><div><strong>{overview.new_users_30d}</strong><span>new users in 30 days</span></div><div><strong>{overview.pending_invitations}</strong><span>pending invitations</span></div><div><strong>{overview.active_users}</strong><span>active customer users</span></div></div></div></>:null}
      {!loading&&!error&&tab==="organizations"?<Table headers={["Organization","Owner","Users","Leads","Status",""]}>{organizations.map(o=><tr key={o.id}><td><strong>{o.name}</strong><small>{o.id}</small></td><td>{o.owner_email||"—"}</td><td>{o.active_user_count}/{o.user_count} active</td><td>{o.lead_count.toLocaleString()}</td><td><Status active={o.is_active}/></td><td><button className="table-action" onClick={()=>void toggleOrganization(o)}>{o.is_active?"Suspend":"Activate"}</button></td></tr>)}</Table>:null}
      {!loading&&!error&&(tab==="users"||tab==="support")?<><form className="platform-search" onSubmit={e=>{e.preventDefault();void load(tab,query)}}><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search name, email, or organization"/><button className="button">Search</button></form><Table headers={["User","Organization","Role","Verification","Status",""]}>{users.map(u=><tr key={u.id}><td><strong>{u.full_name||"Unnamed"}</strong><small>{u.email}</small></td><td>{u.brokerage_name||"—"}</td><td>{u.role}</td><td>{u.is_verified?"Verified":"Pending"}</td><td><Status active={u.is_active}/></td><td><button className="table-action" onClick={()=>void toggleUser(u)}>{u.is_active?"Suspend":"Activate"}</button></td></tr>)}</Table></>:null}
      {!loading&&!error&&tab==="product"?<><form className="flag-form platform-card" onSubmit={e=>void addFlag(e)}><div><h2>Create feature flag</h2><p>Global switches use a stable lowercase key.</p></div><input required pattern="[a-z0-9_.-]+" value={flagKey} onChange={e=>setFlagKey(e.target.value)} placeholder="feature.key"/><input value={flagDescription} onChange={e=>setFlagDescription(e.target.value)} placeholder="What does this control?"/><button className="button">Create disabled</button></form><Table headers={["Key","Scope","Description","State",""]}>{flags.map(f=><tr key={f.id}><td><strong>{f.key}</strong></td><td>{f.brokerage_id||"Global"}</td><td>{f.description||"—"}</td><td><Status active={f.enabled} labels={["On","Off"]}/></td><td><button className="table-action" onClick={()=>void toggleFlag(f)}>Turn {f.enabled?"off":"on"}</button></td></tr>)}</Table></>:null}
      {!loading&&!error&&tab==="operations"&&operations?<div className="platform-metrics ops">{Object.entries(operations).map(([key,value])=><article key={key}><span>{key.replaceAll("_"," ")}</span><strong>{value}</strong></article>)}</div>:null}
      {!loading&&!error&&tab==="security"?<Table headers={["Time","Actor","Action","Target","Detail"]}>{events.map(e=><tr key={e.id}><td>{formatDate(e.created_at)}</td><td>{e.actor_email}</td><td><strong>{e.action}</strong></td><td>{e.target_type} {e.target_id||""}</td><td><small>{e.detail||"—"}</small></td></tr>)}</Table>:null}
      {!loading&&!error&&tab==="engineering"&&engineering?<div className="platform-card engineering-grid">{Object.entries(engineering).map(([key,value])=><div key={key}><span>{key.replaceAll("_"," ")}</span><strong>{value}</strong></div>)}</div>:null}
    </section>
  </main>
}

function Table({headers,children}:{headers:string[];children:React.ReactNode}){return <div className="platform-table-wrap"><table><thead><tr>{headers.map((h,i)=><th key={`${h}-${i}`}>{h}</th>)}</tr></thead><tbody>{children}</tbody></table></div>}
function Status({active,labels=["Active","Suspended"]}:{active:boolean;labels?:[string,string]}){return <span className={`platform-status ${active?"on":"off"}`}>{active?labels[0]:labels[1]}</span>}
function formatDate(value:string|null){return value?new Date(value).toLocaleString():"—"}
