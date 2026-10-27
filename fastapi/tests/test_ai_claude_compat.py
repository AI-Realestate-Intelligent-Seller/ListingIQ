"""Claude adapter compatibility, verified offline against recorded request bodies.

These tests check what ListingIQ would send to the Anthropic Messages API and
how Claude-shaped responses flow through the existing business logic. They use
fake HTTP only, so they prove request construction and normalization, not that
the live API accepts a request.
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

from app.ai import client as ai_client
from app.ai.errors import InvalidProviderRequestError
from app.ai.models import AIMessage, AIRequest, AIToolDefinition, GenerationOptions, Role
from app.ai.providers.anthropic import AnthropicProvider, provider_schema
from app.ai.schemas import (
    CALENDAR_ACTION_SCHEMA,
    DISPOSITION_SCHEMA,
    REASON_SUGGESTIONS_SCHEMA,
    REPLY_SUGGESTIONS_SCHEMA,
    REVIEW_SCHEMA,
)
from app.core.config import settings
from app.models import Conversation, Message
from app.sms import bobbie, service

UNSUPPORTED = {'minLength', 'maxLength', 'minimum', 'maximum', 'multipleOf', 'maxItems'}
CONVERSATION = SimpleNamespace(
    contact='+10000000000', name='Owner', property_address='1 Test St',
    lead_status='processing', lead_context=json.dumps({'available': True}),
)


def _keywords(schema):
    found = set(schema)
    for child in (schema.get('properties') or {}).values():
        found |= _keywords(child)
    if isinstance(schema.get('items'), dict):
        found |= _keywords(schema['items'])
    return found


def _text(content):
    return {'type': 'message', 'role': 'assistant', 'model': 'claude-haiku-5-5',
            'stop_reason': 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1},
            'content': [{'type': 'text', 'text': content}]}


@pytest.fixture
def provider_http(monkeypatch):
    """Capture every outbound provider request; replies are queued per test."""
    state = {'claude': [], 'deepseek': [], 'replies': []}

    class Reply:
        ok = True
        status_code = 200
        headers = {'request-id': 'req_offline'}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, json=None, headers=None, timeout=None):
        reply = state['replies'].pop(0) if len(state['replies']) > 1 else state['replies'][0]
        if url.endswith('/v1/messages'):
            state['claude'].append(json)
            return Reply(_text(reply) if isinstance(reply, str) else reply)
        state['deepseek'].append(json)
        return Reply({'model': json['model'], 'choices': [{
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': reply}}]})

    monkeypatch.setattr(requests, 'post', fake_post)
    monkeypatch.setitem(settings['ai'], 'api_key', 'offline-deepseek-key')
    monkeypatch.setitem(settings['ai'], 'anthropic_api_key', 'offline-anthropic-key')
    monkeypatch.setitem(settings['ai'], 'base_url', 'https://api.deepseek.com')
    monkeypatch.setitem(settings['ai'], 'anthropic_base_url', 'https://api.anthropic.com')
    monkeypatch.setattr(bobbie.knowledge, 'needs_bobbie_knowledge', lambda _text: False)
    return state


def use(monkeypatch, provider, model):
    monkeypatch.setitem(settings['ai'], 'provider', provider)
    monkeypatch.setitem(settings['ai'], 'model', model)


# --------------------------------------------------------------------------
# Schema translation
# --------------------------------------------------------------------------

@pytest.mark.parametrize('schema', [
    DISPOSITION_SCHEMA, REVIEW_SCHEMA, CALENDAR_ACTION_SCHEMA,
    REPLY_SUGGESTIONS_SCHEMA, REASON_SUGGESTIONS_SCHEMA,
], ids=['disposition', 'review', 'calendar', 'reply_suggestions', 'reason_suggestions'])
def test_feature_schemas_translate_to_anthropic_supported_keywords(schema):
    wire = provider_schema(schema)

    assert not _keywords(wire) & UNSUPPORTED
    assert wire['required'] == schema['required']
    assert wire['additionalProperties'] is False
    assert set(wire['properties']) == set(schema['properties'])
    for name, child in schema['properties'].items():
        assert wire['properties'][name].get('type') == child.get('type')
        assert wire['properties'][name].get('enum') == child.get('enum')
    # The removed limits are restated for the model, as the official SDKs do.
    assert schema is not wire and _keywords(schema) & UNSUPPORTED


def test_schema_translation_keeps_supported_array_minimum_and_states_limits():
    wire = provider_schema(REPLY_SUGGESTIONS_SCHEMA)['properties']['suggestions']

    assert wire['minItems'] == 0
    assert 'maxItems' not in wire
    assert wire['description'] == '{maxItems: 3}'
    assert wire['items'] == {'type': 'string', 'description': '{minLength: 1, maxLength: 320}'}
    assert 'location_discussion' in DISPOSITION_SCHEMA['properties']['lead_status']['enum']
    assert provider_schema(DISPOSITION_SCHEMA)['properties']['confidence'] == {
        'type': 'number', 'description': '{minimum: 0, maximum: 1}'}


# --------------------------------------------------------------------------
# Structured results are provider-neutral
# --------------------------------------------------------------------------

GOOD = {
    'action': 'continue', 'intent': 'price_question', 'outcome': 'positive',
    'lead_status': 'interested', 'confidence': 0.8, 'conversation_stage': 'qualified_for_call',
    'next_step': 'answer_and_qualify', 'qualification_focus': 'timeline',
    'calendar': {'state': 'none', 'should_fetch_availability': False,
                 'requested_time_text': '', 'reason': 'no call yet'},
    'reason': 'owner asked about price',
}
DISPOSITIONS = {
    'valid': GOOD,
    # Claude's constrained output still ignores the removed length limits.
    'over_length_text': {**GOOD, 'reason': 'r' * 400, 'intent': 'i' * 120,
                         'qualification_focus': 'q' * 300},
    'location_discussion': {**GOOD, 'lead_status': 'location_discussion'},
    'meeting_accepted': {**GOOD, 'calendar': {'state': 'call_accepted',
                                              'should_fetch_availability': True,
                                              'requested_time_text': '', 'reason': 'c' * 300}},
    'ending': {**GOOD, 'action': 'end', 'outcome': 'negative', 'lead_status': 'not_interested'},
}


@pytest.mark.parametrize('case', DISPOSITIONS, ids=list(DISPOSITIONS))
def test_disposition_result_is_identical_for_claude_and_deepseek(provider_http, monkeypatch, case):
    history = [{'direction': 'outbound', 'text': 'Hi from Bobbie.'},
               {'direction': 'inbound', 'text': 'Maybe, at the right price.'}]
    provider_http['replies'] = [json.dumps(DISPOSITIONS[case])]

    use(monkeypatch, 'deepseek', 'deepseek-v4-flash')
    via_deepseek = bobbie.analyze_conversation_disposition(CONVERSATION, history)
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')
    via_claude = bobbie.analyze_conversation_disposition(CONVERSATION, history)

    assert via_claude == via_deepseek
    assert 'fallback' not in via_claude['reason'].lower()
    assert len(via_claude['reason']) <= 240 and len(via_claude['intent']) <= 80


REVIEWS = {
    'approved': {'valid': True, 'issues': [], 'rewrite_instruction': '', 'reason': 'ok'},
    'rejected_over_length': {'valid': False, 'issues': ['x' * 150] * 9,
                             'rewrite_instruction': 'w' * 600, 'reason': 'r' * 300},
}


@pytest.mark.parametrize('case', REVIEWS, ids=list(REVIEWS))
def test_review_result_is_identical_for_claude_and_deepseek(provider_http, monkeypatch, case):
    history = [{'direction': 'inbound', 'text': 'What would it sell for?'}]
    provider_http['replies'] = [json.dumps(REVIEWS[case])]

    use(monkeypatch, 'deepseek', 'deepseek-v4-flash')
    via_deepseek = bobbie.review_ai_reply(CONVERSATION, history, 'Draft.', {})
    use(monkeypatch, 'anthropic', 'claude-sonnet-5-5')
    via_claude = bobbie.review_ai_reply(CONVERSATION, history, 'Draft.', {})

    assert via_claude == via_deepseek
    assert via_claude['valid'] is REVIEWS[case]['valid']
    assert len(via_claude['issues']) <= 6


def test_claude_receives_schema_while_deepseek_request_is_unchanged(provider_http, monkeypatch):
    provider_http['replies'] = [json.dumps(GOOD)]
    history = [{'direction': 'inbound', 'text': 'Maybe.'}]

    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')
    bobbie.analyze_conversation_disposition(CONVERSATION, history)
    use(monkeypatch, 'deepseek', 'deepseek-v4-flash')
    bobbie.analyze_conversation_disposition(CONVERSATION, history)

    claude, = provider_http['claude']
    deepseek, = provider_http['deepseek']
    assert claude['output_config']['format'] == {
        'type': 'json_schema', 'schema': provider_schema(DISPOSITION_SCHEMA)}
    assert not {'temperature', 'top_p', 'top_k'} & set(claude)
    assert 'response_format' not in deepseek and deepseek['temperature'] == 0


# --------------------------------------------------------------------------
# Conversation history sent to Claude
# --------------------------------------------------------------------------

MIXED_HISTORY = [
    {'direction': 'outbound', 'text': 'Hi, this is Bobbie about 1 Test St.'},   # outreach.initial
    {'direction': 'outbound', 'text': 'Just circling back.'},                  # ai.followup.1
    {'direction': 'inbound', 'text': 'Who is this?'},
    {'direction': 'outbound', 'text': 'Jordan from the team here.'},           # broker.message
    {'direction': 'inbound', 'text': ''},                                       # media-only reply
    {'direction': 'inbound', 'text': 'What would it sell for?'},
]


def test_every_history_type_reaches_claude_in_order_without_invented_turns(
        provider_http, monkeypatch):
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')
    provider_http['replies'] = ['Happy to put numbers together. What timeline suits you?']

    reply = bobbie.generate_ai_reply(CONVERSATION, MIXED_HISTORY)

    body, = provider_http['claude']
    assert reply == 'Happy to put numbers together. What timeline suits you?'
    assert body['messages'] == [
        {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Hi, this is Bobbie about 1 Test St.'}]},
        {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Just circling back.'}]},
        {'role': 'user', 'content': 'Who is this?'},
        {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Jordan from the team here.'}]},
        {'role': 'user', 'content': 'What would it sell for?'},
    ]
    # The persona, approved context and instructions stay in the system prompt.
    assert len(body['system']) == 2


def test_claude_request_never_ends_on_an_assistant_turn(provider_http, monkeypatch):
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')
    provider_http['replies'] = ['unused']
    history = [{'direction': 'outbound', 'text': 'Could I stop by?'},
               {'direction': 'inbound', 'text': ''}]

    with pytest.raises(ai_client.AiUnavailableError, match='must end with a user message'):
        bobbie.generate_ai_reply(CONVERSATION, history)
    assert provider_http['claude'] == [], 'rejected before any HTTP request'


def test_deepseek_still_receives_the_same_trailing_history(provider_http, monkeypatch):
    use(monkeypatch, 'deepseek', 'deepseek-v4-flash')
    provider_http['replies'] = ['Thanks for the photo. What would you like to know?']
    history = [{'direction': 'outbound', 'text': 'Could I stop by?'},
               {'direction': 'inbound', 'text': ''}]

    reply = bobbie.generate_ai_reply(CONVERSATION, history)

    assert reply == 'Thanks for the photo. What would you like to know?'
    assert provider_http['deepseek'][0]['messages'][-1] == {
        'role': 'assistant', 'content': 'Could I stop by?'}


def test_late_instruction_keeps_the_earlier_claude_request_unchanged(provider_http, monkeypatch):
    use(monkeypatch, 'anthropic', 'claude-sonnet-5-5')
    # An empty draft triggers Bobbie's one retry with an added instruction.
    provider_http['replies'] = ['', 'Fair question. Could I stop by to give you a real number?']
    history = [{'direction': 'outbound', 'text': 'Hi from Bobbie.'},
               {'direction': 'inbound', 'text': 'What would it sell for?'}]

    bobbie.generate_ai_reply(CONVERSATION, history)

    first, retry = provider_http['claude']
    instruction = _assert_follow_up_appends_only(first, retry)
    assert 'Return one non-empty, complete Bobbie SMS' in instruction


def test_tool_turn_and_thinking_blocks_are_replayed_unchanged(provider_http, monkeypatch):
    use(monkeypatch, 'anthropic', 'claude-sonnet-5-5')
    tool_turn = [
        {'type': 'thinking', 'thinking': '', 'signature': 'sig-1'},
        {'type': 'tool_use', 'id': 'toolu_1', 'name': 'get_lead_details', 'input': {}},
    ]
    provider_http['replies'] = [
        {**_text(''), 'content': tool_turn, 'stop_reason': 'tool_use'},
        'It is a 3-bedroom. What timing works for you?',
    ]
    history = [{'direction': 'inbound', 'text': 'How many bedrooms does it have?'}]

    reply = bobbie.generate_ai_reply(CONVERSATION, history)

    first, second = provider_http['claude']
    assert reply == 'It is a 3-bedroom. What timing works for you?'
    assert second['messages'][:len(first['messages'])] == first['messages']
    assert second['messages'][-2] == {'role': 'assistant', 'content': tool_turn}
    assert second['messages'][-1]['content'][0]['tool_use_id'] == 'toolu_1'
    assert second['system'] == first['system']


# --------------------------------------------------------------------------
# Complete request payloads across a reply, its tool loop, and follow-ups
# --------------------------------------------------------------------------

THINKING = {'claude-haiku-5-5': {'type': 'disabled'},
            'claude-sonnet-5-5': {'type': 'between_tools'}}
TOOL_TURN = [
    {'type': 'thinking', 'thinking': '', 'signature': 'sig-1'},
    {'type': 'redacted_thinking', 'data': 'opaque-2'},
    {'type': 'tool_use', 'id': 'toolu_lead', 'name': 'get_lead_details', 'input': {}},
    {'type': 'tool_use', 'id': 'toolu_kb', 'name': 'search_bobbie_knowledge', 'input': {'query': 'Bobbie'}},
]
TOOL_HISTORY = [{'direction': 'outbound', 'text': 'Hi from Bobbie.'},
                {'direction': 'inbound', 'text': 'Who is Bobbie, and how many bedrooms?'}]


def _changed_fields(before, after):
    return {key for key in set(before) | set(after) if before.get(key) != after.get(key)}


def _assert_follow_up_appends_only(before, after):
    """Everything before the follow-up instruction is byte-for-byte unchanged."""
    assert _changed_fields(before, after) == {'messages', 'tool_choice'}
    assert before['tool_choice'] == {'type': 'auto'}
    assert after['tool_choice'] == {'type': 'none'}
    assert after['messages'][:len(before['messages'])] == before['messages']
    appended = after['messages'][len(before['messages']):]
    assert len(appended) == 1 and appended[0]['role'] == 'system'
    return appended[0]['content']


@pytest.mark.parametrize('model', sorted(THINKING))
def test_ordinary_reply_payload(provider_http, monkeypatch, model):
    use(monkeypatch, 'anthropic', model)
    provider_http['replies'] = ['Happy to help. What timing works for you?']

    bobbie.generate_ai_reply(CONVERSATION, TOOL_HISTORY)

    body, = provider_http['claude']
    assert set(body) == {'model', 'max_tokens', 'messages', 'stream', 'thinking',
                         'output_config', 'system', 'tools', 'tool_choice'}
    assert body['model'] == model and body['max_tokens'] == 2048 and body['stream'] is False
    assert body['thinking'] == THINKING[model]
    assert body['output_config'] == {'effort': 'low'}
    assert body['tool_choice'] == {'type': 'auto'}
    assert [tool['name'] for tool in body['tools']] == ['get_lead_details', 'search_bobbie_knowledge']
    assert body['tools'] == [{'name': item['function']['name'],
                              'description': item['function']['description'],
                              'input_schema': item['function']['parameters']}
                             for item in bobbie.TOOLS]


@pytest.mark.parametrize('model', sorted(THINKING))
def test_multiple_tool_calls_payload(provider_http, monkeypatch, model):
    use(monkeypatch, 'anthropic', model)
    provider_http['replies'] = [
        {**_text(''), 'content': TOOL_TURN, 'stop_reason': 'tool_use'},
        'Happy to help. What timing works for you?',
    ]
    monkeypatch.setattr(bobbie.knowledge, 'search_bobbie_knowledge',
                        lambda query, limit: {'query': query, 'results': []})

    bobbie.generate_ai_reply(CONVERSATION, TOOL_HISTORY)

    first, second = provider_http['claude']
    assert _changed_fields(first, second) == {'messages'}
    assert second['messages'][:len(first['messages'])] == first['messages']
    assistant, results = second['messages'][len(first['messages']):]
    assert assistant == {'role': 'assistant', 'content': TOOL_TURN}
    assert results['role'] == 'user'
    assert [item['tool_use_id'] for item in results['content']] == ['toolu_lead', 'toolu_kb']


@pytest.mark.parametrize('model', sorted(THINKING))
@pytest.mark.parametrize('follow_up', ['empty_draft_retry', 'policy_repair'])
def test_follow_up_after_tool_calls_keeps_tools_system_and_history(
        provider_http, monkeypatch, model, follow_up):
    use(monkeypatch, 'anthropic', model)
    draft = '' if follow_up == 'empty_draft_retry' else 'x ' * 200  # empty, or over 240 chars
    provider_http['replies'] = [
        {**_text(''), 'content': TOOL_TURN, 'stop_reason': 'tool_use'},
        draft,
        'Short answer. What timing works for you?',
    ]
    monkeypatch.setattr(bobbie.knowledge, 'search_bobbie_knowledge',
                        lambda query, limit: {'query': query, 'results': []})

    reply = bobbie.generate_ai_reply(CONVERSATION, TOOL_HISTORY)

    first, after_tools, follow = provider_http['claude']
    assert reply == 'Short answer. What timing works for you?'
    assert _changed_fields(first, after_tools) == {'messages'}
    instruction = _assert_follow_up_appends_only(after_tools, follow)
    assert follow['tools'] == first['tools'] and follow['system'] == first['system']
    assert follow['thinking'] == THINKING[model]
    assert follow['messages'][len(first['messages'])] == {'role': 'assistant', 'content': TOOL_TURN}
    expected = 'Return one non-empty' if follow_up == 'empty_draft_retry' else 'Rewrite Bobbie'
    assert instruction.startswith(expected)


def test_forced_tool_choice_is_refused_before_http_for_models_that_reject_it():
    calls = []
    adapter = AnthropicProvider(api_key='offline', http_post=lambda *a, **k: calls.append(a))
    request = AIRequest(
        feature='test', operation_id='op', attempt_id='at', provider='anthropic',
        model='claude-opus-5-5', messages=(AIMessage(Role.USER, 'hi'),),
        tools=(AIToolDefinition('lookup', 'Look up', {'type': 'object', 'properties': {}}),),
        options=GenerationOptions(tool_choice='required'),
    )

    with pytest.raises(InvalidProviderRequestError, match='forced tool choice'):
        adapter.generate(request)
    assert calls == []


# --------------------------------------------------------------------------
# End-to-end SMS behaviour on Claude
# --------------------------------------------------------------------------

def _thread(session, make_user, *, handled_by='bobbie'):
    broker = make_user('claude-compat-broker@linchpinglobal.net', role='broker')
    conversation = Conversation(
        contact='+13125550199', user_id=broker.id, name='Owner',
        property_address='1 Test St', ai_enabled=handled_by == 'bobbie',
        handled_by=handled_by, lead_status='interested',
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    start = datetime.utcnow() - timedelta(minutes=5)
    session.add_all([
        Message(conversation_id=conversation.id, direction='outbound', text='Could I stop by?',
                status='delivered', event_type='outreach.initial', created_at=start),
        Message(conversation_id=conversation.id, direction='inbound', text='Sure, come by.',
                status='received', event_type='message.received',
                created_at=start + timedelta(minutes=1)),
    ])
    session.commit()
    return conversation


def test_claude_meeting_acceptance_hands_off_to_a_person(
        provider_http, monkeypatch, make_user, session):
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')
    conversation = _thread(session, make_user)
    provider_http['replies'] = [json.dumps(DISPOSITIONS['meeting_accepted'])]
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _s, _c, text, event_type, *rest:
                        sent.append((text, event_type)) or Message(id=1))

    service.process_ai_reply(conversation.id, 'Sure, come by.')

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert updated.handled_by == 'broker' and updated.ai_enabled is False
    assert updated.lead_status == 'location_discussion'
    assert len(sent) == 1 and 'someone from our team' in sent[0][0].lower()
    assert len(provider_http['claude']) == 1


def test_human_takeover_keeps_claude_out_of_the_thread(
        provider_http, monkeypatch, make_user, session):
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')
    conversation = _thread(session, make_user, handled_by='broker')
    provider_http['replies'] = [json.dumps(GOOD)]
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda *_a, **_k: pytest.fail('nothing may be sent during a takeover'))

    service.process_ai_reply(conversation.id, 'Sure, come by.')

    assert provider_http['claude'] == []
