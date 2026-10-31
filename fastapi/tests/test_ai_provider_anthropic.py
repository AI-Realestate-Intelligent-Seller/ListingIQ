"""Offline contracts for the Anthropic adapter and neutral feature gateway."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests

from app.ai import client as ai_client
from app.ai.errors import (
    AuthenticationError,
    InvalidProviderRequestError,
    MalformedProviderResponseError,
    PermissionDeniedError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    ToolCallingError,
    UnsupportedCapabilityError,
)
from app.ai.models import (
    AIMessage,
    AIRequest,
    AIToolDefinition,
    GenerationOptions,
    Role,
    StructuredOutputMode,
    TokenUsage,
)
from app.ai.providers.anthropic import AnthropicProvider, provider_schema
from app.ai.registry import ProviderRegistry
from app.ai.service import AIService, ai_service
from app.leads import reason_suggest
from app.models import Campaign, Conversation, Lead, Message, User
from app.sms import bobbie, reply_suggest
from test_ai_review_evidence import LEGACY_AGENT  # noqa: E402  trusted test identity


class FakeResponse:
    def __init__(self, payload=None, *, status=200, headers=None, json_error=False):
        self.payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError('not json')
        return self.payload


def anthropic_request(**changes):
    base = AIRequest(
        feature='test.anthropic',
        operation_id='operation-1',
        attempt_id='attempt-1',
        correlation_id='correlation-1',
        provider='anthropic',
        model='claude-haiku-5-5',
        messages=(AIMessage(Role.USER, 'Hello Claude'),),
        timeout_seconds=9.5,
    )
    return replace(base, **changes)


def anthropic_service(post, *, sink=lambda _event: None, api_key='offline-key'):
    registry = ProviderRegistry()
    registry.register('anthropic', AnthropicProvider(
        api_key=api_key,
        base_url='https://anthropic.invalid/',
        http_post=post,
    ))
    return AIService(registry, event_sink=sink)


def message_payload(*, content=None, model='claude-haiku-5-5', stop_reason='end_turn',
                    usage=None):
    return {
        'id': 'msg_offline_1',
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': content if content is not None else [
            {'type': 'text', 'text': 'Hello from Claude'},
        ],
        'stop_reason': stop_reason,
        'stop_sequence': None,
        'usage': usage if usage is not None else {
            'input_tokens': 20,
            'output_tokens': 7,
            'cache_creation_input_tokens': 3,
            'cache_read_input_tokens': 5,
        },
    }


UNSUPPORTED_SCHEMA_KEYWORDS = {'minLength', 'maxLength', 'minimum', 'maximum', 'multipleOf', 'maxItems'}


def _schema_keywords(schema):
    found = set(schema)
    for child in (schema.get('properties') or {}).values():
        found |= _schema_keywords(child)
    if isinstance(schema.get('items'), dict):
        found |= _schema_keywords(schema['items'])
    return found


def assert_wire_schema(body, feature_schema):
    """The request carries the Anthropic-compatible form of the feature schema."""
    wire = body['output_config']['format']['schema']
    assert wire == provider_schema(feature_schema)
    assert not _schema_keywords(wire) & UNSUPPORTED_SCHEMA_KEYWORDS


@pytest.mark.parametrize('model', ['claude-haiku-5-5', 'claude-sonnet-5-5'])
def test_anthropic_text_generation_supports_both_configured_models(model):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return FakeResponse(
            message_payload(model=model), headers={'request-id': 'req-offline-1'},
        )

    response = anthropic_service(post).generate_text(anthropic_request(
        model=model,
        system_instructions=('Keep the answer concise.',),
        max_output_tokens=120,
        options=GenerationOptions(
            temperature=0.4,
            top_p=0.9,
            stop=('DONE',),
        ),
    ))

    assert seen['url'] == 'https://anthropic.invalid/v1/messages'
    assert seen['timeout'] == 9.5
    assert seen['headers'] == {
        'Authorization': 'Bearer offline-key',
        'anthropic-version': '2026-10-21',
        'Content-Type': 'application/json',
    }
    expected = {
        'model': model,
        'max_tokens': 120,
        'messages': [{'role': 'user', 'content': 'Hello Claude'}],
        'stream': False,
        'system': [{'type': 'text', 'text': 'Keep the answer concise.'}],
        'stop_sequences': ['DONE'],
    }
    expected['thinking'] = {
        'type': 'disabled' if model == 'claude-haiku-5-5' else 'between_tools',
    }
    expected['output_config'] = {'effort': 'low'}
    assert seen['json'] == expected
    assert response.text == 'Hello from Claude'
    assert response.model == model
    assert response.provider_request_id == 'req-offline-1'


def test_anthropic_maps_multiturn_roles_and_omits_unsupported_sampling_values():
    seen = {}

    def post(_url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(message_payload())

    anthropic_service(post).generate(anthropic_request(
        messages=(
            AIMessage(Role.SYSTEM, 'Second system instruction.'),
            AIMessage(Role.USER, 'First owner turn.'),
            AIMessage(Role.ASSISTANT, 'First assistant turn.'),
            AIMessage(Role.USER, 'Newest owner turn.'),
        ),
        system_instructions=('Primary system instruction.',),
        options=GenerationOptions(temperature=0, top_p=0.5),
    ))

    assert seen['json']['system'] == [
        {'type': 'text', 'text': 'Primary system instruction.'},
        {'type': 'text', 'text': 'Second system instruction.'},
    ]
    assert seen['json']['messages'] == [
        {'role': 'user', 'content': 'First owner turn.'},
        {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'First assistant turn.'},
        ]},
        {'role': 'user', 'content': 'Newest owner turn.'},
    ]
    assert 'temperature' not in seen['json']
    assert 'top_p' not in seen['json']


def test_anthropic_native_structured_output_uses_output_config_and_validates():
    seen = {}
    schema = {
        'type': 'object',
        'properties': {'answer': {'type': 'string'}},
        'required': ['answer'],
        'additionalProperties': False,
    }

    def post(_url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(message_payload(content=[{
            'type': 'text', 'text': '{"answer":"grounded"}',
        }]))

    response = anthropic_service(post).generate_structured(anthropic_request(
        output_schema=schema,
        structured_output_mode=StructuredOutputMode.NATIVE_REQUIRED,
    ))

    assert seen['json']['output_config'] == {
        'effort': 'low',
        'format': {'type': 'json_schema', 'schema': schema},
    }
    assert response.structured == {'answer': 'grounded'}
    assert response.structured_output_valid is True


def test_anthropic_multiple_tool_calls_preserve_blocks_ids_results_and_thinking():
    requests_seen = []
    first_content = [
        {'type': 'thinking', 'thinking': 'private reasoning', 'signature': 'sig-1'},
        {'type': 'text', 'text': 'I will check.'},
        {'type': 'tool_use', 'id': 'tool-lead', 'name': 'get_lead_details', 'input': {}},
        {'type': 'tool_use', 'id': 'tool-knowledge', 'name': 'search_agent_knowledge',
         'input': {'query': 'commission'}},
    ]
    responses = iter([
        FakeResponse(message_payload(content=first_content, stop_reason='tool_use')),
        FakeResponse(message_payload(content=[{
            'type': 'text', 'text': 'The tools supplied the approved context.',
        }])),
    ])

    def post(_url, **kwargs):
        requests_seen.append(kwargs['json'])
        return next(responses)

    service = anthropic_service(post)
    tools = (
        AIToolDefinition('get_lead_details', 'Read approved lead details', {
            'type': 'object', 'properties': {},
        }),
        AIToolDefinition('search_agent_knowledge', 'Search approved knowledge', {
            'type': 'object',
            'properties': {'query': {'type': 'string'}},
            'required': ['query'],
        }),
    )
    first = service.generate_with_tools(anthropic_request(
        tools=tools,
        options=GenerationOptions(tool_choice='required'),
    ))
    assert [call.id for call in first.tool_calls] == ['tool-lead', 'tool-knowledge']
    assert [call.name for call in first.tool_calls] == [
        'get_lead_details', 'search_agent_knowledge',
    ]
    assert first.provider_metadata['_provider_state'] == first_content
    assert requests_seen[0]['tool_choice'] == {'type': 'any'}

    second = service.generate(anthropic_request(
        messages=(
            AIMessage(Role.USER, 'Please check the approved context.'),
            AIMessage(
                Role.ASSISTANT,
                first.text,
                tool_calls=first.tool_calls,
                provider_state=first.provider_metadata['_provider_state'],
            ),
            AIMessage(Role.TOOL, '{"available":true}', 'tool-lead'),
            AIMessage(Role.TOOL, '{"error":"offline index unavailable"}', 'tool-knowledge'),
        ),
        tools=tools,
    ))

    continuation = requests_seen[1]['messages']
    assert continuation[1] == {'role': 'assistant', 'content': first_content}
    assert continuation[2]['role'] == 'user'
    assert [block['tool_use_id'] for block in continuation[2]['content']] == [
        'tool-lead', 'tool-knowledge',
    ]
    assert 'is_error' not in continuation[2]['content'][0]
    assert continuation[2]['content'][1]['is_error'] is True
    assert second.text == 'The tools supplied the approved context.'


def test_sonnet_5_5_rejects_forced_tool_choice_before_http():
    request = anthropic_request(
        model='claude-sonnet-5-5',
        tools=(AIToolDefinition('lookup', 'Lookup', {
            'type': 'object', 'properties': {},
        }),),
        options=GenerationOptions(tool_choice='required'),
    )
    with pytest.raises(InvalidProviderRequestError, match='forced tool choice'):
        anthropic_service(
            lambda *_args, **_kwargs: pytest.fail('HTTP must not run'),
        ).generate_with_tools(request)


def test_anthropic_normalizes_usage_cache_tokens_stop_reason_and_instrumentation():
    events = []
    response = anthropic_service(
        lambda *_args, **_kwargs: FakeResponse(
            message_payload(stop_reason='max_tokens'),
            headers={'Request-ID': 'request-42'},
        ),
        sink=events.append,
    ).generate(anthropic_request())

    assert response.usage == TokenUsage(20, 7, 35, 5, 3, 'provider')
    assert response.stop_reason == 'max_tokens'
    assert response.provider == 'anthropic'
    assert response.provider_request_id == 'request-42'
    assert response.latency_ms >= 0
    assert [event.status for event in events] == ['started', 'success']
    assert (
        events[-1].input_tokens,
        events[-1].output_tokens,
        events[-1].total_tokens,
        events[-1].cache_read_tokens,
        events[-1].cache_write_tokens,
        events[-1].usage_source,
    ) == (20, 7, 35, 5, 3, 'provider')


def test_anthropic_missing_credentials_fail_before_http():
    service = anthropic_service(
        lambda *_args, **_kwargs: pytest.fail('HTTP must not run'), api_key='',
    )
    with pytest.raises(ProviderConfigurationError):
        service.generate(anthropic_request())


@pytest.mark.parametrize(('status', 'error_type'), [
    (400, InvalidProviderRequestError),
    (401, AuthenticationError),
    (403, PermissionDeniedError),
    (402, ProviderConfigurationError),
    (429, RateLimitError),
    (500, ProviderUnavailableError),
    (504, ProviderTimeoutError),
    (529, ProviderUnavailableError),
])
def test_anthropic_maps_http_failures_without_exposing_provider_details(status, error_type):
    service = anthropic_service(lambda *_args, **_kwargs: FakeResponse(
        {'type': 'error', 'error': {
            'type': 'overloaded_error', 'message': 'private provider detail',
        }, 'request_id': 'request-body-1'},
        status=status,
        headers={'retry-after': '2.5'},
    ))
    with pytest.raises(error_type) as caught:
        service.generate(anthropic_request())
    assert 'private provider detail' not in str(caught.value)
    assert caught.value.http_status == status
    assert caught.value.provider_request_id == 'request-body-1'
    if status == 429:
        assert caught.value.retry_after_seconds == 2.5


@pytest.mark.parametrize(('failure', 'error_type'), [
    (requests.Timeout('private timeout'), ProviderTimeoutError),
    (requests.ConnectionError('private network'), ProviderUnavailableError),
])
def test_anthropic_maps_transport_failures(failure, error_type):
    def post(*_args, **_kwargs):
        raise failure

    with pytest.raises(error_type) as caught:
        anthropic_service(post).generate(anthropic_request())
    assert 'private' not in str(caught.value)
    assert caught.value.retryable is True


@pytest.mark.parametrize(('response', 'error_type'), [
    (FakeResponse(None, json_error=True), MalformedProviderResponseError),
    (FakeResponse([]), MalformedProviderResponseError),
    (FakeResponse({'type': 'error', 'content': []}), MalformedProviderResponseError),
    (FakeResponse({'type': 'message', 'content': None}), MalformedProviderResponseError),
    (FakeResponse({'type': 'message', 'content': [{'type': 'text'}]}),
     MalformedProviderResponseError),
    (FakeResponse({'type': 'message', 'content': [{
        'type': 'tool_use', 'id': '', 'name': 'lookup', 'input': {},
    }]}), ToolCallingError),
])
def test_anthropic_rejects_malformed_success_responses(response, error_type):
    with pytest.raises(error_type):
        anthropic_service(lambda *_args, **_kwargs: response).generate(
            anthropic_request(),
        )


def test_anthropic_rejects_unknown_shared_options_before_http():
    request = anthropic_request(options=GenerationOptions(
        provider_options={'unsupported_option': True},
    ))
    with pytest.raises(UnsupportedCapabilityError):
        anthropic_service(
            lambda *_args, **_kwargs: pytest.fail('HTTP must not run'),
        ).generate(request)


def _select_global_anthropic(monkeypatch, responses, *, model='claude-haiku-5-5'):
    seen = []
    queue = iter(responses)
    monkeypatch.setitem(ai_client.settings['ai'], 'provider', 'anthropic')
    monkeypatch.setitem(ai_client.settings['ai'], 'model', model)
    monkeypatch.setitem(ai_client.settings['ai'], 'anthropic_api_key', 'offline-key')
    provider = ai_service.registry.resolve('anthropic')

    def post(_url, **kwargs):
        seen.append(kwargs['json'])
        return FakeResponse(next(queue))

    monkeypatch.setattr(provider, '_http_post', post)
    return seen


@pytest.mark.parametrize('model', ['claude-haiku-5-5', 'claude-sonnet-5-5'])
def test_neutral_client_switches_to_anthropic_by_configuration_only(monkeypatch, model):
    seen = _select_global_anthropic(monkeypatch, [message_payload(model=model)], model=model)

    result = ai_client.completion([
        {'role': 'system', 'content': 'System contract.'},
        {'role': 'user', 'content': 'Feature prompt.'},
    ], temperature=0.4, max_tokens=80)

    assert result == {'role': 'assistant', 'content': 'Hello from Claude'}
    assert seen[0]['model'] == model
    assert seen[0]['max_tokens'] == 1024
    assert seen[0]['system'] == [{'type': 'text', 'text': 'System contract.'}]


@pytest.mark.parametrize('stop_reason', ['max_tokens', 'refusal'])
def test_neutral_client_rejects_truncated_or_refused_structured_output(
        monkeypatch, stop_reason):
    _select_global_anthropic(monkeypatch, [message_payload(
        content=[{'type': 'text', 'text': '{"suggestions":["Safe draft"]}'}],
        stop_reason=stop_reason,
    )])

    with pytest.raises(ai_client.AiUnavailableError, match=stop_reason):
        ai_client.completion(
            [{'role': 'user', 'content': 'Draft one reply.'}],
            output_schema={
                'type': 'object',
                'properties': {
                    'suggestions': {
                        'type': 'array', 'items': {'type': 'string'},
                    },
                },
                'required': ['suggestions'],
                'additionalProperties': False,
            },
        )


def test_bobbie_20_turn_workflow_executes_parallel_tools_and_continues(monkeypatch):
    events = []
    monkeypatch.setattr(ai_service, 'event_sink', events.append)
    first_content = [
        {'type': 'thinking', 'thinking': 'private reasoning', 'signature': 'signature-1'},
        {'type': 'tool_use', 'id': 'lead-1', 'name': 'get_lead_details', 'input': {}},
        {'type': 'tool_use', 'id': 'knowledge-1', 'name': 'search_agent_knowledge',
         'input': {'query': 'commission'}},
    ]
    seen = _select_global_anthropic(monkeypatch, [
        message_payload(content=first_content, stop_reason='tool_use', usage={
            'input_tokens': 600, 'output_tokens': 45,
            'cache_creation_input_tokens': 100, 'cache_read_input_tokens': 20,
        }),
        message_payload(content=[{
            'type': 'text',
            'text': 'That makes sense. What timing would work best for you?',
        }], usage={'input_tokens': 720, 'output_tokens': 18}),
    ])
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)
    monkeypatch.setattr(
        bobbie.knowledge,
        'search_agent_knowledge',
        lambda query, context, limit: {'query': query, 'limit': limit, 'results': []},
    )
    conversation = SimpleNamespace(
        contact='+13125550123',
        name='Jordan Lee',
        property_address='123 Example Ave, Austin, TX 78701',
        lead_status='processing',
        lead_context=json.dumps({
            'available': True,
            'outreach_reason': 'The listing is no longer active.',
            'property_details': {'Bedrooms': 3},
            'phone_number_source': {'available': False},
        }),
    )
    history = [
        {
            'direction': 'inbound' if index % 2 else 'outbound',
            'text': f'Conversation turn {index}',
        }
        for index in range(20)
    ]
    # The knowledge tool is offered only for agent questions, so the latest
    # owner message asks one (matching the scripted 'commission' tool query).
    history[-1]['text'] = "What's your commission?"

    result = bobbie.generate_ai_reply(conversation, history, agent_context=LEGACY_AGENT)

    assert result == 'That makes sense. What timing would work best for you?'
    assert len(seen) == 2
    assert seen[0]['max_tokens'] == 2048
    assert seen[0]['thinking'] == {'type': 'disabled'}
    assert len(seen[0]['messages']) == 20
    assert [tool['name'] for tool in seen[0]['tools']] == [
        'get_lead_details', 'search_agent_knowledge',
    ]
    assert seen[1]['messages'][-2] == {'role': 'assistant', 'content': first_content}
    results = seen[1]['messages'][-1]['content']
    assert [item['tool_use_id'] for item in results] == ['lead-1', 'knowledge-1']
    completed = [event for event in events if event.status == 'success']
    assert [event.total_tokens for event in completed] == [765, 738]
    assert [event.cache_read_tokens for event in completed] == [20, None]


def test_bobbie_tool_errors_are_returned_and_maximum_rounds_are_bounded(monkeypatch):
    responses = []
    for index in range(3):
        responses.append(message_payload(content=[{
            'type': 'tool_use',
            'id': f'knowledge-{index}',
            'name': 'search_agent_knowledge',
            'input': {'query': 'commission'},
        }], stop_reason='tool_use'))
    responses.append(message_payload(content=[{
        'type': 'text', 'text': 'I can have the brokerage clarify that for you.',
    }]))
    seen = _select_global_anthropic(monkeypatch, responses)
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)
    monkeypatch.setattr(
        bobbie.knowledge,
        'search_agent_knowledge',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('offline tool failure')),
    )
    conversation = SimpleNamespace(
        contact='+13125550123', name='Jordan Lee',
        property_address='123 Example Ave', lead_status='processing',
        lead_context=json.dumps({'available': True, 'phone_number_source': {'available': False}}),
    )

    result = bobbie.generate_ai_reply(
        conversation, [{'direction': 'inbound', 'text': 'What is the commission?'}],
    agent_context=LEGACY_AGENT)

    assert result == 'I can have the brokerage clarify that for you.'
    assert len(seen) == 4
    # The bounded retry keeps the same tools (so replayed tool turns stay valid)
    # but no longer allows another tool call.
    assert seen[-1]['tools'] == seen[0]['tools']
    assert seen[-1]['tool_choice'] == {'type': 'none'}
    assert seen[-1]['messages'][:len(seen[-2]['messages'])] == seen[-2]['messages']
    assert any(
        block.get('is_error') is True
        for request_body in seen[1:]
        for message in request_body['messages']
        if isinstance(message.get('content'), list)
        for block in message['content']
        if block.get('type') == 'tool_result'
    )


def _bobbie_conversation():
    return SimpleNamespace(
        contact='+13125550123',
        name='Jordan Lee',
        property_address='123 Example Ave, Austin, TX 78701',
        lead_status='processing',
        lead_context=json.dumps({
            'available': True,
            'outreach_reason': 'The listing is no longer active.',
            'property_details': {'Bedrooms': 3},
            'phone_number_source': {'available': False},
        }),
    )


def test_anthropic_bobbie_disposition_preserves_structured_workflow(monkeypatch):
    seen = _select_global_anthropic(monkeypatch, [message_payload(content=[{
        'type': 'text',
        'text': json.dumps({
            'action': 'continue',
            'intent': 'conditional_interest',
            'outcome': 'positive',
            'lead_status': 'interested',
            'confidence': 0.9,
            'conversation_stage': 'qualified_for_call',
            'next_step': 'answer_and_qualify',
            'qualification_focus': 'timing',
            'calendar': {
                'state': 'none',
                'should_fetch_availability': False,
                'requested_time_text': '',
                'reason': 'No meeting requested.',
            },
            'reason': 'The owner expressed conditional interest.',
        }),
    }])])

    result = bobbie.analyze_conversation_disposition(
        _bobbie_conversation(),
        [{'direction': 'inbound', 'text': 'Maybe, if the timing makes sense.'}],
    agent_context=LEGACY_AGENT)

    assert result['action'] == 'continue'
    assert result['lead_status'] == 'interested'
    assert result['next_step'] == 'offer_call'
    assert seen[0]['max_tokens'] == 1024
    assert_wire_schema(seen[0], bobbie.DISPOSITION_SCHEMA)


def test_anthropic_bobbie_followup_and_review_keep_existing_contracts(monkeypatch):
    seen = _select_global_anthropic(monkeypatch, [
        message_payload(content=[{
            'type': 'text',
            'text': 'Just checking whether selling is still something you would consider.',
        }]),
        # The generated follow-up is itself reviewed before it may be sent.
        message_payload(content=[{
            'type': 'text',
            'text': json.dumps({
                'valid': True, 'issues': [], 'rewrite_instruction': '', 'reason': 'Low-pressure follow-up.',
            }),
        }]),
        message_payload(content=[{
            'type': 'text',
            'text': json.dumps({
                'valid': True,
                'issues': [],
                'rewrite_instruction': '',
                'reason': 'The draft answers the owner safely.',
            }),
        }]),
    ])
    conversation = _bobbie_conversation()
    history = [{'direction': 'outbound', 'text': 'Would you consider selling?'}]

    followup = bobbie.generate_no_response_followup(conversation, history, attempt=1, maximum=3, agent_context=LEGACY_AGENT)
    review = bobbie.review_ai_reply(
        conversation,
        history + [{'direction': 'inbound', 'text': 'What would happen next?'}],
        'I can explain the next steps.',
        {'next_step': 'answer_only'},
    agent_context=LEGACY_AGENT)

    assert followup == 'Just checking whether selling is still something you would consider.'
    assert review['valid'] is True
    assert review['issues'] == []
    assert len(seen) == 3
    assert_wire_schema(seen[1], bobbie.REVIEW_SCHEMA)
    assert followup in json.dumps(seen[1])


def test_anthropic_review_rejection_runs_plain_text_repair(monkeypatch):
    seen = _select_global_anthropic(monkeypatch, [
        message_payload(content=[{'type': 'text', 'text': json.dumps({
            'valid': False,
            'issues': ['did_not_answer_owner'],
            'rewrite_instruction': 'Answer the owner before asking one question.',
            'reason': 'The draft skipped the latest question.',
        })}]),
        message_payload(content=[{
            'type': 'text',
            'text': 'Thanks for clarifying. What timing would work best for you?',
        }]),
        message_payload(content=[{'type': 'text', 'text': json.dumps({
            'valid': True, 'issues': [], 'rewrite_instruction': '', 'reason': 'Answers the owner.'})}]),
    ])
    history = [{'direction': 'inbound', 'text': 'I may sell later this year.'}]
    disposition = {'next_step': 'answer_and_qualify'}

    repaired = bobbie.review_and_repair_ai_reply(
        _bobbie_conversation(), history, 'Can we meet tomorrow?', disposition,
    agent_context=LEGACY_AGENT)

    assert repaired == 'Thanks for clarifying. What timing would work best for you?'
    # review, plain-text regeneration, then a review of the regeneration
    assert len(seen) == 3
    assert_wire_schema(seen[0], bobbie.REVIEW_SCHEMA)
    assert 'output_config' in seen[1]
    assert 'format' not in seen[1]['output_config']
    assert_wire_schema(seen[2], bobbie.REVIEW_SCHEMA)
    assert repaired in json.dumps(seen[2])


def test_anthropic_calendar_resolution_uses_native_schema(monkeypatch):
    seen = _select_global_anthropic(monkeypatch, [message_payload(content=[{
        'type': 'text',
        'text': json.dumps({
            'action': 'ask_confirmation',
            'consent': 'owner_proposed',
            'start_at': '2026-11-03T14:00:00-06:00',
            'end_at': '2026-11-03T14:30:00-06:00',
            'reason': 'The proposed time is available.',
        }),
    }])])
    availability = {
        'current_time': '2026-11-01T12:00:00-06:00',
        'timezone': 'America/Chicago',
        'slots': [{
            'start_at': '2026-11-03T14:00:00-06:00',
            'end_at': '2026-11-03T14:30:00-06:00',
            'label': 'Tuesday, November 3, 2026 at 2:00 PM',
        }],
    }

    result = bobbie.resolve_calendar_action(
        _bobbie_conversation(),
        [{'direction': 'inbound', 'text': 'Could we do Tuesday at 2?'}],
        availability,
        {'state': 'time_proposed', 'requested_time_text': 'Tuesday at 2'},
    )

    assert result['action'] == 'ask_confirmation'
    assert_wire_schema(seen[0], bobbie.CALENDAR_ACTION_SCHEMA)


def test_anthropic_campaign_reason_suggestion_uses_existing_feature_prompt(
        session, monkeypatch):
    seen = _select_global_anthropic(monkeypatch, [message_payload(content=[{
        'type': 'text',
        'text': json.dumps({'suggestions': [
            'I noticed the listing is no longer active. Would selling still be of interest?',
            'Would you consider discussing a new path for the property?',
            'Is selling the property something you would revisit?',
        ]}),
    }])])
    user = User(
        email='anthropic-reason@example.test',
        hashed_password='unused',
        full_name='Offline Tester',
        role='broker',
        brokerage_id='brokerage-1',
        is_active=True,
    )
    session.add(user)
    session.flush()
    campaign = Campaign(user_id=user.id, name='Anthropic offline test', status='draft')
    session.add(campaign)
    session.flush()
    session.add(Lead(
        user_id=user.id,
        campaign_id=campaign.id,
        owner_name='Offline Owner',
        phone='+15550000001',
        property_address='1 Offline Way',
        signals='expired',
    ))
    session.commit()

    result = reason_suggest.suggest(session, user, campaign, signal='expired')

    assert result['source'] == 'ai'
    assert len(result['options']) >= 3
    assert 'Use ONLY the record types listed by the user' in seen[0]['system'][0]['text']
    assert 'expired' in json.dumps(seen[0]).lower()


def test_anthropic_no_signal_reason_suggestions_remain_neutral(session, monkeypatch):
    seen = _select_global_anthropic(monkeypatch, [message_payload(content=[{
        'type': 'text',
        'text': json.dumps({'suggestions': [
            'I am reaching out about the property. Would you consider selling?',
            'Would you be open to discussing a possible sale of the property?',
            'Is selling the property something you might consider?',
        ]}),
    }])])
    user = User(
        email='anthropic-neutral@example.test', hashed_password='unused',
        full_name='Offline Tester', role='broker', brokerage_id='brokerage-neutral',
        is_active=True,
    )
    session.add(user)
    session.flush()
    campaign = Campaign(user_id=user.id, name='Neutral offline test', status='draft')
    session.add(campaign)
    session.flush()
    session.add(Lead(
        user_id=user.id, campaign_id=campaign.id, owner_name='Offline Owner',
        phone='+15550000003', property_address='3 Offline Way', signals='',
    ))
    session.commit()

    result = reason_suggest.suggest(
        session, user, campaign, signal=reason_suggest.NO_SIGNAL_REASON_KEY,
    )

    assert result['source'] == 'ai'
    assert len(result['options']) == 3
    assert all(option['source'] == 'ai' for option in result['options'])
    assert 'No lead signal or verified property fact is available' in seen[0]['system'][0]['text']


def test_anthropic_reply_suggestions_use_existing_context_and_contract(session, monkeypatch):
    seen = _select_global_anthropic(monkeypatch, [message_payload(content=[{
        'type': 'text',
        'text': json.dumps({'suggestions': [
            'I understand. What timing would work best for you?',
            'That makes sense. Would you be open to a quick conversation?',
            'Thanks for clarifying. What would you like to know first?',
        ]}),
    }])])
    user = User(
        email='anthropic-reply@example.test',
        hashed_password='unused',
        full_name='Offline Tester',
        role='broker',
        brokerage_id='brokerage-reply',
        is_active=True,
    )
    session.add(user)
    session.flush()
    conversation = Conversation(
        user_id=user.id,
        contact='+15550000002',
        name='Offline Owner',
        property_address='2 Offline Way',
        lead_status='interested',
        lead_context=json.dumps({'outreach_reason': 'The owner asked about timing.'}),
    )
    session.add(conversation)
    session.flush()
    session.add_all([
        Message(
            conversation_id=conversation.id,
            direction='outbound',
            text='Would you consider selling the property?',
        ),
        Message(
            conversation_id=conversation.id,
            direction='inbound',
            text='Maybe, depending on the timing.',
        ),
    ])
    session.commit()

    result = reply_suggest.suggest(session, conversation)

    assert result['source'] == 'ai'
    assert len(result['suggestions']) == 3
    assert seen[0]['system'][0]['text'] == reply_suggest.SYSTEM_PROMPT
    assert 'OWNER [INBOUND]: Maybe, depending on the timing.' in seen[0]['messages'][0]['content']
