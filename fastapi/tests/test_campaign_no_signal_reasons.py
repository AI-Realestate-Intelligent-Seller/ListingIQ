"""Production issue: a 500-property No Signal campaign got "AI returned only 0
safe usable suggestions", so no grounded reason could be confirmed before Send.

Claude answered, but the neutral filter was a word blocklist: "real estate"
matched `estate`, "consider listing it" matched `listing` and "put it up for
sale" matched `for sale`. Those are offers about a future sale, not claims about
the property. AI HTTP is faked; nothing is sent.
"""

import json
import logging
import re

import pytest

from app.integration_data.service import _lead_signals
from app.leads import campaign as campaign_service
from app.leads import reason_suggest
from app.models import Campaign, Lead, OutboundSmsJob

NO_SIGNAL = campaign_service.NO_SIGNAL_REASON_KEY
TEMPLATE = 'Hi {{first_name}}, about {{address}}. {{reason}}'
# The pre-fix filter, kept to prove the regression.
OLD_FILTER = re.compile(
    r'\b(list(?:ed|ing)?|expired|absentee|live elsewhere|foreclos|probate|equity|'
    r'vacan|tax(?:es)?|delinquen|distress|hardship|financial|estate|inherited|'
    r'for sale|off market|market record|public record)\b', re.I,
)
# Neutral offers the old blocklist rejected ("estate", "listing", "for sale").
OLD_REJECTED = [
    'Would real estate help be useful if you ever consider selling?',
    "I wanted to reach out about your property and see if you'd ever consider listing it.",
    "I'm reaching out regarding your property; would you consider putting it up for sale?",
]
# Wording the neutral prompt asks for: the purpose only, no sender introduction.
NEUTRAL = [
    'I wanted to ask whether you would consider selling your property.',
    "I wanted to reach out about your property and see if you'd ever consider listing it.",
    "I'm reaching out regarding your property; would you consider putting it up for sale?",
]
INTRODUCTIONS = [
    "I'm a local real estate agent, and I wanted to ask whether you would consider selling.",
    "I'm a broker reaching out to see if you'd consider selling your property.",
    'As a real estate professional, I wanted to ask if selling is something you would consider.',
    "I'm a local agent and wanted to ask whether you'd consider selling.",
]
UNSUBSTANTIATED = [
    'I see your listing expired. Would you consider putting it back on the market?',
    'As an out-of-state owner, have you thought about selling the property?',
    'I have cash buyers looking nearby. Would you sell?',
]


def _ai(monkeypatch, suggestions):
    calls = []

    def completion(messages, **options):
        calls.append({'messages': messages, **options})
        return {'role': 'assistant', 'content': json.dumps({'suggestions': suggestions})}

    monkeypatch.setattr(reason_suggest.ai_client, 'is_configured', lambda: True)
    monkeypatch.setattr(reason_suggest.ai_client, 'completion', completion)
    return calls


def _no_sms(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError('tests must not send SMS')
    monkeypatch.setattr('app.sms.service.send_sms', refuse)


def _campaign(session, owner, groups=None, snapshot=None):
    state = {'version': 1, 'groups': groups or {}}
    if snapshot is not None:
        state['reason_signal_snapshot'] = snapshot
    campaign = Campaign(user_id=owner.id, name='No Signal', status='draft',
                        message_template=TEMPLATE, reason_groups=json.dumps(state))
    session.add(campaign)
    session.flush()
    return campaign


def _leads(session, owner, campaign, count, signals='', start=0):
    leads = [Lead(user_id=owner.id, campaign_id=campaign.id, owner_name=f'Owner {n}',
                  phone=f'+1312{5500000 + n:07d}', property_address=f'{n} Neutral St, Chicago, IL',
                  signals=signals, source='csv_import')
             for n in range(start, start + count)]
    session.add_all(leads)
    session.commit()
    return leads


def _group(preview, key):
    return next((group for group in preview['reason_groups'] if group['key'] == key), None)


def test_the_old_filter_rejected_every_neutral_offer():
    assert all(OLD_FILTER.search(text) for text in OLD_REJECTED)  # reproduces "0 safe usable"
    assert all(reason_suggest.neutral_reason_problem(text) is None for text in OLD_REJECTED + NEUTRAL)


def test_ai_options_skip_sender_introductions(monkeypatch, make_user, session, caplog):
    owner = make_user('no-signal-intro@example.test', role='broker')
    campaign = _campaign(session, owner)
    _leads(session, owner, campaign, 3)
    calls = _ai(monkeypatch, INTRODUCTIONS[:2] + NEUTRAL[:1])
    caplog.set_level(logging.INFO, logger=reason_suggest.logger.name)

    result = reason_suggest.suggest(session, owner, campaign, signal=NO_SIGNAL)

    system = calls[0]['messages'][0]['content']
    assert 'never introduce yourself or say you are an agent, broker or real-estate professional' in system
    assert '"I wanted to ask whether you would consider selling your property."' in system
    assert 'You may say you are a local real-estate agent' not in system
    assert [option['text'] for option in result['options']] == NEUTRAL[:1]
    assert 'Removed 2 that were not neutral (sender introduction)' in result['note']
    assert ('reasons={"sender_introduction: i\'m a broker": 1, '
            '"sender_introduction: i\'m a local real estate agent": 1}') in caplog.text
    assert all(reason_suggest._screen_neutral([text])[0] == [] for text in INTRODUCTIONS)
    # Style rule for AI output only: a broker may still confirm their own wording.
    campaign_service.update_draft(session, owner, campaign,
                                  reason_group={'signal': NO_SIGNAL, 'reason': INTRODUCTIONS[0], 'confirmed': True})


def test_500_genuine_no_signal_recipients_get_a_neutral_reason_and_can_send(
        monkeypatch, make_user, session, caplog):
    owner = make_user('no-signal-500@example.test', role='broker')
    campaign = _campaign(session, owner)
    _leads(session, owner, campaign, 500)
    _no_sms(monkeypatch)

    before = campaign_service.preview(session, owner, campaign)
    assert [(g['key'], g['label'], g['count']) for g in before['reason_groups']] == [
        (NO_SIGNAL, 'No Signal', 500)]
    assert before['needs_review_count'] == 500
    assert len(before['recipients']) == 20  # preview pagination is unchanged

    calls = _ai(monkeypatch, NEUTRAL)
    caplog.set_level(logging.INFO, logger=reason_suggest.logger.name)
    result = reason_suggest.suggest(session, owner, campaign, signal=NO_SIGNAL)

    assert calls[0]['feature'] == 'campaigns.reason_suggestions.neutral'
    assert result['source'] == 'ai' and result['note'] == ''
    assert [option['text'] for option in result['options']] == NEUTRAL
    assert result['signals'] == [{'key': NO_SIGNAL, 'label': 'No Signal', 'count': 500, 'share': 1.0}]
    assert ('recipients=500 generated=3 parsed=3 accepted=3 rejected=0 reasons={}' in caplog.text)

    campaign_service.update_draft(session, owner, campaign,
                                  reason_group={'signal': NO_SIGNAL, 'reason': NEUTRAL[0], 'confirmed': True})
    after = campaign_service.preview(session, owner, campaign)
    assert after['needs_review_count'] == 0
    assert _group(after, NO_SIGNAL)['confirmed'] is True
    assert [g['key'] for g in after['reason_groups']] == [NO_SIGNAL]  # never shown as Expired/FSBO
    assert all(row['reason_source'] == 'confirmed_group' for row in after['recipients'])

    queued = campaign_service.send(session, owner, campaign)
    assert queued['queued'] == 500 and queued['skipped'] == []
    texts = [job.text for job in session.query(OutboundSmsJob).filter_by(campaign_id=campaign.id)]
    assert len(texts) == 500 and all(NEUTRAL[0] in text for text in texts)


def test_unsubstantiated_suggestions_are_rejected_with_named_reasons(
        monkeypatch, make_user, session, caplog):
    owner = make_user('no-signal-invalid@example.test', role='broker')
    campaign = _campaign(session, owner)
    _leads(session, owner, campaign, 3)
    _ai(monkeypatch, UNSUBSTANTIATED)
    caplog.set_level(logging.INFO, logger=reason_suggest.logger.name)

    result = reason_suggest.suggest(session, owner, campaign, signal=NO_SIGNAL)

    assert result['options'] == [] and result['suggestions'] == []
    assert result['note'].startswith('AI returned only 0 safe usable suggestions.')
    assert 'Removed 3 that were not neutral' in result['note']
    assert ('generated=3 parsed=3 accepted=0 rejected=3 reasons={"listing_status: your listing": 1, '
            '"ownership_or_residence: out-of-state": 1, "value_price_or_buyer: cash": 1}') in caplog.text
    assert not any(text in caplog.text for text in UNSUBSTANTIATED)  # counts only, no text logged

    for text in UNSUBSTANTIATED:
        with pytest.raises(ValueError, match='must stay neutral'):
            campaign_service.update_draft(session, owner, campaign,
                                          reason_group={'signal': NO_SIGNAL, 'reason': text, 'confirmed': True})
    session.rollback()
    assert campaign_service.preview(session, owner, campaign)['needs_review_count'] == 3


@pytest.mark.parametrize('text,problem', [
    ('Would you consider selling? Your old listing is gone.', 'listing_status'),
    ('I noticed the property and wanted to ask about selling.', 'observed_record'),
    ('Is the property empty? Would you sell?', 'vacancy'),
    ('Would you sell for top dollar?', 'value_price_or_buyer'),
    ('I handle estate sales. Would you consider selling?', 'distress_or_legal'),
    ('Hope you are well, have a great day.', 'not_about_selling'),
])
def test_claim_rules_name_the_problem(text, problem):
    assert reason_suggest.neutral_reason_problem(text).startswith(problem)


def test_mixed_signal_and_no_signal_campaign_keeps_groups_separate(monkeypatch, make_user, session):
    owner = make_user('mixed-signal@example.test', role='broker')
    campaign = _campaign(session, owner)
    expired = _leads(session, owner, campaign, 4, signals='expired')
    neutral = _leads(session, owner, campaign, 6, start=100)
    _no_sms(monkeypatch)
    _ai(monkeypatch, NEUTRAL)

    result = reason_suggest.suggest(session, owner, campaign, signal=NO_SIGNAL)
    assert result['signals'][0]['count'] == 6  # only the signal-less properties

    expired_reason = 'I see that your property is no longer listed, have you thought about relisting?'
    for signal, reason in ((NO_SIGNAL, NEUTRAL[1]), ('expired', expired_reason)):
        campaign_service.update_draft(session, owner, campaign,
                                      reason_group={'signal': signal, 'reason': reason, 'confirmed': True})
    preview = campaign_service.preview(session, owner, campaign)
    assert {(g['key'], g['count']) for g in preview['reason_groups']} == {('expired', 4), (NO_SIGNAL, 6)}
    assert [o['key'] for o in preview['signal_options']] == ['expired']  # No Signal is not a filterable signal
    assert preview['needs_review_count'] == 0

    assert campaign_service.send(session, owner, campaign)['queued'] == 10
    jobs = {job.lead_id: job.text for job in session.query(OutboundSmsJob).filter_by(campaign_id=campaign.id)}
    assert all(expired_reason in jobs[lead.id] for lead in expired)
    assert all(NEUTRAL[1] in jobs[lead.id] and 'listed' not in jobs[lead.id] for lead in neutral)


def test_existing_finalized_reasons_stay_unchanged(monkeypatch, make_user, session):
    owner = make_user('finalized@example.test', role='broker')
    multi_key = '1 Neutral St, Chicago, IL'
    from app.leads.address import canonical
    snapshot = {'finalized': True, 'properties': {canonical(multi_key): 'absentee_owner'}}
    groups = {'expired': {'reason': 'Stable expired reason', 'confirmed': True},
              'absentee_owner': {'reason': 'Stable absentee reason', 'confirmed': True}}
    campaign = _campaign(session, owner, groups=groups, snapshot=snapshot)
    _leads(session, owner, campaign, 1, signals='expired')
    multi = _leads(session, owner, campaign, 1, signals='expired,absentee_owner', start=1)[0]
    _leads(session, owner, campaign, 2, start=10)
    _ai(monkeypatch, NEUTRAL)

    reason_suggest.suggest(session, owner, campaign, signal=NO_SIGNAL)
    campaign_service.update_draft(session, owner, campaign,
                                  reason_group={'signal': NO_SIGNAL, 'reason': NEUTRAL[2], 'confirmed': True})

    state = json.loads(campaign.reason_groups)
    assert state['reason_signal_snapshot'] == snapshot
    assert state['groups']['expired'] == groups['expired']
    assert state['groups']['absentee_owner'] == groups['absentee_owner']
    assert campaign_service.resolve_reason(campaign, multi)[:2] == ('Stable absentee reason', 'confirmed_group')


def test_provider_category_keys_become_canonical_lead_signals():
    assert _lead_signals(['tax_default', 'reo', 'canceled', 'vacant_lot', 'active_auction',
                          'absentee', 'nod', 'vacant_property', 'expired', 'not_a_signal']) == [
        'tax_delinquent', 'reo_bank_owned', 'cancelled_mls_listing', 'vacant_land', 'auction',
        'absentee_owner', 'notice_of_default', 'vacant', 'expired']


# Lofty CRM tags Expired / fsbo / Canceled are listing-status signals (business decision).
def test_lofty_import_reads_canceled_expired_and_fsbo_tags_as_signals():
    from app.leads.importer import parse_csv
    text = ('Lofty Lead ID,First Name,Last Name,Phone 1,OK to Text,Property Address,Lead Type,Tags\n'
            'l-1,Ana,Diaz,(312) 555-0120,Yes,1 Tag St,Seller,"SM-Seller, Canceled, SM-Tier C"\n'
            'l-2,Bo,Lee,(312) 555-0121,Yes,2 Tag St,Seller,"fsbo, Expired"\n'
            'l-3,Cy,Ng,(312) 555-0122,Yes,3 Tag St,Seller,"SM-Seller, SM-Bounced"\n')
    rows, _warnings, _meta = parse_csv(text.encode('utf-8'))
    assert [row['signals'] for row in rows] == [['cancelled_mls_listing'], ['fsbo', 'expired'], []]


def _tagged(owner, n, tags, **extra):
    return Lead(user_id=owner.id, owner_name=f'Tagged {n}', phone=f'+1312{5600000 + n:07d}',
                property_address=f'{n} Tag St, Chicago, IL', signals='', source='csv_import',
                details=json.dumps({'lofty_lead_id': f'l-{n}', 'crm_tags': tags}), **extra)


def test_backfill_moves_tagged_draft_leads_out_of_no_signal(make_user, session):
    from app.leads.service import backfill_crm_tag_signals
    owner = make_user('crm-tags@example.test', role='broker')
    draft = _campaign(session, owner)
    sent = Campaign(user_id=owner.id, name='Sent', status='sent', message_template=TEMPLATE)
    session.add(sent)
    session.flush()
    canceled = _tagged(owner, 1, 'SM-Seller, Canceled', campaign_id=draft.id)
    both = _tagged(owner, 2, 'fsbo, Expired', campaign_id=draft.id)
    untagged = _tagged(owner, 3, 'SM-Seller, SM-Tier C', campaign_id=draft.id)
    contacted = _tagged(owner, 4, 'Expired', campaign_id=sent.id)
    session.add_all([canceled, both, untagged, contacted])
    session.commit()

    dry = backfill_crm_tag_signals(session)
    assert dry == {'checked': 4, 'updated': 2, 'skipped_already_contacted': 1,
                   'signals': {'cancelled_mls_listing': 1, 'fsbo': 1, 'expired': 1}}
    session.expire_all()
    assert canceled.signals == ''  # dry run writes nothing

    assert backfill_crm_tag_signals(session, apply=True)['updated'] == 2
    session.expire_all()
    assert (canceled.signals, canceled.main_signal) == ('cancelled_mls_listing', 'cancelled_mls_listing')
    assert both.signals == 'fsbo,expired' and both.main_signal is None
    assert untagged.signals == '' and contacted.signals == ''
    groups = {(g['key'], g['label'], g['count'])
              for g in campaign_service.preview(session, owner, draft)['reason_groups']}
    assert groups == {('cancelled_mls_listing', 'Cancelled', 1), ('fsbo', 'FSBO', 1),
                      ('expired', 'Expired', 1), (NO_SIGNAL, 'No Signal', 1)}
    assert backfill_crm_tag_signals(session, apply=True)['updated'] == 0  # idempotent
