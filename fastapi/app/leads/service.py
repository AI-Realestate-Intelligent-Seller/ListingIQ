"""Lead pool operations: import, filter, and hand leads to Bobbie.

The three right-hand columns of the pool — score, stage, last activity — are
system-owned. Stage in particular is *derived on read* from the lead's own
state, so it can never drift out of sync with the conversation it points at.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

import json

from ..models import Conversation, Lead, Message, User
from ..tenancy import brokerage_user_ids
from . import address as address_key
from .catalog import SIGNALS, signal_label
from .importer import normalize_phone, parse_upload, score_lead
from .location import STATE_LABELS, Location, parse as parse_location

# Ids per DELETE statement — comfortably under every driver's bind-parameter cap.
DELETE_CHUNK = 500


def split_signals(lead: Lead) -> list[str]:
    return [key for key in (lead.signals or '').split(',') if key]


def lead_details(lead: Lead) -> dict:
    """The imported property attributes, or {} if there were none."""
    if not lead.details:
        return {}
    try:
        parsed = json.loads(lead.details)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Supplemental contact numbers are operational metadata, not property
    # attributes, so the details drawer must not render them as a house fact.
    parsed.pop('_phone_numbers', None)
    return parsed


def lead_phone_numbers(lead: Lead) -> list[dict]:
    """Every number shown for a lead, each with its own contact restriction."""
    stored: list[dict] = []
    if lead.details:
        try:
            parsed = json.loads(lead.details)
            candidates = parsed.get('_phone_numbers', []) if isinstance(parsed, dict) else []
        except ValueError:
            candidates = []
        if isinstance(candidates, list):
            for item in candidates:
                if not isinstance(item, dict) or not item.get('phone'):
                    continue
                phone = str(item['phone']).strip()
                if phone and all(row['phone'] != phone for row in stored):
                    stored.append({'phone': phone, 'dnc': bool(item.get('dnc'))})

    if lead.phone and all(row['phone'] != lead.phone for row in stored):
        stored.insert(0, {'phone': lead.phone, 'dnc': bool(lead.dnc)})
    elif lead.phone:
        for row in stored:
            if row['phone'] == lead.phone:
                row['dnc'] = bool(row['dnc'] or lead.dnc)
                break
    return stored


def derive_stage(lead: Lead, now: datetime | None = None) -> str:
    """Where this lead stands with us, in strict precedence."""
    if lead.dnc:
        return 'dnc'
    if lead.conversation_id is not None:
        return 'in_campaign'
    if not lead.phone:
        # Nothing can be sent without a number, however good the signals are.
        return 'needs_review'
    # A freshly imported lead used to sit at "New" for a day. It is the same
    # thing to everyone who reads it — contactable, not yet contacted — and the
    # split only ever delayed outreach, so there is one stage now.
    return 'ready'


def serialize(lead: Lead, now: datetime | None = None) -> dict:
    signals = split_signals(lead)
    return {
        'id': lead.id,
        'owner_name': lead.owner_name,
        'phone': lead.phone,
        'phone_numbers': lead_phone_numbers(lead),
        'property_address': lead.property_address,
        'area': lead.area,
        'signals': [{'key': key, 'label': signal_label(key)} for key in signals],
        'score': lead.score,
        'outreach_reason': lead.outreach_reason,
        'stage': derive_stage(lead, now),
        'last_activity_at': lead.last_activity_at,
        'conversation_id': lead.conversation_id,
        'created_at': lead.created_at,
    }


def score_breakdown(lead: Lead) -> list[dict]:
    """Why this lead scores what it scores, in the order the points are added.

    The score is advisory, so showing its arithmetic is the honest thing to do:
    a broker can see it is a sum of signals and contactability, not a model.
    """
    parts = [{'label': signal_label(key), 'points': SIGNALS.get(key, ('', 0))[1]}
             for key in split_signals(lead)]
    if lead.phone:
        parts.append({'label': 'Phone number on file', 'points': 20})
    if lead.property_address:
        parts.append({'label': 'Property address on file', 'points': 10})
    return parts


def detail(session: Session, lead: Lead) -> dict:
    """Everything the details panel shows, including the linked conversation."""
    item = serialize(lead)
    item['refreshed_at'] = lead.refreshed_at
    item['details'] = lead_details(lead)
    item['score_breakdown'] = score_breakdown(lead)
    item['conversation'] = None

    if lead.conversation_id is not None:
        conversation = (session.query(Conversation)
                        .filter(Conversation.id == lead.conversation_id)
                        .first())
        if conversation is not None:
            latest = (session.query(Message)
                      .filter(Message.conversation_id == conversation.id)
                      .order_by(Message.created_at.desc(), Message.id.desc())
                      .first())
            item['conversation'] = {
                'id': conversation.id,
                'handled_by': conversation.handled_by,
                'lead_status': conversation.lead_status,
                'meeting_booked': bool(conversation.meeting_booked),
                'message_count': (session.query(Message)
                                  .filter(Message.conversation_id == conversation.id).count()),
                'latest_message': latest.text if latest else None,
                'latest_message_at': latest.created_at if latest else None,
            }
    return item


def lead_location(lead: Lead) -> Location:
    """Where a lead is, read back out of the address it was imported with."""
    return parse_location(lead.property_address, lead.area)


def _matches_search(lead: Lead, search: str, now: datetime) -> bool:
    """Search visible fields and the useful imported metadata behind a lead."""
    terms = [term.lower() for term in search.split() if term]
    if not terms:
        return True

    stage = derive_stage(lead, now)
    phones = lead_phone_numbers(lead)
    details = lead_details(lead)
    values = [
        str(lead.id),
        lead.owner_name or '',
        lead.phone or '',
        lead.property_address or '',
        lead.area or '',
        lead.outreach_reason or '',
        str(lead.score),
        stage,
        'do not contact dnc' if stage == 'dnc' else '',
        str(lead.conversation_id or ''),
        str(lead.campaign_id or ''),
        str(lead.last_activity_at or ''),
        str(lead.created_at or ''),
        ' '.join(f'{key} {signal_label(key)}' for key in split_signals(lead)),
        ' '.join(
            f"{item['phone']} {'do not contact dnc' if item['dnc'] else 'contactable'}"
            for item in phones
        ),
        json.dumps(details, ensure_ascii=False),
    ]
    haystack = ' '.join(values).lower()
    # Also make punctuation-insensitive phone/ID searches work naturally.
    compact = ''.join(character for character in haystack if character.isalnum())
    return all(term in haystack or ''.join(char for char in term if char.isalnum()) in compact
               for term in terms)


def _pool_locations(session: Session, user: User) -> list[Location]:
    """Every pool lead's location, cheaply — two columns, not whole rows.

    The filter menu and the town-to-ZIP crosswalk both need the *whole* pool,
    including the leads the current filters hide, so this deliberately ignores
    every filter.
    """
    rows = (session.query(Lead.property_address, Lead.area)
            .filter(Lead.user_id.in_(brokerage_user_ids(session, user)),
                    Lead.campaign_id.is_(None)).all())
    return [parse_location(address, area) for address, area in rows]


def _city_states(locations: list[Location]) -> dict[str, str]:
    """Town -> the one state the pool ever pairs it with, where there is only one.

    A lead whose town came from the vendor's `area` column carries no state, and
    a town is identified by both. Rather than let "Mattoon" and "Mattoon, IL"
    sit in the menu as two towns, a stateless town is read as the stated one
    when the pool leaves no doubt which that is. Where there is doubt — a
    Springfield in two states — the stateless town stays separate, because
    guessing would file leads under a town they are not in.
    """
    seen: dict[str, set[str]] = {}
    for item in locations:
        if item.city_key and item.state:
            seen.setdefault(item.city_key, set()).add(item.state)
    return {key: next(iter(states)) for key, states in seen.items() if len(states) == 1}


def _city_id(item: Location, city_states: dict[str, str]) -> str:
    """Which town in the filter menu this lead belongs to, or '' for none."""
    if not item.city_key:
        return ''
    if item.state:
        return item.city_id
    return f'{item.city_key}|{city_states.get(item.city_key, "")}'


def _resolved_state(item: Location, city_id: str) -> str:
    """The state a lead is in, including the one its town implies.

    A lead with only an `area` of "Mattoon" has no state of its own, but once
    _city_states has settled that the pool's only Mattoon is in Illinois, it is
    in Illinois for the state filter too. Filing it under the town but not
    under the town's state would be two answers to one question.
    """
    if item.state:
        return item.state
    return city_id.split('|')[1] if '|' in city_id else ''


def _city_label(item: Location, city_id: str) -> str:
    """How a town reads in the menu: its own spelling, plus its state."""
    state = city_id.split('|')[1] if '|' in city_id else ''
    return f'{item.city}, {state}' if item.city and state else item.city


def _zips_for_cities(locations: list[Location], cities: set[str],
                     city_states: dict[str, str]) -> set[str]:
    """The ZIPs the pool has seen for these towns.

    This is what makes a town searchable when the leads in it do not agree on
    what to call it. One address says "Mattoon", the next says "Village of
    Mattoon" and a third names the county instead — but they share a ZIP, so
    picking the town finds all three. The pool is its own crosswalk; no postal
    database is involved, and none is needed, because a ZIP with no leads in it
    could not widen the result anyway.
    """
    if not cities:
        return set()
    return {item.postal for item in locations
            if item.postal and _city_id(item, city_states) in cities}


def _matches_location(item: Location, city_id: str, states: set[str], cities: set[str],
                      zips: set[str], city_zips: set[str]) -> bool:
    """AND across the three filters, OR within each one.

    Each filter stands on its own — a ZIP with no state selected is a perfectly
    good question — and they narrow each other when combined.
    """
    if states and _resolved_state(item, city_id) not in states:
        return False
    if cities and not (city_id in cities or (item.postal and item.postal in city_zips)):
        return False
    if zips and item.postal not in zips:
        return False
    return True


def list_leads(session: Session, user: User, search: str = '', signals: list[str] | None = None,
               stage: str = '', states: list[str] | None = None, cities: list[str] | None = None,
               zips: list[str] | None = None) -> list[dict]:
    """The brokerage's leads, filtered by text, signals, stage and location.

    Signal, stage and location filters run in Python: signals are a packed
    column, stage is derived and location is read back out of the address
    string, so none can be expressed as a portable SQL predicate. The pool is a
    per-brokerage working set, not a warehouse table.
    """
    # A lead in a campaign is shown under that campaign, not here: the pool is
    # what is still to be worked, and a row in both places is a row two people
    # can pick up at once.
    query = (session.query(Lead)
             .filter(Lead.user_id.in_(brokerage_user_ids(session, user)),
                     Lead.campaign_id.is_(None)))
    now = datetime.utcnow()
    rows = query.order_by(Lead.score.desc(), Lead.id.desc()).all()
    wanted = {key for key in (signals or []) if key}
    want_states = {key for key in (states or []) if key}
    want_cities = {key for key in (cities or []) if key}
    want_zips = {key for key in (zips or []) if key}
    # Resolving a town needs the whole pool, so it is only read when a town or
    # a state is being filtered on — a state because a town can imply one.
    city_states: dict[str, str] = {}
    city_zips: set[str] = set()
    if want_cities or want_states:
        locations = _pool_locations(session, user)
        city_states = _city_states(locations)
        city_zips = _zips_for_cities(locations, want_cities, city_states)

    result = []
    for lead in rows:
        if not _matches_search(lead, search, now):
            continue
        if wanted and not wanted.issubset(set(split_signals(lead))):
            continue
        if want_states or want_cities or want_zips:
            item = lead_location(lead)
            if not _matches_location(item, _city_id(item, city_states), want_states,
                                     want_cities, want_zips, city_zips):
                continue
        item = serialize(lead, now)
        if stage and item['stage'] != stage:
            continue
        result.append(item)
    return result


def location_facets(session: Session, user: User, states: list[str] | None = None,
                    cities: list[str] | None = None, zips: list[str] | None = None) -> dict:
    """The location filter menu: which states, towns and ZIPs the pool holds.

    Each list is counted with the *other* two filters applied but not its own.
    That is what makes the controls dependent without making them modal —
    choosing Illinois narrows the town list to Illinois towns, while the state
    list still shows every state, so the choice can be changed rather than
    only undone.

    A selection is always listed even when nothing matches it any more, at a
    count of zero. Dropping it out of the menu would leave a filter in force
    with no visible way to lift it.
    """
    want_states = {key for key in (states or []) if key}
    want_cities = {key for key in (cities or []) if key}
    want_zips = {key for key in (zips or []) if key}

    locations = _pool_locations(session, user)
    city_states = _city_states(locations)
    city_zips = _zips_for_cities(locations, want_cities, city_states)

    state_counts: dict[str, int] = {}
    city_counts: dict[str, int] = {}
    city_labels: dict[str, str] = {}
    zip_counts: dict[str, int] = {}
    zip_towns: dict[str, set[str]] = {}

    for item in locations:
        city_id = _city_id(item, city_states)
        label = _city_label(item, city_id)
        state = _resolved_state(item, city_id)
        if state and _matches_location(item, city_id, set(), want_cities, want_zips, city_zips):
            state_counts[state] = state_counts.get(state, 0) + 1
        if city_id and _matches_location(item, city_id, want_states, set(), want_zips, city_zips):
            city_counts[city_id] = city_counts.get(city_id, 0) + 1
            city_labels.setdefault(city_id, label)
        if item.postal and _matches_location(item, city_id, want_states, want_cities, set(), city_zips):
            zip_counts[item.postal] = zip_counts.get(item.postal, 0) + 1
            # A ZIP can span towns, so retain every locality represented in
            # the pool rather than making the first imported row look
            # authoritative. The ZIP remains one filter and its count covers
            # every matching lead regardless of which locality name it uses.
            if label:
                zip_towns.setdefault(item.postal, set()).add(label)

    for key in want_states:
        state_counts.setdefault(key, 0)
    for key in want_cities:
        city_counts.setdefault(key, 0)
        city_labels.setdefault(key, key.split('|')[0].title())
    for key in want_zips:
        zip_counts.setdefault(key, 0)

    def ranked(counts: dict[str, int], label: dict[str, str] | None = None) -> list[dict]:
        # Busiest first, then alphabetical: the menu opens on where the work is.
        return [{'key': key, 'label': (label or {}).get(key, key), 'count': counts[key]}
                for key in sorted(counts, key=lambda key: (-counts[key],
                                                           (label or {}).get(key, key).lower()))]

    return {
        'states': ranked(state_counts, {code: STATE_LABELS.get(code, code) for code in state_counts}),
        'cities': ranked(city_counts, city_labels),
        'zips': ranked(zip_counts, {
            code: (f'{code} · {" / ".join(sorted(zip_towns[code]))}'
                   if code in zip_towns else code)
            for code in zip_counts
        }),
    }


def facets(session: Session, user: User) -> dict:
    """Counts per signal and per stage across the brokerage's pool.

    Counts what the pool shows, so leads held by a campaign are excluded here
    exactly as they are in `list_leads`.
    """
    now = datetime.utcnow()
    signal_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    total = 0
    for lead in session.query(Lead).filter(
            Lead.user_id.in_(brokerage_user_ids(session, user)),
            Lead.campaign_id.is_(None)).all():
        total += 1
        for key in split_signals(lead):
            signal_counts[key] = signal_counts.get(key, 0) + 1
        stage = derive_stage(lead, now)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    return {'total': total, 'signals': signal_counts, 'stages': stage_counts}


def analyze_upload(session: Session, user: User, content: bytes, filename: str,
                   overrides: dict[str, str] | None = None,
                   mapping_confirmed: bool = True) -> dict:
    """What this file would do, without writing anything.

    A property is one lead even when its address formatting changes. The same
    phone may appear on multiple leads when it belongs to multiple properties.
    Phone is only the fallback identity for rows without a usable address.
    """
    # CSV preview must not guess which vendor column represents the property.
    # Before the user confirms that choice, show every readable row and defer
    # duplicate removal. An explicit blank mapping means "no address" and uses
    # phone fallback once confirmed.
    parse_overrides = overrides
    if not mapping_confirmed:
        parse_overrides = {**(overrides or {}), 'property_address': ''}
    rows, warnings, meta = parse_upload(content, filename, parse_overrides)
    readable_count = len(rows)
    def identity(phone: str | None, address: str | None) -> tuple[str, str] | None:
        property_key = address_key.canonical(address)
        if property_key:
            return ('property', property_key)
        normalized_phone = normalize_phone(phone or '')
        return ('phone', normalized_phone) if normalized_phone else None

    existing_keys: set[tuple[str, str]] = set()
    existing_leads = (session.query(Lead)
                      .filter(Lead.user_id.in_(brokerage_user_ids(session, user)))
                      .all())
    for lead in existing_leads:
        key = identity(lead.phone, lead.property_address)
        if key:
            existing_keys.add(key)

    if not mapping_confirmed:
        return {
            'rows': rows,
            'columns': meta['columns'],
            'mapping': meta['mapping'],
            'signal_columns': meta['signal_columns'],
            'total_rows': readable_count,
            'importable': len(rows),
            'duplicate_count': 0,
            'warnings': warnings,
            'sample': [
                {
                    'owner_name': row['owner_name'],
                    'phone': row['phone'],
                    'property_address': row['property_address'],
                    'area': row['area'],
                    'signals': [signal_label(key) for key in row['signals']],
                    'outreach_reason': row.get('outreach_reason'),
                    'details': row.get('details') or {},
                }
                for row in rows[:5]
            ],
        }

    unique_rows: list[dict] = []
    seen_keys = set(existing_keys)
    duplicate_count = 0
    for row in rows:
        key = identity(row.get('phone'), row.get('property_address'))
        if key and key in seen_keys:
            duplicate_count += 1
            continue
        unique_rows.append(row)
        if key:
            seen_keys.add(key)
    rows = unique_rows
    if duplicate_count:
        label = 'row was' if duplicate_count == 1 else 'rows were'
        warnings.insert(
            0,
            f'{duplicate_count} duplicate {label} skipped because the property or phone already exists.',
        )

    return {
        'rows': rows,
        'columns': meta['columns'],
        'mapping': meta['mapping'],
        'signal_columns': meta['signal_columns'],
        'total_rows': readable_count,
        'importable': len(rows),
        'duplicate_count': duplicate_count,
        'warnings': warnings,
        'sample': [
            {
                'owner_name': row['owner_name'],
                'phone': row['phone'],
                'property_address': row['property_address'],
                'area': row['area'],
                'signals': [signal_label(key) for key in row['signals']],
                'outreach_reason': row.get('outreach_reason'),
                'details': row.get('details') or {},
            }
            for row in rows[:5]
        ],
    }


# Rows per INSERT batch. A vendor list can run to six figures, and one flush of
# that size is far slower than a handful of batches.
IMPORT_CHUNK = 1000


def import_file(session: Session, user: User, content: bytes, filename: str,
                limit: int = 0, overrides: dict[str, str] | None = None) -> dict:
    """Add a CSV or XLSX to the broker's pool, one lead per readable row.

    `limit` caps how many new, non-duplicate rows are taken (0 means all).
    Property matching is brokerage-wide, so HOB and broker imports cannot add
    the same property twice. A shared phone may still create separate leads for
    separate properties.
    """
    report = analyze_upload(session, user, content, filename, overrides)
    rows = report['rows']
    if not rows:
        return {'created': 0, 'total_rows': report['total_rows'], 'warnings': report['warnings']}

    if limit and limit > 0:
        rows = rows[:limit]

    now = datetime.utcnow()
    for start in range(0, len(rows), IMPORT_CHUNK):
        session.bulk_save_objects([
            Lead(
                user_id=user.id,
                owner_name=row['owner_name'],
                phone=row['phone'],
                property_address=row['property_address'],
                area=row['area'],
                signals=','.join(row['signals']),
                outreach_reason=row.get('outreach_reason'),
                details=json.dumps(row['details']) if row.get('details') else None,
                score=row['score'],
                dnc=False,
                created_at=now,
                refreshed_at=now,
            )
            for row in rows[start:start + IMPORT_CHUNK]
        ])
        session.commit()

    return {'created': len(rows), 'total_rows': report['total_rows'],
            'warnings': report['warnings']}


# What Bobbie says she is calling about, per signal. Keeping this factual
# matters: she is only allowed to state what the record actually shows.
_REASON_BY_SIGNAL: dict[str, str] = {
    'expired': 'The property appears to have come off the market without a recorded sale',
    'fsbo': 'The property appears to be listed for sale by the owner',
    'pre_foreclosure': 'Public records show a pre-foreclosure filing on the property',
    'tax_delinquent': 'Public records show delinquent property taxes',
    'probate': 'The property appears in probate records',
    'divorce': 'Public records suggest the property may be part of a change in ownership',
    'vacant': 'The property appears to be vacant',
    'absentee_owner': 'Records show the owner lives at a different address',
    'high_equity': 'Public records suggest the property carries significant equity',
}

FALLBACK_REASON = 'I came across the property while reviewing records in the area'


def outreach_reason(lead: Lead, override: str = '') -> str:
    """The one sentence Bobbie uses to explain why she reached out.

    A reason shipped with the lead wins over the generic per-signal wording:
    the vendor knows what the record actually shows.
    """
    if (override or '').strip():
        return override.strip()
    if (lead.outreach_reason or '').strip():
        return lead.outreach_reason.strip().rstrip('.')
    for key in split_signals(lead):
        if key in _REASON_BY_SIGNAL:
            return _REASON_BY_SIGNAL[key]
    return FALLBACK_REASON


def delete_leads(session: Session, user: User, lead_ids: list[int]) -> int:
    """Remove leads from this broker's pool. Ids that are not theirs are ignored.

    The ids are chunked because an IN clause is one bind parameter per id and
    every database caps those (SQLite historically at 999). Selecting a whole
    pool of several thousand leads has to work.

    The linked conversation is deliberately left alone: a thread Bobbie has
    already opened is a record of contact, not pool clutter.
    """
    unique = list(dict.fromkeys(lead_ids))
    scope = brokerage_user_ids(session, user)
    deleted = 0
    for start in range(0, len(unique), DELETE_CHUNK):
        deleted += (session.query(Lead)
                    .filter(Lead.user_id.in_(scope),
                            Lead.id.in_(unique[start:start + DELETE_CHUNK]))
                    .delete(synchronize_session=False))
    if deleted:
        session.commit()
    return int(deleted)


def sync_activity(session: Session, user_ids: list[int]) -> None:
    """Refresh last-activity and DNC from the conversations leads point at.

    Takes the brokerage's account ids rather than one, so a lead imported by a
    teammate is refreshed too.
    """
    leads = (session.query(Lead)
             .filter(Lead.user_id.in_(user_ids), Lead.conversation_id.isnot(None))
             .all())
    for lead in leads:
        conversation = session.query(Conversation).filter(Conversation.id == lead.conversation_id).first()
        if conversation is None:
            # The broker deleted the thread, so the contact is undone: the lead
            # leaves its campaign as well as the thread. Clearing only the
            # conversation would leave it hidden from the pool and listed under
            # a campaign that no longer has a message for it — workable from
            # nowhere. The campaign's own counts read from the messages, which
            # went with the thread.
            lead.conversation_id = None
            lead.campaign_id = None
            continue
        latest = (session.query(Message)
                  .filter(Message.conversation_id == conversation.id)
                  .order_by(Message.created_at.desc(), Message.id.desc())
                  .first())
        if latest is not None:
            lead.last_activity_at = latest.created_at
        if conversation.dnc_alert:
            lead.dnc = True
    session.commit()
