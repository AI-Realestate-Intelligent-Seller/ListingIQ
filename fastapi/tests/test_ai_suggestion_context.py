"""High-signal contracts for the two user-facing AI suggestion features.

These tests stop at the provider boundary.  They prove which database facts are
selected and exactly what is sent to that boundary; they do not claim that a
language model will interpret those facts well.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.leads import reason_suggest
from app.models import Campaign, Conversation, Lead, Message, User
from app.sms import reply_suggest


def _user(session, email: str, brokerage_id: str = 'fictional-brokerage') -> User:
    user = User(
        email=email,
        hashed_password='unused',
        full_name='Fictional Tester',
        role='broker',
        brokerage_id=brokerage_id,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _conversation(session, user: User, *, contact: str, name: str,
                  address: str) -> Conversation:
    conversation = Conversation(
        user_id=user.id,
        contact=contact,
        name=name,
        property_address=address,
        lead_status='interested',
        lead_context='{}',
    )
    session.add(conversation)
    session.flush()
    return conversation


def test_reply_prompt_uses_exact_newest_20_eligible_messages_without_cross_thread_leak(
        session, monkeypatch):
    """One fixture covers selection, chronology, roles, repetition, and isolation."""
    user = _user(session, 'agent@fictional.example')
    conversation = _conversation(
        session, user, contact='+15550001001', name='Avery North',
        address='101 Cedar Way, Fiction City, IL 60001',
    )
    unrelated = _conversation(
        session, user, contact='+15550001002', name='Avery South',
        address='909 Birch Way, Fiction City, IL 60009',
    )
    base = datetime(2026, 1, 2, 9, 0, 0, tzinfo=timezone.utc)
    scenario_text = {
        5: 'I am interested in selling the Cedar property.',
        6: 'I can explain the next steps.',
        7: 'I am not interested at that price.',
        8: 'Would a higher price change your mind?',
        9: 'How many bedrooms does Cedar have?',
        10: 'The profile says three bedrooms.',
        11: 'I do not want an appointment.',
        12: 'Understood, I will not schedule one.',
        13: 'Please have a human call me.',
        14: 'A broker can call tomorrow.',
        15: 'I also own 202 Pine Road.',
        16: 'We can discuss each property separately.',
        17: 'Earlier I said yes to selling.',
        18: 'Thanks for clarifying.',
        19: 'Keep this repeated statement.',
        20: 'Keep this repeated statement.',
        21: 'What is the roof age? It is absent from my profile.',
        22: 'I will check rather than guess.',
        23: 'Actually, my latest preference is not to sell.',
        24: 'Can you confirm you understood my latest preference?',
    }
    target_messages = []
    for index in range(1, 25):
        message = Message(
            conversation_id=conversation.id,
            direction='inbound' if index % 2 else 'outbound',
            text=scenario_text.get(index, f'old context {index}'),
            created_at=base + timedelta(minutes=index),
        )
        target_messages.append(message)
        session.add(message)
    session.add_all([
        Message(conversation_id=conversation.id, direction='inbound', text='',
                created_at=base + timedelta(hours=2)),
        Message(conversation_id=conversation.id, direction='outbound', text=None,
                created_at=base + timedelta(hours=3)),
        Message(conversation_id=unrelated.id, direction='inbound',
                text='LEAK FROM ANOTHER CONVERSATION',
                created_at=base + timedelta(hours=4)),
    ])
    session.commit()

    captured = {}

    def fake_completion(messages, **options):
        captured['messages'] = messages
        captured['options'] = options
        return {'content': '{"suggestions":["I understood your latest preference."]}'}

    monkeypatch.setattr(reply_suggest.deepseek, 'is_configured', lambda: True)
    monkeypatch.setattr(reply_suggest.deepseek, 'completion', fake_completion)
    before = session.query(Message).count()

    result = reply_suggest.suggest(session, conversation)

    expected_lines = [
        f"{'Owner' if index % 2 else 'Agent'}: {scenario_text.get(index, f'old context {index}')}"
        for index in range(5, 25)
    ]
    expected_facts = json.dumps({
        'owner_name': 'Avery North',
        'current_property': '101 Cedar Way, Fiction City, IL 60001',
        'outreach_reason': None,
        'lead_status': 'interested',
        'meeting_booked': False,
        'upcoming_meeting': None,
        'properties': [{
            'property_address': '101 Cedar Way, Fiction City, IL 60001',
            'signals': [],
            'property_details': {},
        }],
    })
    expected_history = '\n'.join(expected_lines)
    expected_user_prompt = (
        f'PROPERTY FACTS:\n{expected_facts}\n\n'
        f'CONVERSATION (oldest first):\n{expected_history}\n\n'
        "Draft the agent's reply to the owner's latest message."
    )
    assert captured == {
        'messages': [
            {'role': 'system', 'content': reply_suggest.SYSTEM_PROMPT},
            {'role': 'user', 'content': expected_user_prompt},
        ],
        'options': {'temperature': 0.4, 'max_tokens': 400},
    }
    assert result == {
        'suggestions': ['I understood your latest preference.'],
        'source': 'ai',
        'note': '',
    }
    assert expected_lines.count('Owner: Keep this repeated statement.') == 1
    assert expected_lines.count('Agent: Keep this repeated statement.') == 1
    assert 'old context 4' not in expected_user_prompt
    assert 'LEAK FROM ANOTHER CONVERSATION' not in expected_user_prompt
    assert session.query(Message).count() == before


def test_reply_prompt_contains_exact_linked_property_facts_and_excludes_unrelated_leads(
        session, monkeypatch):
    user = _user(session, 'owner@fictional.example')
    conversation = _conversation(
        session, user, contact='+15550002001', name='Jordan Example',
        address='20 Current Lane, Fiction City, IL 60020',
    )
    conversation.lead_context = json.dumps({
        'outreach_reason': 'The current listing expired.',
        'other_properties': ['10 Prior Place, Fiction City, IL 60010'],
    })
    unrelated_conversation = _conversation(
        session, user, contact='+15550002002', name='Riley Unrelated',
        address='999 Unrelated Avenue, Fiction City, IL 60999',
    )
    # Insert the prior property first to prove the current property is explicitly promoted.
    session.add_all([
        Lead(
            user_id=user.id,
            conversation_id=conversation.id,
            owner_name='Jordan Example',
            phone='+15550002001',
            property_address='10 Prior Place, Fiction City, IL 60010',
            area='West Fiction',
            signals='high_equity',
            outreach_reason='Equity record.',
            details=json.dumps({'Bedrooms': 2, 'Estimated Value': 210000}),
        ),
        Lead(
            user_id=user.id,
            conversation_id=conversation.id,
            owner_name='Jordan Example',
            phone='+15550002001',
            property_address='20 Current Lane, Fiction City, IL 60020',
            area=None,
            signals='expired,pre_foreclosure',
            outreach_reason='The listing expired.',
            details=json.dumps({
                'Bedrooms': 4,
                'Year Built': 1978,
                '_phone_numbers': [{'phone': '+15559999999'}],
                '_campaign_original': {'private': 'not a property fact'},
            }),
        ),
        Lead(
            user_id=user.id,
            conversation_id=unrelated_conversation.id,
            owner_name='Riley Unrelated',
            phone='+15550002002',
            property_address='999 Unrelated Avenue, Fiction City, IL 60999',
            signals='probate',
            details=json.dumps({'Bedrooms': 99}),
        ),
        Message(
            conversation_id=conversation.id,
            direction='inbound',
            text='Which details do you have for the current property?',
            created_at=datetime(2026, 2, 3, 12, 0, 0, tzinfo=timezone.utc),
        ),
    ])
    session.commit()
    captured = {}

    def fake_completion(messages, **_options):
        captured['request'] = messages
        return {'content': '{"suggestions":["I have the imported details in front of me."]}'}

    monkeypatch.setattr(reply_suggest.deepseek, 'is_configured', lambda: True)
    monkeypatch.setattr(reply_suggest.deepseek, 'completion', fake_completion)

    reply_suggest.suggest(session, conversation)

    user_prompt = captured['request'][1]['content']
    facts_json = user_prompt.split('PROPERTY FACTS:\n', 1)[1].split(
        '\n\nCONVERSATION (oldest first):', 1)[0]
    assert json.loads(facts_json) == {
        'owner_name': 'Jordan Example',
        'current_property': '20 Current Lane, Fiction City, IL 60020',
        'outreach_reason': 'The current listing expired.',
        'lead_status': 'interested',
        'meeting_booked': False,
        'upcoming_meeting': None,
        'properties': [
            {
                'property_address': '20 Current Lane, Fiction City, IL 60020',
                'area': None,
                'owner_name': 'Jordan Example',
                'signals': ['Expired', 'Pre-Foreclosure'],
                'sensitive_signals': ['Pre-Foreclosure'],
                'outreach_reason': 'The listing expired.',
                'property_details': {'Bedrooms': 4, 'Year Built': 1978},
            },
            {
                'property_address': '10 Prior Place, Fiction City, IL 60010',
                'area': 'West Fiction',
                'owner_name': 'Jordan Example',
                'signals': ['High Equity'],
                'sensitive_signals': [],
                'outreach_reason': 'Equity record.',
                'property_details': {'Bedrooms': 2, 'Estimated Value': 210000},
            },
        ],
        'other_properties_discussed': ['10 Prior Place, Fiction City, IL 60010'],
    }
    assert '999 Unrelated Avenue' not in user_prompt
    assert '+15559999999' not in user_prompt
    assert '_campaign_original' not in user_prompt


def test_reply_history_character_bound_is_the_exact_recent_tail():
    messages = [
        Message(direction='inbound' if index % 2 else 'outbound',
                text=f'message-{index:02d}-' + chr(64 + index) * 500)
        for index in range(1, 21)
    ]
    full = '\n'.join(
        f"{'Owner' if item.direction == 'inbound' else 'Agent'}: {item.text}"
        for item in messages
    )

    bounded = reply_suggest._history_text(messages)

    assert bounded == full[-reply_suggest.MAX_HISTORY_CHARS:]
    assert len(bounded) == reply_suggest.MAX_HISTORY_CHARS
    assert bounded.endswith('message-20-' + 'T' * 500)


def test_reply_output_parser_and_unusable_provider_fallback_are_deterministic(
        session, monkeypatch):
    overlong = 'x' * (reply_suggest.MAX_SUGGESTION_CHARS + 25)
    assert reply_suggest._parse('{"suggestions":["Only one."]}') == ['Only one.']
    assert reply_suggest._parse(
        '{"suggestions":["First of two.","Second of two."]}',
    ) == ['First of two.', 'Second of two.']
    assert reply_suggest._parse(json.dumps({
        'suggestions': ['  First option.  ', '', None, 42, 'First option.', overlong,
                        'Fourth option is bounded out.'],
    })) == ['First option.', 'x' * reply_suggest.MAX_SUGGESTION_CHARS,
            'Fourth option is bounded out.']

    user = _user(session, 'fallback@fictional.example')
    conversation = _conversation(
        session, user, contact='+15550003001', name='Taylor Example',
        address='30 Fallback Street, Fiction City, IL 60030',
    )
    session.add(Message(
        conversation_id=conversation.id,
        direction='inbound',
        text='What price is the property worth?',
        created_at=datetime(2026, 3, 4, 8, 0, 0, tzinfo=timezone.utc),
    ))
    session.commit()
    monkeypatch.setattr(reply_suggest.deepseek, 'is_configured', lambda: True)
    monkeypatch.setattr(
        reply_suggest.deepseek, 'completion',
        lambda *_args, **_kwargs: {'content': '{"suggestions":[null,"  ",7]}'},
    )

    first = reply_suggest.suggest(session, conversation)
    second = reply_suggest.suggest(session, conversation)

    assert first == second == {
        'suggestions': [
            'Happy to put real numbers together for you. What price would make it worth it?',
            'Fair question. Could I stop by and take a quick look so I can give you a real answer?',
            'Are you mainly curious about value, or thinking about selling soon?',
        ],
        'source': 'template',
        'note': 'AI returned no usable suggestions, so these are safe reply templates.',
    }


def test_campaign_prompt_uses_exact_tenant_scoped_membership_counts_and_no_personal_data(
        session, monkeypatch):
    owner = _user(session, 'owner@north.example', 'north-brokerage')
    teammate = _user(session, 'teammate@north.example', 'north-brokerage')
    outsider = _user(session, 'outsider@south.example', 'south-brokerage')
    campaign = Campaign(user_id=owner.id, name='North signals', status='draft')
    other_campaign = Campaign(user_id=owner.id, name='Other campaign', status='draft')
    session.add_all([campaign, other_campaign])
    session.flush()
    session.add_all([
        Lead(user_id=owner.id, campaign_id=campaign.id, owner_name='Casey One',
             phone='+15551000001', property_address='1 Private Path', area='North District',
             signals='expired,high_equity'),
        Lead(user_id=teammate.id, campaign_id=campaign.id, owner_name='Casey Two',
             phone='+15551000002', property_address='2 Private Path', area='North District',
             signals='expired,probate'),
        Lead(user_id=owner.id, campaign_id=campaign.id, owner_name='Casey Three',
             phone='+15551000003', property_address='3 Private Path', area='North District',
             signals='high_equity'),
        Lead(user_id=outsider.id, campaign_id=campaign.id, owner_name='TENANT LEAK',
             phone='+15551999999', property_address='999 Tenant Leak Road',
             area='South District', signals='divorce'),
        Lead(user_id=owner.id, campaign_id=other_campaign.id, owner_name='CAMPAIGN LEAK',
             phone='+15551888888', property_address='888 Campaign Leak Road',
             area='Other District', signals='vacant'),
    ])
    session.commit()
    captured = {}

    def fake_completion(messages, **options):
        captured['messages'] = messages
        captured['options'] = options
        return {'content': json.dumps({'suggestions': ['Model one.', 'Model two.', 'Model three.']})}

    monkeypatch.setattr(reason_suggest.deepseek, 'is_configured', lambda: True)
    monkeypatch.setattr(reason_suggest.deepseek, 'completion', fake_completion)

    result = reason_suggest.suggest(session, owner, campaign)

    expected_profile = [
        {'key': 'expired', 'label': 'Expired', 'count': 2, 'share': 0.667},
        {'key': 'high_equity', 'label': 'High Equity', 'count': 2, 'share': 0.667},
        {'key': 'probate', 'label': 'Probate', 'count': 1, 'share': 0.333},
    ]
    assert result == {
        'signals': expected_profile,
        'suggestions': [
            'Model one.', 'Model two.', 'Model three.',
            reason_suggest._CATALOG_SUGGESTIONS['expired'][0],
        ],
        'source': 'ai',
        'note': '',
    }
    assert captured['messages'][1]['content'] == (
        'This campaign texts 3 property owners in North District.\n'
        'What the records show across the set:\n'
        '- Expired: 2 of 3 leads\n'
        '- High Equity: 2 of 3 leads\n'
        '- Probate: 1 of 3 leads\n\n'
        'The set is mainly about: Expired, High Equity.\n'
        'Write 3 alternative reasons.'
    )
    assert captured['options'] == {'temperature': 0.7}
    provider_text = json.dumps(captured['messages'])
    for prohibited in (
        'Casey One', 'Casey Two', 'Casey Three', '+15551000001',
        '1 Private Path', 'TENANT LEAK', 'CAMPAIGN LEAK', 'Divorce', 'Vacant',
    ):
        assert prohibited not in provider_text


def test_campaign_empty_signals_and_malformed_ai_output_use_exact_catalog_fallbacks(
        session, monkeypatch):
    owner = _user(session, 'campaign-fallback@fictional.example')
    empty_campaign = Campaign(user_id=owner.id, name='No members', status='draft')
    empty_profile_campaign = Campaign(user_id=owner.id, name='No signals', status='draft')
    signaled_campaign = Campaign(user_id=owner.id, name='Expired', status='draft')
    session.add_all([empty_campaign, empty_profile_campaign, signaled_campaign])
    session.flush()
    session.add_all([
        Lead(user_id=owner.id, campaign_id=empty_profile_campaign.id,
             owner_name='No Signal', phone='+15552000001',
             property_address='1 Empty Way', signals=''),
        Lead(user_id=owner.id, campaign_id=signaled_campaign.id,
             owner_name='Expired Signal', phone='+15552000002',
             property_address='2 Signal Way', signals='expired'),
    ])
    session.commit()

    monkeypatch.setattr(reason_suggest.deepseek, 'is_configured', lambda: True)
    no_signal_result = {
        'signals': [],
        'suggestions': reason_suggest._GENERIC_SUGGESTIONS,
        'source': 'catalog',
        'note': 'No signals on these leads to go on.',
    }
    assert reason_suggest.suggest(session, owner, empty_campaign) == no_signal_result
    assert reason_suggest.suggest(session, owner, empty_profile_campaign) == no_signal_result

    monkeypatch.setattr(
        reason_suggest.deepseek, 'completion',
        lambda *_args, **_kwargs: {'content': 'not valid JSON'},
    )
    fallback = reason_suggest._catalog_suggestions([{
        'key': 'expired', 'label': 'Expired', 'count': 1, 'share': 1.0,
    }])
    assert reason_suggest.suggest(session, owner, signaled_campaign) == {
        'signals': [{'key': 'expired', 'label': 'Expired', 'count': 1, 'share': 1.0}],
        'suggestions': fallback,
        'source': 'catalog',
        'note': 'DeepSeek returned nothing usable, so these are the standard wordings.',
    }
