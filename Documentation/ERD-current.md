# ListingIQ — Current Database (as built)

Generated from the running system: **7 tables · 36 endpoints · migrations 0001–0007 · 218 tests passing.**

SQLite in development (`project/fastapi/listingiq.db`), PostgreSQL in production via `POSTGRES_URL`.

The diagram below renders on GitHub and in any Mermaid-aware viewer. The raw source is also
kept separately in [`erd-current.mmd`](erd-current.mmd) so it can be pasted into
[mermaid.live](https://mermaid.live) or exported to an image.

---

## The shape in one sentence

**Everything hangs off `users`.** There is no `brokerages` table — brokerage identity is a
uuid **string** column (`users.brokerage_id`), not a foreign key. Leads, conversations and
bookings are all scoped to a single broker, never to a brokerage.

That single fact is what the future modules have to change first: cross-brokerage lead
exclusivity and round-robin assignment both need an entity that does not exist yet.

---

## Entity relationship diagram

```mermaid
erDiagram
    USERS ||--o{ INVITATIONS : "invited_by"
    USERS ||--o{ CONVERSATIONS : "owns"
    USERS ||--o{ LEADS : "owns"
    USERS ||--o{ BOOKINGS : "owns"
    USERS ||--o{ AI_RUNS : "logs"
    CONVERSATIONS ||--o{ MESSAGES : "contains"
    LEADS |o--o| CONVERSATIONS : "campaigned into"

    USERS {
        int id PK
        string email UK "indexed, lower-cased"
        string hashed_password
        string first_name
        string last_name
        string full_name
        string brokerage_name
        string brokerage_id "uuid string, NOT a FK"
        string role "hob broker agent"
        bool is_head_or_owner
        bool is_verified
        bool is_active
        text google_refresh_token "encrypted, unused"
        datetime created_at
    }
    INVITATIONS {
        int id PK
        string email "indexed"
        string role "broker or agent"
        string brokerage_id "indexed"
        string brokerage_name
        int invited_by FK
        string token_hash UK "sha256, raw never stored"
        datetime expires_at "48h"
        string status "pending accepted expired cancelled"
        datetime accepted_at
        datetime created_at
    }
    LEADS {
        int id PK
        int user_id FK "indexed"
        string owner_name
        string phone "indexed, E164"
        string property_address
        string area
        string signals "comma separated keys"
        string outreach_reason "vendor wording"
        text details "JSON attributes"
        int score "0-100, derived"
        bool dnc
        int conversation_id FK "null until campaigned"
        datetime last_activity_at
        datetime refreshed_at
        datetime created_at
    }
    CONVERSATIONS {
        int id PK
        string contact "indexed, E164"
        int user_id FK
        string name
        string property_address
        bool ai_enabled
        bool recipient_ai_enabled "legacy, dormant"
        string handled_by "bobbie or broker"
        text lead_context "JSON grounded facts"
        string lead_status
        string queue_status "legacy, dormant"
        bool dnc_alert
        bool meeting_booked
        string followup_state "pending, accepted or declined"
        datetime processed_at
        datetime created_at
    }
    MESSAGES {
        int id PK
        int conversation_id FK
        string direction "inbound or outbound"
        string from_number
        string to_number
        text text
        string status
        string event_type "outreach.initial ai.reply broker.message"
        string telnyx_id "indexed"
        datetime created_at
    }
    BOOKINGS {
        int id PK
        int user_id FK "indexed"
        string phone
        string name
        string title
        datetime start_at
        datetime end_at
        string join_token
        datetime created_at
    }
    AI_RUNS {
        int id PK
        int user_id FK
        string model
        text prompt
        text result
        datetime created_at
    }
```

---

## Tables and who writes them

| Table | Written by | Read by | Migration |
|---|---|---|---|
| `users` | Registration; invitation acceptance | Every authenticated request | 0001 |
| `invitations` | HOB invite panel; acceptance | `/join` landing page | 0002 |
| `conversations` | Manual thread creation; campaign launch; every handover; the inbound classifier; Follow-ups decisions and manual status overrides | SMS tab; Follow-ups tab; lead pool sync | 0003, 0005, 0008 |
| `messages` | Every send; the Telnyx webhook | SMS thread; Follow-ups thread and its "has replied" filter; lead "last activity" | 0003 |
| `bookings` | Bobbie, when a meeting is agreed; the Follow-ups scheduler, when one is booked by hand | Availability calculation | 0004 |
| `leads` | CSV/XLSX import (batched inserts); campaign launcher | Lead Pool tab | 0006, 0007 |
| `ai_runs` | `ai_runtime.py` only — **nothing live calls it** | nothing | 0001 |

---

## Three columns worth understanding

**`conversations.handled_by`** — `bobbie` or `broker`. This is the handover gate. The AI
pipeline returns early unless it reads `bobbie`, so the rule "Bobbie never replies on a
broker-owned thread" is enforced in the database state, not in the UI. Sending a message
yourself, an owner opting out, a booking, or a closing message all move it to `broker`;
only an explicit hand-back moves it to `bobbie`.

**`conversations.followup_state`** — `pending`, `accepted` or `declined`. The Follow-ups
decision, kept apart from `handled_by` on purpose. Accepting a lead says who owns the outcome;
taking the thread off Bobbie is a different action with a different consequence, and folding
them together would silence her the moment anyone accepted a lead. Nothing in the AI pipeline
reads this column.

**`leads.stage`** — *does not exist*. Stage is derived on every read, in strict precedence:

```
dnc → in_campaign → needs_review (no phone) → new (refreshed < 24h) → ready
```

It is deliberately not a column so it can never drift from the conversation it describes —
delete a thread and the lead returns to `ready` automatically. **This conflicts with the
"stages changed manually, one stage prerequisite for another" requirement** in the
expectations sheet; see the gaps below.

---

## Structural gaps, read against the future modules

| # | Module | Gap |
|---|---|---|
| 1 | **Campaigns** | A campaign is a verb, not a row. No table joins a run of outreach to its leads, its message and its results, so "what went out, how many reached, how many replied" has nothing to query. Everything in campaign analytics depends on this table existing first. |
| 2 | **Data** | No record identity above the broker. No `brokerage_id` on `leads` or `conversations` at all. Two brokers importing the same list get two independent leads and both can text the same owner. "Used in one campaign, unavailable to any other" needs a global record keyed on phone/PIN plus a claim. |
| 3 | **Data** | Stage is derived, not stored (above). Manual stages with prerequisites need a stored column, an allowed-transition table, and an audit of who moved what. A hybrid is likely: system-owned for `in_campaign` and `dnc`, manual for the review stages. |
| 4 | **Assignments** | Nothing links an agent to a lead — no `assigned_to`, no assignment history, no round-robin cursor. Every query scopes by `user_id` = the logged-in broker. |
| 5 | **Data** | `GET /leads` returns the whole pool unpaginated and the UI polls it every 5s. It held at 4,485 leads; with batch ingestion it will not. Pagination is a prerequisite, not an optimisation. |
| 6 | **Inbound** | The Telnyx webhook matches a conversation on phone number alone, unscoped by owner. Today each broker works their own numbers so it holds; the moment two brokerages share a contact the newest thread silently wins. |
| 7 | **Cleanup** | Three routers are dead code — `/api/v1/conversations`, `/api/v1/messages`, `/api/v1/ai`. The frontend calls none of them and they duplicate live behaviour with weaker guards (`POST /messages/` bypasses the handover rule entirely). Delete before the schema grows around them. |
| 8 | **Cleanup** | `leads` and `conversations` both carry name, phone/contact, address and a DNC flag, synced one direction on every pool read. Once campaigns become a real entity this duplication needs resolving or the two will drift. |

---

## Files in this folder

| File | What it is |
|---|---|
| `ERD-current.md` | This document |
| `erd-current.mmd` | Mermaid ERD source, standalone |
| `tables.csv` | Column dictionary — every column, its type, key, default and **why it exists** |
| `endpoints.csv` | All 36 endpoints — tables read, tables written, the **actual query**, why it is shaped that way, and where the UI calls it |
