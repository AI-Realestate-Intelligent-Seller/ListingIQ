# Policy guards — the deterministic layer

Everything here runs **without a model**. It is the part of the system that cannot be
argued out of its position, and it holds the final veto over anything Bobbie sends.

Code: `app/sms/policy.py`, `app/sms/classifier.py`.

---

## Where the guards sit

| Point | Guard | Runs |
|---|---|---|
| Before any model call | opt-out regex | on every inbound message |
| Before any model call | lead classifier | on every inbound message |
| After the reply draft | policy violation scan | on every generated reply |
| After a failed repair | safe grounded fallback | when the draft cannot be fixed |
| Before sending | `fit_complete_sms()` | on every outbound message |

---

## 1. Opt-out detection

```python
OPT_OUT = r"\b(stop|stopall|unsubscribe|remove me|do not contact|don't contact
             |don't message|leave me alone|take me off|done here)\b"
```

Matched in `process_ai_reply()` **before the first model call**. On a match: a fixed sentence is
sent, `lead_status='dnc'`, `dnc_alert=true`, the thread is handed to the broker, and the lead's
`dnc` flag propagates on the next pool read — permanently blocking that lead from any campaign.

**No model is ever asked whether to honour an opt-out.** This is a legal obligation, not a
judgement call.

---

## 2. Lead classifier

`classify_lead_message()` — a set of ordered regexes producing `{lead_status, terminal,
dnc_alert}`. It runs on every inbound message and is also the fallback when the disposition
model is unavailable.

| Pattern | Status |
|---|---|
| `OPT_OUT` | `dnc`, terminal |
| `NO_REPLY` | `no_response`, terminal |
| `QUALIFIED_PROCESS_OBJECTION` — "not interested in repeating that process" | **not** terminal — an objection to the method, not the sale |
| `IMMEDIATE_BUYER_CONDITION` — "do you have a ready buyer?" | conditional interest |
| `DIRECT_REJECTION` — "not interested", "already sold", "wrong number" | `not_interested`, terminal |
| `READY_TO_SELL` | `ready_to_sell` |
| `INTERESTED`, `CALL_OPENNESS` | `interested` |
| `WANT_MORE_INFO` | `want_more_info` |

**The two negative-lookahead rules carry most of the value.** `DIRECT_REJECTION` deliberately
does *not* match "not interested in a call" — refusing a phone call is not refusing to sell.
And `QUALIFIED_PROCESS_OBJECTION` catches the seller who is tired of listing, not of selling.
Both were cases where a naive keyword match killed a live lead.

**Status never regresses.** `merge_lead_status()` uses a priority ladder
(`processing < want_more_info < interested < ready_to_sell`), so a later vague message cannot
demote a seller who already said they are ready.

---

## 3. Policy violation scan

`find_reply_policy_violations()` runs on every generated draft. Each violation is a claim that
could put a brokerage in front of a regulator or a lawyer.

| Violation | Catches |
|---|---|
| `over_length` | more than 240 characters |
| `identity_switch` | "Bobbie Fisher's AI assistant", third-person self-reference |
| `invented_email` | any email address |
| `invented_phone` | any phone number — there is no verified callback number |
| `unsupported_delivery` | promises to email, send, or follow up later |
| `unsupported_fee` | any commission, fee, percentage or rate |
| `unsupported_buyer` | claiming a ready buyer |
| `unsupported_production` | sales volume or performance claims |
| `unsupported_local_experience` | "I've sold many homes in your area" |
| `unsupported_outcome` | promised results |
| `unsupported_tenure` | "several years", "since 2015", "over 10 years" |
| `example_fact_leak` | property facts borrowed from the prompt's example |
| `unsupported_phone_source` | "I got your number from public records" — only when the lead data does **not** say so |
| `repeated_question` | a question already asked, by intent not by wording |

### Two details worth knowing

**Honest denials are allowed.** `BUYER_DENIAL` strips clauses like "I don't have a ready buyer"
*before* the buyer check runs, so refusing to claim a buyer is not itself flagged as claiming
one.

**Phone-source is conditional.** The claim is only a violation when the approved lead context
does not record how the number was sourced — which is the default. If a vendor file ever
carries that provenance, the same sentence becomes legal.

### Repetition detection

`find_conversation_repetition()` compares by **question intent**, not wording — "what's your
timeline?" and "when were you hoping to sell?" collapse to the same intent. Token-similarity
above threshold against recent outbound messages also counts. This exists because the most
common complaint about SMS bots is being asked the same thing twice.

---

## 4. Repair, then fallback

```
draft → violations? → one repair attempt naming the violations
                   → still violating? → safe_grounded_fallback()
```

`safe_grounded_fallback()` is a ladder of about a dozen hand-written sentences, chosen by
matching the violation *and* the owner's latest message, and skipped if the candidate would
itself be a repetition. Examples:

> "I have a property lead record, but it doesn't show how your phone number was sourced, so I
> don't want to guess."

> "Fees and listing terms depend on the service and written agreement, so I don't have approved
> numbers to quote here."

> "I don't have a verified buyer ready today. I understand you won't repeat the same listing
> process; would you only consider a direct buyer with proof of funds?"

Last resort: *"I've shared what I can verify here, and I don't want to repeat myself or invent
details."*

Every one of these is a refusal that still moves the conversation forward — which is the point.
A safe reply that kills the thread is not actually safe for the business.

---

## 5. Length fitting

`fit_complete_sms(text, 240)` trims to the last complete sentence within the limit rather than
cutting mid-word; if no sentence break exists it truncates with an ellipsis. Runs on every
outbound message, including fallbacks.

---

## Testing

These guards are the most heavily tested part of the system — `tests/test_sms.py` covers each
violation class, the honest-denial carve-out, the repetition detector, the classifier's
negative lookaheads, and the status ladder. They are pure functions with no I/O, so they are
cheap to test and cheap to extend.

**When you add a new claim Bobbie must not make, add the regex here first** — the prompt is a
request, this is the enforcement.
