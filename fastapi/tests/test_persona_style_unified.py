"""One published Persona style for all three AI features.

Automatic SMS replies, Follow-up reply suggestions and Campaign reason
suggestions must each send the model the right agent's complete published
communication style (signature phrases, preferred and avoided vocabulary,
settings) and every owner-approved SMS example of the active version.

Fully offline: model calls are captured fakes, no SMS is sent. These tests prove
what reaches the first model request, not how well a real model imitates style.
"""

import json
import logging

import pytest

from app import db
from app.leads import campaign as campaign_service
from app.leads import reason_suggest
from app.models import Campaign, Conversation, Lead
from app.personas.models import PersonaVersionExample
from app.sms import prompt as prompt_module
from app.sms import reply_suggest
from app.sms.prompt import PERSONA_STYLE_RULES, PersonaContextOverflowError, persona_prompt_context
from app.sms.runtime_context import resolve_agent_runtime_context, resolve_user_runtime_context

from test_personal_ai_runtime_e2e import (  # noqa: F401  (fixtures are used by name)
    API,
    _conversation,
    offline,
    scripted,
)
from test_sms_ai_quality_reviewer import VALID, _disposition, _first_reply_request, _run_pipeline

SIGNATURE = ['Happy to take a look', "Let's find a time that works"]
PREFERRED = ["I'd be glad to help", 'no pressure at all']
AVOIDED = ['circle back', 'touch base']
UNNATURAL = ['synergy', 'Dear valued homeowner']


def _examples(prefix, count):
    return [{
        'scenario': f'{prefix} scenario {index:02d}', 'channel': 'sms', 'language': 'en',
        'customer_message': f'{prefix} customer {index:02d}',
        'preferred_response': f'{prefix}-EX-{index:02d} Happy to take a look whenever suits you.',
        'provenance': 'user_written',
    } for index in range(count)]


def _save_examples(client, headers, persona_id, revision, examples):
    """Save through the owner's form (bulk) and add the last example with its context and explanation."""
    *bulk, last = examples
    saved = client.put(f'{API}/{persona_id}/examples', headers=headers,
                       json={'revision': revision, 'examples': bulk})
    assert saved.status_code == 200, saved.text
    created = client.post(f'{API}/{persona_id}/examples', headers=headers, json={
        **last, 'revision': saved.json()['revision'], 'context': 'Owner asked about timing',
        'style_explanation': 'Warm and brief', 'owner_approved': True})
    assert created.status_code == 201, created.text
    return client.get(f'{API}/{persona_id}/draft', headers=headers).json()['revision']


def _style(signature=SIGNATURE, preferred=PREFERRED, avoided=AVOIDED, unnatural=UNNATURAL, tone='warm'):
    return {
        'style_config': {'tone': tone, 'greeting_preference': 'Hi {first name}',
                         'sign_off_preference': 'Talk soon', 'preferred_phrases': list(preferred),
                         'avoided_phrases': list(avoided)},
        'communication': {'signature_phrases': list(signature), 'unnatural_expressions': list(unnatural),
                          'emoji_usage': 'none'},
    }


def _publish(client, make_user, auth_header, email, brokerage, name, examples, style=None,
             brokerage_name='Style Realty', role='agent'):
    """Publish a Persona through the real owner API with the given style and examples."""
    style = style or _style()
    user = make_user(email, role=role, brokerage_id=brokerage, brokerage_name=brokerage_name)
    headers = auth_header(email)
    persona = client.post(API, headers=headers, json={'full_name': name}).json()
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    saved = client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={
        'revision': draft['revision'], 'professional_title': 'Broker',
        'biography': f'{name} biography', 'specialties': [f'{name} specialty'], 'service_areas': [f'{name} area'],
        'style_config': style['style_config'],
        'profile_config': {
            'identity': {'license_number': f'TEST-{user.id}', 'licensing_jurisdiction': 'Test State',
                         'licensing_status': 'Active'},
            'communication': style['communication'],
        },
    })
    assert saved.status_code == 200, saved.text
    revision = _save_examples(client, headers, persona['id'], saved.json()['revision'], examples)
    version = client.post(f"{API}/{persona['id']}/versions", headers=headers, json={'revision': revision})
    assert version.status_code in (200, 201), version.text
    return user, headers, persona, version.json()


def _republish(client, headers, persona_id, examples, style):
    draft = client.get(f'{API}/{persona_id}/draft', headers=headers).json()
    saved = client.patch(f'{API}/{persona_id}/draft', headers=headers, json={
        'revision': draft['revision'], 'style_config': style['style_config'],
        'profile_config': {**draft['profile_config'], 'communication': style['communication']}})
    assert saved.status_code == 200, saved.text
    revision = _save_examples(client, headers, persona_id, saved.json()['revision'], examples)
    version = client.post(f'{API}/{persona_id}/versions', headers=headers, json={'revision': revision})
    assert version.status_code in (200, 201), version.text
    return version.json()


def _persona_json(text):
    """The compact Persona JSON object that ends a system prompt."""
    start = text.rindex('{"active_published_version_id"')
    return json.loads(text[start:])


def _assert_style(persona, version_id, prefix, count, signature=SIGNATURE, preferred=PREFERRED):
    assert persona['active_published_version_id'] == version_id
    style = persona['communication_style']
    assert style['signature_phrases'] == list(signature)
    assert style['preferred_vocabulary'] == list(preferred)
    assert set(style['avoided_expressions']) == set(AVOIDED + UNNATURAL)
    assert style['style_settings']['tone'] == 'warm'
    assert style['style_settings']['greeting_preference'] == 'Hi {first name}'
    assert style['communication_profile']['emoji_usage'] == 'none'
    responses = sorted(example['preferred_response'] for example in persona['approved_examples'])
    assert responses == sorted(f'{prefix}-EX-{index:02d} Happy to take a look whenever suits you.'
                               for index in range(count))
    # Scenario, customer message, preferred response, context and explanation survive; no ids/provenance noise.
    assert all({'scenario', 'customer_message', 'preferred_response'} <= set(item)
               for item in persona['approved_examples'])
    explained = [item for item in persona['approved_examples'] if 'style_explanation' in item]
    assert explained == [{**explained[0], 'context': 'Owner asked about timing',
                          'style_explanation': 'Warm and brief'}]
    assert not any('provenance' in item or 'id' in item for item in persona['approved_examples'])


def _sms_request(monkeypatch, user_id, latest='Thanks for the message, tell me more.'):
    _sent, calls, _state = _run_pipeline(monkeypatch, user_id, latest, {
        'sms.disposition': [_disposition()],
        'sms.reply': ['Thanks for getting back to me. What would help most right now?'],
        'sms.review': [VALID],
    })
    return _first_reply_request(calls)


# ---------------------------------------------------------------------------
# Automatic SMS conversations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('count', [10, 35])
def test_automatic_sms_request_carries_every_approved_example_and_full_style(
        client, make_user, auth_header, monkeypatch, caplog, count):
    user, _h, _p, version = _publish(client, make_user, auth_header, f'sms{count}@example.com',
                                     f'b-sms-{count}', 'Avery Stone', _examples('SMS', count))
    caplog.set_level(logging.INFO, logger='app.sms.bobbie')
    request = _sms_request(monkeypatch, user.id)
    system = request['messages'][0]['content']
    _assert_style(_persona_json(system), version['id'], 'SMS', count)
    assert PERSONA_STYLE_RULES in system
    logs = [record.getMessage() for record in caplog.records if '[ai-persona]' in record.getMessage()]
    assert f'examples_included={count} examples_available={count}' in logs[0]
    assert 'signature_phrases=2 preferred_vocabulary=2 avoided_expressions=4' in logs[0]
    assert 'Happy to take a look' not in logs[0]  # counts only, never text


def test_seller_pricing_flow_is_unchanged_and_style_needs_no_knowledge_search(
        client, make_user, auth_header, monkeypatch):
    user, *_ = _publish(client, make_user, auth_header, 'seller-style@example.com', 'b-seller-style',
                        'Avery Stone', _examples('SELL', 12))
    searched = []
    monkeypatch.setattr('app.sms.knowledge.search_agent_knowledge',
                        lambda *a, **k: searched.append(a) or {'matches': []})
    request = _sms_request(monkeypatch, user.id, 'I just want to sell it quickly at best price like 675000$')
    assert searched == []
    assert [tool['function']['name'] for tool in request['options']['tools']] == ['get_lead_details']
    assert len(_persona_json(request['messages'][0]['content'])['approved_examples']) == 12


# ---------------------------------------------------------------------------
# Follow-ups → Reply Suggestions
# ---------------------------------------------------------------------------

def _reply_suggestion_request(monkeypatch, user_id, latest='Is the roof included in what you look at?'):
    captured = []

    def completion(messages, **options):
        captured.append({'messages': messages, **options})
        return {'role': 'assistant', 'content': json.dumps({'suggestions': [
            'Happy to take a look at the roof when I stop by.',
            "Let's find a time that works and I'll check the roof then.",
        ]})}

    monkeypatch.setattr(reply_suggest.ai_client, 'is_configured', lambda: True)
    monkeypatch.setattr(reply_suggest.ai_client, 'completion', completion)
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, contact=f'+1312777{user_id:04d}', history=[
            ('outbound', 'Hi, I am reaching out about 1 Test St.'), ('inbound', latest)])
        context = resolve_agent_runtime_context(session, conversation)
        result = reply_suggest.suggest(session, conversation, context)
    return result, captured


def test_reply_suggestions_use_the_same_complete_style_examples_and_latest_message(
        client, make_user, auth_header, monkeypatch):
    user, *_rest, version = _publish(client, make_user, auth_header, 'reply@example.com', 'b-reply',
                                     'Avery Stone', _examples('RPL', 35))
    result, captured = _reply_suggestion_request(monkeypatch, user.id)
    assert result['source'] == 'ai' and len(result['suggestions']) == 2
    assert len(captured) == 1 and captured[0]['feature'] == 'followups.reply_suggestions'
    system, user_text = captured[0]['messages'][0]['content'], captured[0]['messages'][1]['content']
    _assert_style(_persona_json(system), version['id'], 'RPL', 35)
    assert PERSONA_STYLE_RULES in system and "write every option in that agent's voice" in system
    assert 'LATEST OWNER MESSAGE (reference data):\nIs the roof included in what you look at?' in user_text
    assert 'OWNER [INBOUND]: Is the roof included in what you look at?' in user_text


def test_sms_and_reply_suggestions_receive_identical_style_and_examples(
        client, make_user, auth_header, monkeypatch):
    user, *_ = _publish(client, make_user, auth_header, 'same@example.com', 'b-same', 'Avery Stone',
                        _examples('SAME', 10))
    sms = _persona_json(_sms_request(monkeypatch, user.id)['messages'][0]['content'])
    _result, captured = _reply_suggestion_request(monkeypatch, user.id)
    reply = _persona_json(captured[0]['messages'][0]['content'])
    for key in ('active_published_version_id', 'identity', 'communication_style', 'sms_signature'):
        assert sms[key] == reply[key], key
    assert sorted(map(json.dumps, sms['approved_examples'])) == sorted(map(json.dumps, reply['approved_examples']))


# ---------------------------------------------------------------------------
# Campaign → AI Reason Suggestions
# ---------------------------------------------------------------------------

def _campaign(session, owner, signals='expired', count=3):
    campaign = Campaign(user_id=owner.id, name='Expired', status='draft',
                        message_template='Hi {{first_name}}, about {{address}}. {{reason}}')
    session.add(campaign)
    session.flush()
    session.add_all([Lead(user_id=owner.id, campaign_id=campaign.id, owner_name=f'Owner {n}',
                          phone=f'+1312{6600000 + owner.id * 10 + n:07d}',
                          property_address=f'{n} Style St, Chicago, IL', signals=signals, source='csv_import')
                     for n in range(count)])
    session.commit()
    return campaign


def _reason_ai(monkeypatch, suggestions):
    calls = []

    def completion(messages, **options):
        calls.append({'messages': messages, **options})
        return {'role': 'assistant', 'content': json.dumps({'suggestions': suggestions})}

    monkeypatch.setattr(reason_suggest.ai_client, 'is_configured', lambda: True)
    monkeypatch.setattr(reason_suggest.ai_client, 'completion', completion)
    return calls


STYLED = [
    "I noticed your property recently came off the market, and I'd be glad to help if you're still "
    'considering selling.',
    'Happy to take a look at your options now that the listing has ended. Are you still thinking of selling?',
]


def test_campaign_reason_suggestions_use_the_sending_agents_persona_not_a_colleagues(
        client, make_user, auth_header, monkeypatch):
    sender, *_s, sender_version = _publish(client, make_user, auth_header, 'sender@example.com', 'b-camp',
                                           'Avery Stone', _examples('CAMP', 35))
    colleague, *_c = _publish(client, make_user, auth_header, 'colleague@example.com', 'b-camp', 'Casey Lin',
                              _examples('COLL', 4),
                              style=_style(signature=['Cheers mate'], preferred=['Righto']))
    calls = _reason_ai(monkeypatch, STYLED)
    with db.SessionLocal() as session:
        sender_row = session.get(type(sender), sender.id)
        campaign = _campaign(session, colleague)  # created by the colleague, shared in the brokerage
        before = campaign_service.preview(session, sender_row, campaign)
        result = reason_suggest.suggest(session, sender_row, campaign, signal='expired')
        after = campaign_service.preview(session, sender_row, campaign)

    assert calls[0]['feature'] == 'campaigns.reason_suggestions.signal'
    system, user_text = calls[0]['messages'][0]['content'], calls[0]['messages'][1]['content']
    _assert_style(_persona_json(system), sender_version['id'], 'CAMP', 35)
    assert 'Cheers mate' not in system and 'COLL-EX' not in system
    assert 'The record types decide WHY' in system and PERSONA_STYLE_RULES in system
    # The verified signal is still what the request is about.
    assert 'Expired' in user_text and 'Expired' not in json.dumps(_persona_json(system))
    # Preferred wording plus the styled AI options; recipient grouping untouched.
    assert result['signals'] == [{'key': 'expired', 'label': 'Expired', 'count': 3, 'share': 1.0}]
    assert result['options'][0]['source'] == 'preferred'
    assert [option['text'] for option in result['options'][1:]] == STYLED
    assert before['reason_groups'] == after['reason_groups']


def test_campaign_whole_set_and_no_signal_prompts_carry_the_persona(client, make_user, auth_header, monkeypatch):
    sender, *_rest, version = _publish(client, make_user, auth_header, 'sets@example.com', 'b-sets',
                                       'Avery Stone', _examples('SET', 10))
    calls = _reason_ai(monkeypatch, ['I wanted to ask whether you would consider selling your property.',
                                     'I see your listing expired. Would you consider putting it back on the market?'])
    with db.SessionLocal() as session:
        sender_row = session.get(type(sender), sender.id)
        expired = _campaign(session, sender_row)
        reason_suggest.suggest(session, sender_row, expired)
        neutral = _campaign(session, sender_row, signals='')
        result = reason_suggest.suggest(session, sender_row, neutral, signal=reason_suggest.NO_SIGNAL_REASON_KEY)
    assert [call['feature'] for call in calls] == ['campaigns.reason_suggestions',
                                                   'campaigns.reason_suggestions.neutral']
    for call in calls:
        _assert_style(_persona_json(call['messages'][0]['content']), version['id'], 'SET', 10)
    # Persona style never loosens the neutral safety screen.
    assert result['suggestions'] == ['I wanted to ask whether you would consider selling your property.']


def test_campaign_without_a_published_persona_keeps_the_generic_prompt(make_user, monkeypatch):
    plain = make_user('plain@example.com', role='agent', brokerage_id='b-plain')
    calls = _reason_ai(monkeypatch, STYLED)
    with db.SessionLocal() as session:
        campaign = _campaign(session, plain)
        result = reason_suggest.suggest(session, session.get(type(plain), plain.id), campaign, signal='expired')
    assert 'SENDING AGENT PERSONA' not in calls[0]['messages'][0]['content']
    assert result['source'] == 'ai'


# ---------------------------------------------------------------------------
# Security and lifecycle
# ---------------------------------------------------------------------------

def test_unapproved_email_draft_and_other_agents_examples_never_reach_the_prompt(
        client, make_user, auth_header):
    owner, headers, persona, version = _publish(client, make_user, auth_header, 'iso@example.com', 'b-iso',
                                                'Avery Stone', _examples('OWN', 5))
    _publish(client, make_user, auth_header, 'peer@example.com', 'b-iso', 'Peer Agent', _examples('PEER', 3))
    _publish(client, make_user, auth_header, 'outsider@example.com', 'b-other', 'Out Sider',
             _examples('OUT', 3), brokerage_name='Other Realty')
    with db.SessionLocal() as session:
        for position, (channel, approved, marker) in enumerate(
                (('sms', 0, 'UNAPPROVED'), ('email', 1, 'EMAILONLY')), start=50):
            session.add(PersonaVersionExample(
                version_id=version['id'], position=position, scenario=marker, channel=channel, language='en',
                customer_message='', preferred_response=f'{marker}-EX', context='', style_explanation='',
                provenance='user_written', owner_approved=approved))
        session.commit()
    # A draft-only example (saved, not published) is not live.
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    assert client.put(f"{API}/{persona['id']}/examples", headers=headers, json={
        'revision': draft['revision'], 'examples': _examples('DRAFT', 2)}).status_code == 200
    with db.SessionLocal() as session:
        text = persona_prompt_context(resolve_user_runtime_context(session, session.get(type(owner), owner.id)))
    for absent in ('UNAPPROVED-EX', 'EMAILONLY-EX', 'DRAFT-EX', 'PEER-EX', 'OUT-EX'):
        assert absent not in text, absent
    assert len(json.loads(text)['approved_examples']) == 5


def test_publishing_a_new_version_changes_style_and_examples(client, make_user, auth_header, monkeypatch):
    user, headers, persona, v1 = _publish(client, make_user, auth_header, 'life@example.com', 'b-life',
                                          'Avery Stone', _examples('V1', 4))
    first = _persona_json(_sms_request(monkeypatch, user.id)['messages'][0]['content'])
    v2 = _republish(client, headers, persona['id'], _examples('V2', 6),
                    _style(signature=['Glad to walk you through it'], preferred=['quick look']))
    second = _persona_json(_sms_request(monkeypatch, user.id)['messages'][0]['content'])
    assert first['active_published_version_id'] == v1['id'] and second['active_published_version_id'] == v2['id']
    assert first['communication_style']['signature_phrases'] == SIGNATURE
    assert second['communication_style']['signature_phrases'] == ['Glad to walk you through it']
    assert second['communication_style']['preferred_vocabulary'] == ['quick look']
    assert all(example['preferred_response'].startswith('V2-EX') for example in second['approved_examples'])
    assert len(second['approved_examples']) == 6


def test_missing_persona_uses_the_existing_generic_behavior(make_user, monkeypatch):
    plain = make_user('noperso@example.com', role='agent', brokerage_id='b-none')
    with db.SessionLocal() as session:
        context = resolve_user_runtime_context(session, session.get(type(plain), plain.id))
    payload = json.loads(persona_prompt_context(context))
    assert payload['communication_style'] == {} and payload['approved_examples'] == []
    _result, captured = _reply_suggestion_request(monkeypatch, plain.id)
    assert _persona_json(captured[0]['messages'][0]['content'])['approved_examples'] == []


def test_an_oversized_persona_is_reported_never_silently_truncated(client, make_user, auth_header, monkeypatch):
    user, *_ = _publish(client, make_user, auth_header, 'big@example.com', 'b-big', 'Avery Stone',
                        _examples('BIG', 10))
    monkeypatch.setattr(prompt_module, 'PERSONA_STYLE_TOKEN_BUDGET', 200)
    with db.SessionLocal() as session:
        context = resolve_user_runtime_context(session, session.get(type(user), user.id))
        with pytest.raises(PersonaContextOverflowError) as raised:
            persona_prompt_context(context)
    assert raised.value.category == 'persona_context_overflow'
    # Reply suggestions fall back to the safe templates instead of a cut-down Persona.
    result, captured = _reply_suggestion_request(monkeypatch, user.id)
    assert captured == [] and result['source'] == 'template'
    # Campaign suggestions keep the preferred catalog wording.
    calls = _reason_ai(monkeypatch, STYLED)
    with db.SessionLocal() as session:
        result = reason_suggest.suggest(session, session.get(type(user), user.id),
                                        _campaign(session, user), signal='expired')
    assert calls == [] and result['source'] == 'catalog' and result['options'][0]['source'] == 'preferred'


def test_opted_out_owner_gets_no_reply_suggestions(client, make_user, auth_header):
    user, *_ = _publish(client, make_user, auth_header, 'dnc@example.com', 'b-dnc', 'Avery Stone',
                        _examples('DNC', 3), role='broker')
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id, contact='+13127770999',
                                     history=[('inbound', 'STOP')])
        session.get(Conversation, conversation.id).lead_status = 'dnc'
        session.commit()
        conversation_id = conversation.id
    response = client.post(f'/api/v1/followups/{conversation_id}/reply-suggestions',
                           headers=auth_header('dnc@example.com'))
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Advisory playbook, publish-time size check, visible oversize failures
# ---------------------------------------------------------------------------

PLAYBOOK = {
    'appointments': 'I like to offer Tuesday at 3pm for a walkthrough.',
    'objections': 'If they say not now, I thank them and leave the door open.',
    'pricing_disagreements': 'I explain that comparable sales guide the list price.',
    'human_handoff': 'Tell them I will personally call within the hour.',
    'sensitive_escalation': 'Escalate anything about divorce straight to me.',
}


def _add_playbook(client, headers, persona_id, playbook=PLAYBOOK):
    draft = client.get(f'{API}/{persona_id}/draft', headers=headers).json()
    saved = client.patch(f'{API}/{persona_id}/draft', headers=headers, json={
        'revision': draft['revision'], 'profile_config': {**draft['profile_config'], 'playbook': playbook}})
    assert saved.status_code == 200, saved.text
    version = client.post(f'{API}/{persona_id}/versions', headers=headers, json={'revision': saved.json()['revision']})
    assert version.status_code in (200, 201), version.text
    return version.json()


def test_advisory_playbook_reaches_sms_and_reply_drafts_but_not_campaign_reasons(
        client, make_user, auth_header, monkeypatch):
    user, headers, persona, _v1 = _publish(client, make_user, auth_header, 'play@example.com', 'b-play',
                                           'Avery Stone', _examples('PLAY', 3))
    _add_playbook(client, headers, persona['id'])
    sms_system = _sms_request(monkeypatch, user.id)['messages'][0]['content']
    _result, captured = _reply_suggestion_request(monkeypatch, user.id)
    reply_system = captured[0]['messages'][0]['content']
    for system in (sms_system, reply_system):
        playbook = _persona_json(system)['conversation_playbook']
        assert playbook['objections'] == PLAYBOOK['objections']
        assert playbook['appointments'] == PLAYBOOK['appointments']
        # Escalation and handoff stay with ListingIQ's own rules.
        assert 'human_handoff' not in playbook and 'sensitive_escalation' not in playbook
        assert 'never propose, offer, book or confirm a specific day or time' in system
    calls = _reason_ai(monkeypatch, STYLED)
    with db.SessionLocal() as session:
        reason_suggest.suggest(session, session.get(type(user), user.id), _campaign(session, user), signal='expired')
    assert 'conversation_playbook' not in calls[0]['messages'][0]['content']
    assert 'Tuesday at 3pm' not in calls[0]['messages'][0]['content']


def test_publishing_an_oversized_persona_is_refused_with_a_clear_reason(
        client, make_user, auth_header, monkeypatch):
    user, headers, persona, version = _publish(client, make_user, auth_header, 'toobig@example.com', 'b-toobig',
                                               'Avery Stone', _examples('BIG', 4))
    monkeypatch.setattr(prompt_module, 'PERSONA_PUBLISH_TOKEN_LIMIT', 600)
    monkeypatch.setattr(prompt_module, 'PERSONA_PUBLISH_WARNING_TOKENS', 300)
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    revision = _save_examples(client, headers, persona['id'], draft['revision'], _examples('MORE', 12))
    readiness = client.post(f"{API}/{persona['id']}/validate", headers=headers)
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()['valid'] is False
    assert any('too_large' in item for item in readiness.json()['errors'])
    refused = client.post(f"{API}/{persona['id']}/versions", headers=headers, json={'revision': revision})
    assert refused.status_code == 422
    assert 'too_large' in json.dumps(refused.json()) and 'Shorten or remove' in json.dumps(refused.json())
    # The previous version stays live; nothing was truncated.
    with db.SessionLocal() as session:
        assert resolve_user_runtime_context(session, session.get(type(user), user.id)).active_persona_version_id \
            == version['id']


def test_a_large_but_valid_persona_publishes_with_a_warning(client, make_user, auth_header, monkeypatch):
    from app.personas import service as persona_service
    monkeypatch.setattr(prompt_module, 'PERSONA_PUBLISH_WARNING_TOKENS', 100)
    user, *_rest, version = _publish(client, make_user, auth_header, 'warn@example.com', 'b-warn',
                                     'Avery Stone', _examples('WARN', 5))
    with db.SessionLocal() as session:
        from app.personas.models import PersonaDraft, PersonaDraftExample
        draft = session.query(PersonaDraft).filter_by(persona_id=_rest[1]['id']).one()
        rows = session.query(PersonaDraftExample).filter_by(persona_id=draft.persona_id).all()
        result = persona_service.validation_result(draft, rows)
    assert result['valid'] and any('every AI request carries all of them' in item for item in result['warnings'])
    assert version['id']


def test_sms_with_an_oversized_persona_sends_nothing_and_hands_over_visibly(
        client, make_user, auth_header, monkeypatch):
    from app.models import Notification
    user, *_ = _publish(client, make_user, auth_header, 'over@example.com', 'b-over', 'Avery Stone',
                        _examples('OVER', 10), role='broker')
    monkeypatch.setattr(prompt_module, 'PERSONA_STYLE_TOKEN_BUDGET', 200)
    with db.SessionLocal() as session:
        before = session.query(Notification.id).order_by(Notification.id.desc()).limit(1).scalar() or 0
    sent, calls, state = _run_pipeline(monkeypatch, user.id, 'Tell me more please.', {
        'sms.disposition': [_disposition()], 'sms.reply': [], 'sms.review': []})
    assert sent == [] and not any(call['feature'] == 'sms.reply' for call in calls)
    assert state['handled_by'] == 'broker' and state['ai_enabled'] is False
    with db.SessionLocal() as session:
        notes = session.query(Notification).filter(Notification.id > before,
                                                   Notification.user_id == user.id).all()
    assert notes and any('too large' in (note.message or '') for note in notes)


def test_oversized_persona_notes_are_specific_in_both_suggestion_features(
        client, make_user, auth_header, monkeypatch):
    user, *_ = _publish(client, make_user, auth_header, 'notes@example.com', 'b-notes', 'Avery Stone',
                        _examples('NOTE', 10))
    monkeypatch.setattr(prompt_module, 'PERSONA_STYLE_TOKEN_BUDGET', 200)
    result, _captured = _reply_suggestion_request(monkeypatch, user.id)
    assert result['source'] == 'template' and 'Persona is too large' in result['note']
    _reason_ai(monkeypatch, STYLED)
    with db.SessionLocal() as session:
        campaign_result = reason_suggest.suggest(session, session.get(type(user), user.id),
                                                 _campaign(session, user), signal='expired')
    assert 'Persona is too large' in campaign_result['note']
