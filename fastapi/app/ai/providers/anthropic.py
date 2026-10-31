"""Anthropic Messages API adapter for the provider-neutral AI contracts."""

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from ..errors import (
    AuthenticationError,
    InvalidProviderRequestError,
    MalformedProviderResponseError,
    PermissionDeniedError,
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    ToolCallingError,
)
from ..models import (
    AIMessage,
    AIRequest,
    AIResponse,
    AIToolCall,
    ProviderCapabilities,
    Role,
    TokenUsage,
)

ANTHROPIC_API_VERSION = '2026-10-21'
DEFAULT_MAX_OUTPUT_TOKENS = 1024
# Models this adapter has explicit request rules for (thinking, effort, tool
# choice). Others are rejected by configuration validation until added here.
SUPPORTED_MODELS = ('claude-haiku-5-5', 'claude-sonnet-5-5')
# Models that return a 400 for forced tool use (tool_choice "any" / "tool").
FORCED_TOOL_CHOICE_UNSUPPORTED = (
    'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1', 'claude-mythos-5-1',
)
# Models that accept a role "system" message inside `messages`. A later
# instruction is kept in place there, so the earlier turns stay unchanged.
MID_CONVERSATION_SYSTEM_MODELS = ('claude-haiku-5-5', 'claude-sonnet-5-5')
# JSON Schema keywords structured outputs reject with a 400. They are removed
# from the wire schema; the feature parsers still enforce the business limits.
_UNSUPPORTED_SCHEMA_KEYWORDS = (
    'minLength', 'maxLength', 'minimum', 'maximum', 'multipleOf', 'maxItems',
)


def provider_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return the schema Anthropic structured outputs accept.

    Unsupported limits are removed and restated in the description, as the
    official SDKs do. `minItems` is kept only for the supported values 0 and 1.
    """
    result: dict[str, Any] = {}
    hints = []
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYWORDS or (key == 'minItems' and value not in (0, 1)):
            hints.append(f'{key}: {value}')
        elif key == 'properties' and isinstance(value, Mapping):
            result[key] = {name: provider_schema(child) for name, child in value.items()}
        elif key == 'items' and isinstance(value, Mapping):
            result[key] = provider_schema(value)
        else:
            result[key] = value
    if hints:
        prefix = f"{result['description']} " if result.get('description') else ''
        result['description'] = prefix + '{' + ', '.join(hints) + '}'
    return result


class AnthropicProvider:
    provider_id = 'anthropic'

    def __init__(
        self,
        *,
        api_key: str | Callable[[], str],
        base_url: str | Callable[[], str] = 'https://api.anthropic.com',
        api_version: str | Callable[[], str] = ANTHROPIC_API_VERSION,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._api_version = api_version
        self._http_post = http_post or requests.post

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            text_generation=True,
            native_structured_output=True,
            tool_calling=True,
            parallel_tool_calls=True,
            thinking_or_reasoning_controls=True,
            prompt_caching=True,
            provider_token_usage=True,
            cache_token_usage=True,
            supported_generation_options=frozenset({
                # Claude 5.5 rejects non-default sampling values. The adapter
                # accepts the shared options but intentionally omits them.
                'temperature', 'top_p', 'stop', 'max_output_tokens',
                'tool_choice', 'thinking', 'output_config', 'service_tier', 'speed',
            }),
            maximum_context_tokens=1_000_000,
            maximum_output_tokens=128_000,
        )

    def generate(self, request: AIRequest) -> AIResponse[Any]:
        api_key = self._resolve(self._api_key)
        if not api_key:
            raise ProviderConfigurationError('Anthropic API key is not configured')

        body = self._build_body(request)
        started = time.perf_counter()
        try:
            response = self._http_post(
                f'{self._resolve(self._base_url).rstrip("/")}/v1/messages',
                json=body,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'anthropic-version': self._resolve(self._api_version),
                    'Content-Type': 'application/json',
                },
                timeout=request.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise ProviderTimeoutError(
                'Anthropic request timed out', retryable=True,
                ambiguous_completion=True, cause=exc,
            ) from exc
        except requests.RequestException as exc:
            raise ProviderUnavailableError(
                'Anthropic provider is unreachable', retryable=True, cause=exc,
            ) from exc

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            if response.ok:
                raise MalformedProviderResponseError(
                    'Anthropic returned a non-JSON success response', cause=exc,
                ) from exc
            payload = {}

        if not response.ok:
            self._raise_http_error(response, payload)
        if not isinstance(payload, dict):
            raise MalformedProviderResponseError('Anthropic response must be an object')
        if payload.get('type') not in (None, 'message'):
            raise MalformedProviderResponseError('Anthropic response is not a message')

        content = payload.get('content')
        text, tool_calls, provider_state = self._parse_content(content)
        usage = self._parse_usage(payload.get('usage'))
        response_model = payload.get('model') if isinstance(payload.get('model'), str) else request.model
        metadata = {'_provider_state': provider_state} if tool_calls else {}
        return AIResponse(
            text=text,
            structured=None,
            tool_calls=tool_calls,
            stop_reason=(payload.get('stop_reason')
                         if isinstance(payload.get('stop_reason'), str) else None),
            provider=self.provider_id,
            model=response_model,
            provider_request_id=self._header(response, 'request-id'),
            usage=usage,
            latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
            structured_output_valid=None,
            provider_metadata=metadata,
        )

    def _build_body(self, request: AIRequest) -> dict[str, Any]:
        system, messages = self._messages(request)
        body: dict[str, Any] = {
            'model': request.model,
            'max_tokens': request.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
            'messages': messages,
            'stream': False,
        }
        if request.model.startswith('claude-haiku-5-5'):
            body['thinking'] = {'type': 'disabled'}
        elif request.model.startswith('claude-sonnet-5-5'):
            body['thinking'] = {'type': 'between_tools'}
        if request.model.startswith(('claude-haiku-5-5', 'claude-sonnet-5-5')):
            body['output_config'] = {'effort': 'low'}
        if system:
            body['system'] = [{'type': 'text', 'text': instruction} for instruction in system]
        if request.options.stop:
            body['stop_sequences'] = list(request.options.stop)
        if request.tools:
            body['tools'] = [{
                'name': tool.name,
                'description': tool.description,
                'input_schema': tool.input_schema,
            } for tool in request.tools]
        if request.options.tool_choice is not None:
            tool_choice = self._tool_choice(request.options.tool_choice)
            if (request.model.startswith(FORCED_TOOL_CHOICE_UNSUPPORTED)
                    and tool_choice.get('type') in {'any', 'tool'}):
                raise InvalidProviderRequestError(
                    f'{request.model} does not support forced tool choice',
                )
            body['tool_choice'] = tool_choice
        if request.output_schema is not None:
            body.setdefault('output_config', {})['format'] = {
                'type': 'json_schema', 'schema': provider_schema(request.output_schema),
            }

        for key, value in request.options.provider_options.items():
            if key.startswith('_'):
                continue
            if key == 'output_config' and isinstance(value, Mapping):
                body.setdefault('output_config', {}).update(value)
                if request.output_schema is not None:
                    body['output_config']['format'] = {
                        'type': 'json_schema', 'schema': provider_schema(request.output_schema),
                    }
            else:
                body[key] = value

        # Claude Haiku/Sonnet 5.5 reject non-default temperature and top_p.
        # Shared ListingIQ callers may keep supplying those hints; the adapter
        # preserves configuration-only switching by omitting unsupported values.
        return body

    def _messages(self, request: AIRequest) -> tuple[list[str], list[dict[str, Any]]]:
        system = [item for item in request.system_instructions if item]
        messages: list[dict[str, Any]] = []
        inline_system = request.model.startswith(MID_CONVERSATION_SYSTEM_MODELS)
        index = 0
        while index < len(request.messages):
            message = request.messages[index]
            if message.role == Role.SYSTEM:
                if message.text:
                    if (inline_system and messages and messages[-1]['role'] == 'user'
                            and self._next_turn_role(request.messages, index) in (None, Role.ASSISTANT)):
                        messages.append({'role': 'system', 'content': message.text})
                    else:
                        system.append(message.text)
                index += 1
                continue
            if message.role == Role.TOOL:
                results = []
                while index < len(request.messages) and request.messages[index].role == Role.TOOL:
                    results.append(self._tool_result(request.messages[index]))
                    index += 1
                messages.append({'role': 'user', 'content': results})
                continue
            if message.role == Role.USER:
                # The API rejects empty text, and an empty turn carries nothing.
                if message.text:
                    messages.append({'role': 'user', 'content': message.text})
            elif message.role == Role.ASSISTANT:
                content = self._assistant_content(message)
                if content:
                    messages.append({'role': 'assistant', 'content': content})
            else:
                raise InvalidProviderRequestError('Anthropic message role is not supported')
            index += 1
        turns = [item for item in messages if item['role'] != 'system']
        if not turns:
            raise InvalidProviderRequestError('Anthropic request has no non-empty messages')
        if turns[-1]['role'] == 'assistant':
            # A trailing assistant turn is a prefill, which these models reject.
            raise InvalidProviderRequestError(
                'Anthropic request must end with a user message; the latest turn is from the assistant',
            )
        return system, messages

    @staticmethod
    def _next_turn_role(messages: tuple[AIMessage, ...], index: int) -> Role | None:
        for message in messages[index + 1:]:
            if message.role == Role.SYSTEM:
                continue
            if message.role in (Role.USER, Role.ASSISTANT) and not (
                    message.text or message.tool_calls or message.provider_state):
                continue
            return message.role
        return None

    @staticmethod
    def _assistant_content(message: AIMessage) -> str | list[dict[str, Any]]:
        if isinstance(message.provider_state, list):
            if not all(isinstance(block, dict) for block in message.provider_state):
                raise InvalidProviderRequestError('Anthropic provider state is malformed')
            return message.provider_state
        blocks: list[dict[str, Any]] = []
        if message.text:
            blocks.append({'type': 'text', 'text': message.text})
        blocks.extend({
            'type': 'tool_use',
            'id': call.id,
            'name': call.name,
            'input': dict(call.arguments),
        } for call in message.tool_calls)
        return blocks if blocks else message.text

    @staticmethod
    def _tool_result(message: AIMessage) -> dict[str, Any]:
        if not message.tool_call_id:
            raise InvalidProviderRequestError('Anthropic tool result is missing its tool-use ID')
        result: dict[str, Any] = {
            'type': 'tool_result',
            'tool_use_id': message.tool_call_id,
            'content': message.text,
        }
        try:
            parsed = json.loads(message.text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get('error') is not None:
            result['is_error'] = True
        return result

    @staticmethod
    def _tool_choice(value: str | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(value, str):
            mapped = {'required': 'any'}.get(value, value)
            if mapped not in {'auto', 'any', 'none'}:
                raise InvalidProviderRequestError('Anthropic tool choice is not supported')
            return {'type': mapped}
        if not isinstance(value, Mapping):
            raise InvalidProviderRequestError('Anthropic tool choice is malformed')
        choice_type = value.get('type')
        if choice_type == 'function' and isinstance(value.get('function'), Mapping):
            name = value['function'].get('name')
            if isinstance(name, str) and name:
                return {'type': 'tool', 'name': name}
        if choice_type in {'auto', 'any', 'none'}:
            return dict(value)
        if choice_type == 'tool' and isinstance(value.get('name'), str) and value.get('name'):
            return dict(value)
        raise InvalidProviderRequestError('Anthropic tool choice is not supported')

    @staticmethod
    def _parse_content(value: Any) -> tuple[str, tuple[AIToolCall, ...], list[dict[str, Any]]]:
        if not isinstance(value, list):
            raise MalformedProviderResponseError('Anthropic response content must be a list')
        text_parts = []
        calls = []
        state = []
        for block in value:
            if not isinstance(block, dict) or not isinstance(block.get('type'), str):
                raise MalformedProviderResponseError('Anthropic returned a malformed content block')
            state.append(block)
            if block['type'] == 'text':
                if not isinstance(block.get('text'), str):
                    raise MalformedProviderResponseError('Anthropic text block must contain text')
                text_parts.append(block['text'])
            elif block['type'] == 'tool_use':
                call_id, name, arguments = block.get('id'), block.get('name'), block.get('input')
                if (not isinstance(call_id, str) or not call_id
                        or not isinstance(name, str) or not name):
                    raise ToolCallingError('Anthropic tool call is missing an ID or name')
                if not isinstance(arguments, dict):
                    raise ToolCallingError('Anthropic tool call input must be an object')
                calls.append(AIToolCall(
                    id=call_id,
                    name=name,
                    arguments=arguments,
                    raw_arguments=json.dumps(arguments, separators=(',', ':')),
                    arguments_valid=True,
                ))
        return ''.join(text_parts), tuple(calls), state

    @staticmethod
    def _parse_usage(value: Any) -> TokenUsage:
        if not isinstance(value, dict):
            return TokenUsage()

        def integer(key: str) -> int | None:
            item = value.get(key)
            return item if isinstance(item, int) and not isinstance(item, bool) else None

        input_tokens = integer('input_tokens')
        output_tokens = integer('output_tokens')
        cache_read = integer('cache_read_input_tokens')
        cache_write = integer('cache_creation_input_tokens')
        reported = (input_tokens, output_tokens, cache_read, cache_write)
        total = sum(item for item in reported if item is not None) if any(
            item is not None for item in reported
        ) else None
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            source='provider',
        )

    @staticmethod
    def _resolve(value: str | Callable[[], str]) -> str:
        resolved = value() if callable(value) else value
        return resolved if isinstance(resolved, str) else ''

    @staticmethod
    def _header(response: Any, name: str) -> str | None:
        headers = getattr(response, 'headers', {}) or {}
        for key, value in headers.items():
            if str(key).lower() == name.lower() and isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _provider_code(payload: Any) -> str | None:
        if not isinstance(payload, dict) or not isinstance(payload.get('error'), dict):
            return None
        code = payload['error'].get('type')
        return str(code) if code is not None else None

    def _raise_http_error(self, response: Any, payload: Any) -> None:
        status = int(response.status_code)
        request_id = self._header(response, 'request-id')
        if request_id is None and isinstance(payload, dict) and isinstance(payload.get('request_id'), str):
            request_id = payload['request_id']
        common = {
            'http_status': status,
            'provider_code': self._provider_code(payload),
            'provider_request_id': request_id,
        }
        if status == 401:
            raise AuthenticationError('Anthropic authentication failed', **common)
        if status == 403:
            raise PermissionDeniedError('Anthropic permission denied', **common)
        if status == 402:
            raise ProviderConfigurationError('Anthropic billing is unavailable', **common)
        if status == 429:
            retry_after = self._header(response, 'retry-after')
            try:
                retry_after_seconds = float(retry_after) if retry_after is not None else None
            except ValueError:
                retry_after_seconds = None
            raise RateLimitError(
                'Anthropic rate limit exceeded', retryable=True,
                retry_after_seconds=retry_after_seconds, **common,
            )
        if status == 504:
            raise ProviderTimeoutError(
                'Anthropic request timed out', retryable=True,
                ambiguous_completion=True, **common,
            )
        if status >= 500:
            raise ProviderUnavailableError(
                'Anthropic provider is unavailable', retryable=True, **common,
            )
        error = InvalidProviderRequestError('Anthropic rejected the request', **common)
        # Anthropic's validation message names the offending request field
        # (for example a schema limit); kept short for operator diagnostics.
        detail = payload.get('error', {}).get('message') if isinstance(payload, dict) and isinstance(payload.get('error'), dict) else None
        error.provider_message = str(detail)[:300] if detail else None
        raise error
