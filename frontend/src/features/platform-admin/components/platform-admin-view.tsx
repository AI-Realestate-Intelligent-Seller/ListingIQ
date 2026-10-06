"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { Brand } from "@/components/brand/brand";
import { Toast } from "@/components/toast/toast";
import { clearAuthSession, readAuthSession } from "@/features/auth/lib/auth-storage";
import { endSession, markActivity, watchSession } from "@/features/auth/lib/session-guard";
import * as api from "../api";

import type { AuditEvent, CustomerScreenRole, Engineering, FeatureFlag, Operations, Organization, PlatformOverview, PlatformUser } from "../types";

type Tab = "integrations" | "overview" | "organizations" | "users" | "product" | "operations" | "support" | "security" | "engineering";
const nav: {id: Tab; label: string; group: string}[] = [
  {id:"overview",label:"Overview",group:"Workspace"}, {id:"organizations",label:"Organizations",group:"Customers"},
  {id:"users",label:"Users & access",group:"Customers"}, {id:"product",label:"Feature flags",group:"Product"},
  {id:"operations",label:"Operations",group:"Operations"}, {id:"integrations",label:"Integrations",group:"Operations"}, {id:"support",label:"Customer lookup",group:"Support"},
  {id:"security",label:"Audit log",group:"Security"}, {id:"engineering",label:"Environments",group:"Engineering"},
];

export function PlatformAdminView({ children }: { children?: React.ReactNode }) {
  const router = useRouter();
  const [tab,setTab]=useState<Tab>(children ? "integrations" : "overview"); const [loading,setLoading]=useState(true); const [error,setError]=useState("");
  const [overview,setOverview]=useState<PlatformOverview|null>(null); const [organizations,setOrganizations]=useState<Organization[]>([]);
  const [users,setUsers]=useState<PlatformUser[]>([]); const [flags,setFlags]=useState<FeatureFlag[]>([]); const [operations,setOperations]=useState<Operations|null>(null);
  const [events,setEvents]=useState<AuditEvent[]>([]); const [engineering,setEngineering]=useState<Engineering|null>(null); const [query,setQuery]=useState("");
  const [flagKey,setFlagKey]=useState(""); const [flagDescription,setFlagDescription]=useState(""); const [notice,setNotice]=useState("");
  const [brokerageName,setBrokerageName]=useState(""); const [inviteEmail,setInviteEmail]=useState(""); const [initialRole,setInitialRole]=useState<CustomerScreenRole>("hob"); const [onboarding,setOnboarding]=useState(false); const [onboardingError,setOnboardingError]=useState("");
  const [updatingScreenUserId,setUpdatingScreenUserId]=useState<number|null>(null);

  async function load(activeTab=tab, search=query) {
    const session=readAuthSession();
    if (!session || session.user.role!=="platform_admin") { clearAuthSession(); router.replace("/login"); return; }
    setTab(activeTab); setLoading(true); setError("");
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
    const requested = new URLSearchParams(window.location.search).get("tab");
    const initial: Tab = children ? "integrations" : nav.some(item => item.id === requested && item.id !== "integrations") ? requested as Tab : "overview";
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing console loads its selected tab on mount
    void load(initial, "");
    return watchSession(()=>{clearAuthSession();router.replace("/login?expired=1")});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[router]);

  function choose(next:Tab){if(next==="integrations"){router.push("/platform-admin/integrations");return;}if(children){router.push(`/platform-admin?tab=${next}`);return;}setTab(next);setNotice("");void load(next,query)}
  async function toggleOrganization(item:Organization){const session=readAuthSession();if(!session)return;const reason=window.prompt(`Reason to ${item.is_active?"suspend":"activate"} ${item.name}:`);if(!reason)return;await api.setOrganizationActive(session.access_token,item.id,!item.is_active,reason);setNotice(`${item.name} updated.`);await load("organizations",query)}
  async function toggleUser(item:PlatformUser){const session=readAuthSession();if(!session)return;const reason=window.prompt(`Reason to ${item.is_active?"suspend":"activate"} ${item.email}:`);if(!reason)return;await api.setUserActive(session.access_token,item.id,!item.is_active,reason);setNotice(`${item.email} updated.`);await load(tab,query)}
  async function updateScreenAccess(item:PlatformUser, role:CustomerScreenRole){const session=readAuthSession();if(!session||role===item.role)return;setUpdatingScreenUserId(item.id);setError("");try{const result=await api.setUserScreenAccess(session.access_token,item.id,role);setUsers(current=>current.map(user=>user.id===item.id?{...user,role:result.role}:user));setNotice(result.message)}catch(reason){setError(reason instanceof Error?reason.message:"Could not update screen access.")}finally{setUpdatingScreenUserId(null)}}
  async function addBrokerage(event:React.FormEvent){event.preventDefault();const session=readAuthSession();if(!session)return;setOnboarding(true);setOnboardingError("");try{const result=await api.createBrokerageOnboarding(session.access_token,{brokerage_name:brokerageName,invite_email:inviteEmail,initial_role:initialRole});setBrokerageName("");setInviteEmail("");setInitialRole("hob");setNotice(result.message);await load("organizations",query)}catch(reason){setOnboardingError(reason instanceof Error?reason.message:"Could not create brokerage onboarding.")}finally{setOnboarding(false)}}
  async function addFlag(event:React.FormEvent){event.preventDefault();const session=readAuthSession();if(!session)return;await api.saveFeatureFlag(session.access_token,{key:flagKey,description:flagDescription,enabled:false,brokerage_id:null});setFlagKey("");setFlagDescription("");setNotice("Feature flag created.");await load("product",query)}
  async function toggleFlag(flag:FeatureFlag){const session=readAuthSession();if(!session)return;await api.saveFeatureFlag(session.access_token,{key:flag.key,description:flag.description||"",enabled:!flag.enabled,brokerage_id:flag.brokerage_id});setNotice(`${flag.key} updated.`);await load("product",query)}
  function logout(){endSession();clearAuthSession();router.replace("/login")}

  return <main className="platform-shell">
    <aside className="platform-sidebar"><Brand light/><div className="platform-badge">INTERNAL CONSOLE</div><nav>{[...new Set(nav.map(i=>i.group))].map(group=><section key={group}><span>{group}</span>{nav.filter(i=>i.group===group).map(i=><button key={i.id} className={tab===i.id?"active":""} onClick={()=>choose(i.id)}>{i.label}</button>)}</section>)}</nav><button className="platform-logout" onClick={logout}>Sign out</button></aside>
    <section className="platform-main">{!children ? <header><div><span>LISTINGIQ PLATFORM</span><h1>{nav.find(i=>i.id===tab)?.label}</h1><p>Internal product and customer operations.</p></div><div className="dashboard-header-actions"><button className="button secondary" onClick={()=>void load()}>Refresh</button></div></header> : null}
      {notice?<Toast message={notice} tone="success" onDone={()=>setNotice("")}/>:null}{error?<div className="platform-error" role="alert">{error}</div>:null}{loading?<div className="platform-loading">Loading platform data…</div>:null}
      {!loading && tab === "integrations" ? children : null}
      {!loading&&!error&&tab==="overview"&&overview?<><div className="platform-metrics">{[["Organizations",overview.organizations],["Active customers",overview.active_organizations],["Customer users",overview.users],["Total leads",overview.leads],["Campaigns",overview.campaigns],["Messages",overview.messages]].map(([l,v])=><article key={l}><span>{l}</span><strong>{Number(v).toLocaleString()}</strong></article>)}</div><div className="platform-card"><h2>Platform pulse</h2><div className="platform-pulse"><div><strong>{overview.new_users_30d}</strong><span>new users in 30 days</span></div><div><strong>{overview.pending_invitations}</strong><span>pending invitations</span></div><div><strong>{overview.active_users}</strong><span>active customer users</span></div></div></div></>:null}
      {!loading&&!error&&tab==="organizations"?<><form className="flag-form brokerage-invite-form platform-card" onSubmit={e=>void addBrokerage(e)}><div><h2>Add brokerage</h2><p>Create the brokerage and send its first user a secure onboarding link.</p>{onboardingError?<p className="platform-error" role="alert">{onboardingError}</p>:null}</div><input required minLength={2} maxLength={255} aria-label="Brokerage name" value={brokerageName} onChange={e=>setBrokerageName(e.target.value)} placeholder="Brokerage name"/><input required type="email" aria-label="Invite email" value={inviteEmail} onChange={e=>setInviteEmail(e.target.value)} placeholder="person@brokerage.com"/><ScreenRoleSelect ariaLabel="Initial screen" value={initialRole} onChange={setInitialRole}/><button className="button" disabled={onboarding}>{onboarding?"Sending…":"Send invite"}</button></form><Table headers={["Organization","Owner / invitee","Users","Leads","Status",""]}>{organizations.map(o=><tr key={o.id}><td><strong>{o.name}</strong><small>{o.id}</small></td><td>{o.owner_email||"—"}</td><td>{o.active_user_count}/{o.user_count} active</td><td>{o.lead_count.toLocaleString()}</td><td>{o.onboarding_status==="invited"?<span className="platform-status off">Invitation pending</span>:<Status active={o.is_active}/>}</td><td>{o.onboarding_status==="invited"?`Awaiting ${screenLabel(o.invitation_role)}`:<button className="table-action" onClick={()=>void toggleOrganization(o)}>{o.is_active?"Suspend":"Activate"}</button>}</td></tr>)}</Table></>:null}
      {!loading&&!error&&(tab==="users"||tab==="support")?<><form className="platform-search" onSubmit={e=>{e.preventDefault();void load(tab,query)}}><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search name, email, or organization"/><button className="button">Search</button></form><Table headers={["User","Organization","Screen access","Verification","Status",""]}>{users.map(u=><tr key={u.id}><td><strong>{u.full_name||"Unnamed"}</strong><small>{u.email}</small></td><td>{u.brokerage_name||"—"}</td><td><ScreenRoleSelect ariaLabel={`Screen access for ${u.email}`} value={u.role} disabled={updatingScreenUserId===u.id} onChange={role=>void updateScreenAccess(u,role)}/></td><td>{u.is_verified?"Verified":"Pending"}</td><td><Status active={u.is_active}/></td><td><button className="table-action" onClick={()=>void toggleUser(u)}>{u.is_active?"Suspend":"Activate"}</button></td></tr>)}</Table></>:null}
      {!loading&&!error&&tab==="product"?<><form className="flag-form platform-card" onSubmit={e=>void addFlag(e)}><div><h2>Create feature flag</h2><p>Global switches use a stable lowercase key.</p></div><input required pattern="[a-z0-9_.-]+" value={flagKey} onChange={e=>setFlagKey(e.target.value)} placeholder="feature.key"/><input value={flagDescription} onChange={e=>setFlagDescription(e.target.value)} placeholder="What does this control?"/><button className="button">Create disabled</button></form><Table headers={["Key","Scope","Description","State",""]}>{flags.map(f=><tr key={f.id}><td><strong>{f.key}</strong></td><td>{f.brokerage_id||"Global"}</td><td>{f.description||"—"}</td><td><Status active={f.enabled} labels={["On","Off"]}/></td><td><button className="table-action" onClick={()=>void toggleFlag(f)}>Turn {f.enabled?"off":"on"}</button></td></tr>)}</Table></>:null}
      {!loading&&!error&&tab==="operations"&&operations?<div className="platform-metrics ops">{Object.entries(operations).map(([key,value])=><article key={key}><span>{key.replaceAll("_"," ")}</span><strong>{value}</strong></article>)}</div>:null}
      {!loading&&!error&&tab==="security"?<Table headers={["Time","Actor","Action","Target","Detail"]}>{events.map(e=><tr key={e.id}><td>{formatDate(e.created_at)}</td><td>{e.actor_email}</td><td><strong>{e.action}</strong></td><td>{e.target_type} {e.target_id||""}</td><td><small>{e.detail||"—"}</small></td></tr>)}</Table>:null}
      {!loading&&!error&&tab==="engineering"&&engineering?<div className="platform-card engineering-grid">{Object.entries(engineering).map(([key,value])=><div key={key}><span>{key.replaceAll("_"," ")}</span><strong>{value}</strong></div>)}</div>:null}
    </section>
  </main>
}

function Table({headers,children}:{headers:string[];children:React.ReactNode}){return <div className="platform-table-wrap"><table><thead><tr>{headers.map((h,i)=><th key={`${h}-${i}`}>{h}</th>)}</tr></thead><tbody>{children}</tbody></table></div>}
function Status({active,labels=["Active","Suspended"]}:{active:boolean;labels?:[string,string]}){return <span className={`platform-status ${active?"on":"off"}`}>{active?labels[0]:labels[1]}</span>}
function ScreenRoleSelect({ariaLabel,value,onChange,disabled=false}:{ariaLabel:string;value:CustomerScreenRole;onChange:(role:CustomerScreenRole)=>void;disabled?:boolean}){
  const [open,setOpen]=useState(false); const [position,setPosition]=useState<{top:number;left:number;width:number}|null>(null); const root=useRef<HTMLDivElement>(null); const menu=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(!open)return;function place(){const rect=root.current?.getBoundingClientRect();if(!rect)return;const width=Math.max(rect.width,128);setPosition({top:rect.bottom+5,left:Math.max(8,Math.min(rect.left,window.innerWidth-width-8)),width})}function close(event:PointerEvent){const target=event.target as Node;if(!root.current?.contains(target)&&!menu.current?.contains(target))setOpen(false)}place();document.addEventListener("pointerdown",close);window.addEventListener("resize",place);window.addEventListener("scroll",place,true);return()=>{document.removeEventListener("pointerdown",close);window.removeEventListener("resize",place);window.removeEventListener("scroll",place,true)}},[open]);
  const options=open&&position?createPortal(<div ref={menu} className="platform-role-menu" style={{top:position.top,left:position.left,width:position.width}} role="listbox" aria-label={`${ariaLabel} options`}>{(["agent","hob","broker"] as CustomerScreenRole[]).map(role=><button type="button" role="option" aria-selected={role===value} aria-label={`${ariaLabel}: ${screenLabel(role)}`} key={role} onClick={()=>{onChange(role);setOpen(false)}}>{screenLabel(role)}{role===value?<span aria-hidden="true">✓</span>:null}</button>)}</div>,document.body):null;
  return <div ref={root} className="platform-role-picker"><button type="button" className="platform-role-trigger" aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} disabled={disabled} onClick={()=>setOpen(current=>!current)}><span>{screenLabel(value)}</span><span aria-hidden="true">⌄</span></button>{options}</div>
}
function screenLabel(role:CustomerScreenRole|null){return role==="hob"?"HOB":role==="broker"?"Broker":role==="agent"?"Agent":"invitee"}
function formatDate(value:string|null){return value?new Date(value).toLocaleString():"—"}
