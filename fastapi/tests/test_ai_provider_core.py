"""Offline contract tests for the universal AI core and DeepSeek adapter."""

import json
import logging
from dataclasses import dataclass, replace

import pytest
import requests

from app.ai import client as ai_client
from app.ai.errors import (
    AuthenticationError,
    InvalidProviderRequestError,
    MalformedOutputError,
    MalformedProviderResponseError,
    ProviderNotImplementedError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SchemaValidationError,
    ToolCallingError,
    UnsupportedCapabilityError,
)
from app.ai.models import (
    AIMessage,
    AIRequest,
    AIResponse,
    AIToolDefinition,
    GenerationOptions,
    ProviderCapabilities,
    Role,
    StructuredOutputMode,
    TokenUsage,
)
from app.ai.providers.deepseek import DeepSeekProvider
from app.ai.registry import ProviderRegistry
from app.ai.service import AIService
from app.sms import deepseek as legacy


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


def request(**changes):
    base = AIRequest(
        feature='test.feature',
        operation_id='op-1',
        attempt_id='attempt-1',
        correlation_id='correlation-1',
        provider='deepseek',
        model='deepseek-test',
        messages=(AIMessage(Role.USER, 'hello'),),
        timeout_seconds=12.5,
    )
    return replace(base, **changes)


def service_with(post, *, sink=lambda _event: None):
    registry = ProviderRegistry()
    registry.register('deepseek', DeepSeekProvider(
        api_key='test-key', base_url='https://deepseek.invalid/', http_post=post,
    ))
    return AIService(registry, event_sink=sink)


def success_payload(**changes):
    payload = {
        'id': 'response-1',
        'model': 'deepseek-response-model',
        'choices': [{
            'message': {'role': 'assistant', 'content': 'hello back'},
            'finish_reason': 'stop',
        }],
        'usage': {
            'prompt_tokens': 10,
            'completion_tokens': 4,
            'total_tokens': 14,
            'prompt_cache_hit_tokens': 6,
            'prompt_cache_miss_tokens': 4,
        },
    }
    payload.update(changes)
    return payload


def test_deepseek_translates_text_request_without_changing_existing_semantics():
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return FakeResponse(success_payload())

    ai = service_with(post)
    response = ai.generate(request(
        system_instructions=('system instruction',),
        messages=(AIMessage(Role.USER, 'hello'), AIMessage(Role.TOOL, '{}', 'call-1')),
        max_output_tokens=120,
        options=GenerationOptions(
            temperature=0.4, top_p=0.9, stop=('END',),
            provider_options={'response_format': {'type': 'json_object'}},
        ),
    ))

    assert seen['url'] == 'https://deepseek.invalid/chat/completions'
    assert seen['timeout'] == 12.5
    assert seen['headers']['Authorization'] == 'Bearer test-key'
    assert seen['json'] == {
        'model': 'deepseek-test',
        'messages': [
            {'role': 'system', 'content': 'system instruction'},
            {'role': 'user', 'content': 'hello'},
            {'role': 'tool', 'content': '{}', 'tool_call_id': 'call-1'},
        ],
        'thinking': {'type': 'disabled'},
        'stream': False,
        'temperature': 0.4,
        'top_p': 0.9,
        'stop': ['END'],
        'max_tokens': 120,
        'response_format': {'type': 'json_object'},
    }
    assert response.text == 'hello back'


def test_deepseek_translates_tools_and_normalizes_tool_calls():
    seen = {}
    payload = success_payload(choices=[{
        'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'call-7', 'type': 'function',
            'function': {'name': 'lookup', 'arguments': '{"query":"roof"}'},
        }]},
        'finish_reason': 'tool_calls',
    }])

    def post(_url, **kwargs):
        seen.update(kwargs)
        return FakeResponse(payload)

    response = service_with(post).generate_with_tools(request(
        tools=(AIToolDefinition('lookup', 'Look up approved facts', {
            'type': 'object', 'properties': {'query': {'type': 'string'}},
            'required': ['query'],
        }),),
        options=GenerationOptions(tool_choice='auto'),
    ))

    assert seen['json']['tools'][0]['function']['parameters']['required'] == ['query']
    assert seen['json']['tool_choice'] == 'auto'
    assert response.text == ''
    assert response.tool_calls[0].arguments == {'query': 'roof'}
    assert response.stop_reason == 'tool_calls'


def test_deepseek_normalizes_usage_model_and_request_id():
    response = service_with(lambda *_args, **_kwargs: FakeResponse(
        success_payload(), headers={'x-request-id': 'header-request-1'},
    )).generate(request())

    assert response.model == 'deepseek-response-model'
    assert response.provider_request_id == 'header-request-1'
    assert response.usage == TokenUsage(10, 4, 14, 6, 4, 'provider')


def test_missing_usage_is_unavailable_not_fabricated_zero():
    response = service_with(lambda *_args, **_kwargs: FakeResponse(
        success_payload(usage=None),
    )).generate(request())
    assert response.usage == TokenUsage()


def test_missing_provider_key_is_a_typed_configuration_error():
    registry = ProviderRegistry()
    registry.register('deepseek', DeepSeekProvider(
        api_key='', base_url='https://deepseek.invalid',
        http_post=lambda *_args, **_kwargs: pytest.fail('network must not run'),
    ))
    with pytest.raises(ProviderConfigurationError):
        AIService(registry).generate(request())


@pytest.mark.parametrize('status,error_type', [
    (401, AuthenticationError),
    (403, AuthenticationError),
    (400, InvalidProviderRequestError),
    (429, RateLimitError),
    (500, ProviderUnavailableError),
    (502, ProviderUnavailableError),
    (503, ProviderUnavailableError),
])
def test_deepseek_maps_http_errors_without_exposing_provider_message(status, error_type):
    ai = service_with(lambda *_args, **_kwargs: FakeResponse(
        {'error': {'message': 'raw sensitive provider message', 'code': 'safe-code'}},
        status=status,
        headers={'retry-after': '2.5'},
    ))
    with pytest.raises(error_type) as caught:
        ai.generate(request())
    assert 'raw sensitive' not in str(caught.value)
    assert caught.value.provider_code == 'safe-code'
    assert caught.value.http_status == status
    assert caught.value.operation_id == 'op-1'
    if status == 429:
        assert caught.value.retry_after_seconds == 2.5


def test_deepseek_maps_timeout_and_network_failure():
    for exc, error_type in [
        (requests.Timeout('private timeout detail'), ProviderTimeoutError),
        (requests.ConnectionError('private network detail'), ProviderUnavailableError),
    ]:
        ai = service_with(lambda *_args, _exc=exc, **_kwargs: (_ for _ in ()).throw(_exc))
        with pytest.raises(error_type) as caught:
            ai.generate(request())
        assert 'private' not in str(caught.value)
        assert caught.value.retryable is True


@pytest.mark.parametrize('response', [
    FakeResponse(None, json_error=True),
    FakeResponse({}),
    FakeResponse({'choices': []}),
    FakeResponse({'choices': [{}]}),
    FakeResponse({'choices': [{'message': 'wrong'}]}),
])
def test_deepseek_rejects_malformed_success_envelopes(response):
    with pytest.raises(MalformedProviderResponseError):
        service_with(lambda *_args, **_kwargs: response).generate(request())


@pytest.mark.parametrize('arguments', ['not-json', '[]'])
def test_deepseek_rejects_invalid_tool_arguments(arguments):
    payload = success_payload(choices=[{'message': {
        'content': '', 'tool_calls': [{
            'id': 'call-1', 'function': {'name': 'lookup', 'arguments': arguments},
        }],
    }}])
    with pytest.raises(ToolCallingError):
        service_with(lambda *_args, **_kwargs: FakeResponse(payload)).generate(request())


def test_structured_generation_parses_validates_and_constructs_dataclass():
    @dataclass
    class Result:
        answer: str

    payload = success_payload(choices=[{'message': {
        'content': '{"answer":"yes"}',
    }}])
    structured_request = request(output_schema={
        'type': 'object',
        'properties': {'answer': {'type': 'string', 'minLength': 1}},
        'required': ['answer'],
        'additionalProperties': False,
    })
    response = service_with(
        lambda *_args, **_kwargs: FakeResponse(payload),
    ).generate_structured(structured_request, Result)
    assert response.structured == Result(answer='yes')
    assert response.structured_output_valid is True


def test_structured_generation_distinguishes_parse_and_schema_errors():
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}},
              'required': ['answer']}
    invalid_json = success_payload(choices=[{'message': {'content': 'not-json'}}])
    with pytest.raises(MalformedOutputError):
        service_with(lambda *_a, **_k: FakeResponse(invalid_json)).generate_structured(
            request(output_schema=schema), dict,
        )
    wrong_shape = success_payload(choices=[{'message': {'content': '{}'}}])
    with pytest.raises(SchemaValidationError):
        service_with(lambda *_a, **_k: FakeResponse(wrong_shape)).generate_structured(
            request(output_schema=schema), dict,
        )


def test_native_schema_requirement_and_unknown_options_fail_before_network():
    calls = []
    ai = service_with(lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(UnsupportedCapabilityError):
        ai.generate_structured(request(
            output_schema={'type': 'object'},
            structured_output_mode=StructuredOutputMode.NATIVE_REQUIRED,
        ))
    with pytest.raises(UnsupportedCapabilityError):
        ai.generate(request(options=GenerationOptions(provider_options={'secret_mode': True})))
    with pytest.raises(UnsupportedCapabilityError):
        ai.generate_structured(request(output_schema={'type': 'object', 'oneOf': []}))
    assert calls == []


def test_unimplemented_and_unknown_providers_fail_typed_without_network():
    registry = ProviderRegistry()
    ai = AIService(registry)
    for provider in ('anthropic', 'openai', 'unknown'):
        with pytest.raises(ProviderNotImplementedError):
            ai.generate(request(provider=provider))


def test_attempt_events_include_safe_metadata_for_success_and_failure():
    events = []
    ai = service_with(
        lambda *_args, **_kwargs: FakeResponse(success_payload()), sink=events.append,
    )
    ai.generate(request())
    assert [event.status for event in events] == ['started', 'success']
    assert events[-1].input_tokens == 10
    assert events[-1].provider_request_id == 'response-1'
    assert not hasattr(events[-1], 'prompt')

    events.clear()
    failing = service_with(
        lambda *_args, **_kwargs: FakeResponse({'error': {'code': 'busy'}}, status=503),
        sink=events.append,
    )
    with pytest.raises(ProviderUnavailableError):
        failing.generate(request())
    assert [event.status for event in events] == ['started', 'failure']
    assert events[-1].error_category == 'provider_unavailable'


def test_failure_event_retains_provider_request_id_and_safe_http_metadata():
    events = []
    failing = service_with(
        lambda *_args, **_kwargs: FakeResponse(
            {'error': {'code': 'invalid-model', 'message': 'raw provider detail'}},
            status=400,
            headers={'x-request-id': 'failed-request-7'},
        ),
        sink=events.append,
    )
    with pytest.raises(InvalidProviderRequestError):
        failing.generate(request())
    event = events[-1]
    assert event.provider_request_id == 'failed-request-7'
    assert event.http_status == 400
    assert event.safe_error_code == 'invalid-model'
    assert event.retryable is False


def test_default_attempt_logging_excludes_prompts_outputs_keys_and_customer_data(
        caplog):
    registry = ProviderRegistry()
    registry.register('deepseek', DeepSeekProvider(
        api_key='secret-api-key',
        base_url='https://deepseek.invalid',
        http_post=lambda *_args, **_kwargs: FakeResponse(success_payload(
            choices=[{'message': {'content': 'private generated SMS'}}],
        )),
    ))
    caplog.set_level(logging.INFO)
    AIService(registry).generate(request(messages=(
        AIMessage(Role.USER, 'private customer prompt at 123 Main Street'),
    )))
    assert 'ai.provider.attempt.completed' in caplog.text
    assert 'private customer prompt' not in caplog.text
    assert 'private generated SMS' not in caplog.text
    assert 'secret-api-key' not in caplog.text
    assert 'Authorization' not in caplog.text


def test_instrumentation_failure_is_visible_but_does_not_break_generation(caplog):
    def broken_sink(_event):
        raise RuntimeError('sink unavailable')

    caplog.set_level(logging.ERROR)
    response = service_with(
        lambda *_args, **_kwargs: FakeResponse(success_payload()), sink=broken_sink,
    ).generate(request())
    assert response.text == 'hello back'
    assert 'event=ai.instrumentation.failed' in caplog.text
    assert 'sink unavailable' not in caplog.text


def test_legacy_completion_signature_and_return_shape_route_through_service(monkeypatch):
    seen = {}

    def generate(ai_request):
        seen['request'] = ai_request
        return AIResponse(
            text='legacy answer', structured=None, tool_calls=(), stop_reason='stop',
            provider='deepseek', model='deepseek-test', provider_request_id='r1',
            usage=TokenUsage(), latency_ms=1, structured_output_valid=None,
        )

    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(legacy.ai_service, 'generate', generate)
    result = legacy.completion(
        [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hello'}],
        temperature=0.4, max_tokens=120, response_format={'type': 'json_object'},
    )
    assert result == {'role': 'assistant', 'content': 'legacy answer'}
    assert seen['request'].messages[0].role == Role.SYSTEM
    assert seen['request'].options.temperature == 0.4
    assert seen['request'].max_output_tokens == 120
    assert seen['request'].options.provider_options['_legacy_options']['response_format'] == {
        'type': 'json_object',
    }


def test_neutral_client_selects_provider_and_model_from_configuration(monkeypatch):
    seen = {}

    def generate(ai_request):
        seen['request'] = ai_request
        return AIResponse(
            text='configured answer', structured=None, tool_calls=(), stop_reason='stop',
            provider=ai_request.provider, model=ai_request.model, provider_request_id='r-config',
            usage=TokenUsage(), latency_ms=1, structured_output_valid=None,
        )

    monkeypatch.setitem(ai_client.settings['ai'], 'anthropic_api_key', 'configured')
    monkeypatch.setitem(ai_client.settings['ai'], 'provider', 'anthropic')
    monkeypatch.setitem(ai_client.settings['ai'], 'model', 'claude-sonnet-5-5')
    monkeypatch.setattr(ai_client.ai_service, 'generate', generate)

    result = ai_client.completion([{'role': 'user', 'content': 'hello'}])

    assert result == {'role': 'assistant', 'content': 'configured answer'}
    assert seen['request'].provider == 'anthropic'
    assert seen['request'].model == 'claude-sonnet-5-5'
    assert seen['request'].feature == 'ai.completion'
    assert legacy.completion is ai_client.completion

    # An unsupported model is rejected before any provider request is built.
    seen.clear()
    monkeypatch.setitem(ai_client.settings['ai'], 'model', 'claude-configured')
    with pytest.raises(ai_client.AiUnavailableError, match='cannot use model'):
        ai_client.completion([{'role': 'user', 'content': 'hello'}])
    assert seen == {}


def test_legacy_completion_preserves_empty_envelope_and_error_boundaries(monkeypatch):
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(
        legacy.ai_service, 'generate',
        lambda _request: (_ for _ in ()).throw(MalformedProviderResponseError('malformed')),
    )
    assert legacy.completion([{'role': 'user', 'content': 'hello'}]) == {}

    monkeypatch.setattr(
        legacy.ai_service, 'generate',
        lambda _request: (_ for _ in ()).throw(ProviderUnavailableError('unavailable')),
    )
    with pytest.raises(legacy.AiUnavailableError):
        legacy.completion([{'role': 'user', 'content': 'hello'}])


def test_legacy_completion_uses_adapter_and_preserves_http_body(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return FakeResponse({'choices': [{'message': {
            'role': 'assistant', 'content': 'answer',
        }}]})

    provider = legacy.ai_service.registry.resolve('deepseek')
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(provider, '_api_key', 'configured')
    monkeypatch.setattr(legacy.requests, 'post', post)

    result = legacy.completion(
        [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'hello'}],
        temperature=0.7, max_tokens=90,
    )

    assert result == {'role': 'assistant', 'content': 'answer'}
    assert seen['json'] == {
        'model': legacy.settings['ai']['model'],
        'messages': [
            {'role': 'system', 'content': 'system'},
            {'role': 'user', 'content': 'hello'},
        ],
        'thinking': {'type': 'disabled'},
        'stream': False,
        'temperature': 0.7,
        'max_tokens': 90,
    }
    assert seen['timeout'] == legacy.settings['ai']['timeout_seconds']


@pytest.mark.parametrize(('messages', 'options'), [
    (
        [{'role': 'user', 'content': 'plain text'}],
        {},
    ),
    (
        [
            {'role': 'system', 'content': 'Return JSON.'},
            {'role': 'user', 'content': 'Give an object.'},
        ],
        {'response_format': {'type': 'json_object'}, 'temperature': 0},
    ),
    (
        [
            {'role': 'system', 'content': 'System first.'},
            {'role': 'user', 'content': 'First turn.'},
            {'role': 'assistant', 'content': 'First answer.'},
            {'role': 'user', 'content': 'Second turn.'},
        ],
        {'temperature': 0.4, 'max_tokens': 120},
    ),
    (
        [
            {'role': 'user', 'content': 'Look it up.'},
            {
                'role': 'assistant',
                'content': '',
                'tool_calls': [{
                    'id': 'call-1',
                    'type': 'function',
                    'function': {'name': 'first_tool', 'arguments': '{"value":1}'},
                }],
            },
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': '{"exact": true}'},
        ],
        {
            'tools': [
                {'type': 'function', 'function': {
                    'name': 'first_tool', 'description': 'First',
                    'parameters': {'type': 'object'},
                }},
                {'type': 'function', 'function': {
                    'name': 'second_tool', 'description': 'Second',
                    'parameters': {'type': 'object', 'properties': {}},
                }},
            ],
            'tool_choice': 'auto',
            'temperature': 0.2,
            'max_tokens': 80,
        },
    ),
])
def test_legacy_request_body_matches_original_client_exactly(
        monkeypatch, messages, options):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return FakeResponse({'choices': [{'message': {
            'role': 'assistant', 'content': 'answer',
        }}]})

    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'request-key')
    monkeypatch.setitem(legacy.settings['ai'], 'base_url', 'https://configured.invalid/')
    monkeypatch.setitem(legacy.settings['ai'], 'model', 'configured-model')
    monkeypatch.setitem(legacy.settings['ai'], 'timeout_seconds', 17.5)
    monkeypatch.setattr(legacy.requests, 'post', post)

    legacy.completion(messages, **options)

    expected = {
        'model': 'configured-model',
        'messages': messages,
        'thinking': {'type': 'disabled'},
        'stream': False,
        **options,
    }
    assert seen['json'] == expected
    assert seen['url'] == 'https://configured.invalid/chat/completions'
    assert seen['headers'] == {
        'Authorization': 'Bearer request-key',
        'Content-Type': 'application/json',
    }
    assert seen['timeout'] == 17.5


def test_legacy_options_retain_the_original_last_write_wins_behavior(monkeypatch):
    seen = {}

    def post(_url, **kwargs):
        seen.update(kwargs)
        return FakeResponse({'choices': [{'message': {'content': 'answer'}}]})

    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(legacy.requests, 'post', post)
    legacy.completion(
        [{'role': 'user', 'content': 'original'}],
        model='caller-model',
        thinking={'type': 'enabled'},
        stream=True,
        provider_extension='kept',
    )
    assert seen['json']['model'] == 'caller-model'
    assert seen['json']['messages'] == [{'role': 'user', 'content': 'original'}]
    assert seen['json']['thinking'] == {'type': 'enabled'}
    assert seen['json']['stream'] is True
    assert seen['json']['provider_extension'] == 'kept'


@pytest.mark.parametrize('message', [
    {'role': 'assistant', 'content': 'text'},
    {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': 'call-1', 'type': 'function',
        'function': {'name': 'lookup', 'arguments': '{}'},
    }]},
    {'role': 'assistant', 'content': 'text and tool', 'tool_calls': [{
        'id': 'call-2', 'type': 'function',
        'function': {'name': 'lookup', 'arguments': '{"query":"roof"}'},
    }]},
    {'role': 'assistant', 'content': None},
    {'role': 'assistant', 'content': ''},
    {'role': 'assistant', 'content': 'text', 'provider_extension': {'kept': True}},
])
def test_legacy_response_returns_the_provider_message_unchanged(monkeypatch, message):
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(
        legacy.requests,
        'post',
        lambda *_args, **_kwargs: FakeResponse({'choices': [{'message': message}]}),
    )
    assert legacy.completion([{'role': 'user', 'content': 'hello'}]) == message


@pytest.mark.parametrize('response', [
    FakeResponse({'choices': []}),
    FakeResponse({}),
    FakeResponse({'choices': [{}]}),
    FakeResponse(None, json_error=True),
])
def test_legacy_response_preserves_empty_envelope_behavior(monkeypatch, response):
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(legacy.requests, 'post', lambda *_args, **_kwargs: response)
    assert legacy.completion([{'role': 'user', 'content': 'hello'}]) == {}


@pytest.mark.parametrize('arguments', ['not-json', '[]'])
def test_legacy_response_preserves_malformed_tool_arguments_verbatim(
        monkeypatch, arguments):
    message = {
        'role': 'assistant',
        'content': None,
        'tool_calls': [{
            'id': 'call-1', 'type': 'function',
            'function': {'name': 'lookup', 'arguments': arguments},
        }],
    }
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(
        legacy.requests,
        'post',
        lambda *_args, **_kwargs: FakeResponse({'choices': [{'message': message}]}),
    )
    assert legacy.completion([{'role': 'user', 'content': 'hello'}]) == message


def test_default_adapter_reads_key_and_endpoint_at_request_time(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return FakeResponse({'choices': [{'message': {'content': 'answer'}}]})

    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'late-key')
    monkeypatch.setitem(legacy.settings['ai'], 'base_url', 'https://late.invalid')
    monkeypatch.setattr(legacy.requests, 'post', post)
    legacy.completion([{'role': 'user', 'content': 'hello'}])
    assert seen['url'] == 'https://late.invalid/chat/completions'
    assert seen['headers']['Authorization'] == 'Bearer late-key'


def test_legacy_missing_key_keeps_the_original_error_contract(monkeypatch):
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', '')
    with pytest.raises(
        legacy.AiUnavailableError,
        match=r'DEEPSEEK_API_KEY \(or AI_API_KEY\) is not configured',
    ):
        legacy.completion([{'role': 'user', 'content': 'hello'}])


@pytest.mark.parametrize('response', [
    FakeResponse({'error': {'message': 'unsafe raw detail'}}, status=401),
    FakeResponse({'error': {'message': 'unsafe raw detail'}}, status=429),
    FakeResponse({'error': {'message': 'unsafe raw detail'}}, status=503),
])
def test_legacy_http_failures_map_to_safe_ai_unavailable(monkeypatch, response):
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(legacy.requests, 'post', lambda *_args, **_kwargs: response)
    with pytest.raises(legacy.AiUnavailableError) as caught:
        legacy.completion([{'role': 'user', 'content': 'hello'}])
    assert 'unsafe raw detail' not in str(caught.value)


@pytest.mark.parametrize('failure', [
    requests.ConnectionError('private connection detail'),
    requests.Timeout('private timeout detail'),
])
def test_legacy_transport_failures_map_to_safe_ai_unavailable(monkeypatch, failure):
    monkeypatch.setitem(legacy.settings['ai'], 'api_key', 'configured')
    monkeypatch.setattr(
        legacy.requests,
        'post',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(legacy.AiUnavailableError) as caught:
        legacy.completion([{'role': 'user', 'content': 'hello'}])
    assert 'private' not in str(caught.value)
