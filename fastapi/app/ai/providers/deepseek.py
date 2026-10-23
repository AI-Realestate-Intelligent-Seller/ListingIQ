"""DeepSeek OpenAI-compatible chat-completions adapter."""

import json
import time
from typing import Any, Callable, Mapping

import requests

from ..errors import (
    AuthenticationError,
    InvalidProviderRequestError,
    MalformedProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderConfigurationError,
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


class DeepSeekProvider:
    provider_id = 'deepseek'

    def __init__(
        self,
        *,
        api_key: str | Callable[[], str],
        base_url: str | Callable[[], str],
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._http_post = http_post or requests.post

    def capabilities(self, model: str) -> ProviderCapabilities:
        return ProviderCapabilities(
            text_generation=True,
            # JSON-object response_format is supported, but DeepSeek does not
            # enforce an arbitrary caller-supplied JSON Schema.
            native_structured_output=False,
            tool_calling=True,
            parallel_tool_calls=True,
            thinking_or_reasoning_controls=True,
            prompt_caching=True,
            provider_token_usage=True,
            cache_token_usage=True,
            supported_generation_options=frozenset({
                'temperature', 'top_p', 'stop', 'max_output_tokens',
                'tool_choice', 'response_format',
            }),
        )

    def generate(self, request: AIRequest) -> AIResponse[Any]:
        api_key = self._resolve(self._api_key)
        if not api_key:
            raise ProviderConfigurationError('DeepSeek API key is not configured')

        body = self._build_body(request)
        started = time.perf_counter()
        try:
            response = self._http_post(
                f'{self._resolve(self._base_url).rstrip("/")}/chat/completions',
                json=body,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                timeout=request.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise ProviderTimeoutError(
                'DeepSeek request timed out', retryable=True,
                ambiguous_completion=True, cause=exc,
            ) from exc
        except requests.RequestException as exc:
            raise ProviderUnavailableError(
                'DeepSeek provider is unreachable', retryable=True, cause=exc,
            ) from exc

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            if response.ok:
                raise MalformedProviderResponseError(
                    'DeepSeek returned a non-JSON success response', cause=exc,
                ) from exc
            payload = {}

        if not response.ok:
            self._raise_http_error(response, payload)
        if not isinstance(payload, dict):
            raise MalformedProviderResponseError('DeepSeek response must be an object')

        choices = payload.get('choices')
        if not isinstance(choices, list) or not choices:
            raise MalformedProviderResponseError('DeepSeek response contains no choices')
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
            raise MalformedProviderResponseError('DeepSeek response contains no assistant message')
        message = choice['message']
        legacy_response = request.options.provider_options.get('_legacy_response') is True
        tool_calls = self._parse_tool_calls(
            message.get('tool_calls'),
            allow_invalid=legacy_response,
        )
        content = message.get('content')
        if content is None:
            content = ''
        if not isinstance(content, str):
            if legacy_response:
                content = ''
            else:
                raise MalformedProviderResponseError('DeepSeek assistant content must be text or null')

        usage = self._parse_usage(payload.get('usage'))
        provider_request_id = self._header(response, 'x-request-id')
        if provider_request_id is None and isinstance(payload.get('id'), str):
            provider_request_id = payload['id']
        response_model = payload.get('model') if isinstance(payload.get('model'), str) else request.model
        return AIResponse(
            text=content,
            structured=None,
            tool_calls=tool_calls,
            stop_reason=choice.get('finish_reason') if isinstance(choice.get('finish_reason'), str) else None,
            provider=self.provider_id,
            model=response_model,
            provider_request_id=provider_request_id,
            usage=usage,
            latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
            structured_output_valid=None,
            # Private compatibility state; instrumentation never serializes it.
            provider_metadata=({'_legacy_message': message} if legacy_response else {}),
        )

    def _build_body(self, request: AIRequest) -> dict[str, Any]:
        legacy_messages = request.options.provider_options.get('_legacy_messages')
        if isinstance(legacy_messages, list):
            messages = legacy_messages
        else:
            messages = [
                {'role': Role.SYSTEM.value, 'content': instruction}
                for instruction in request.system_instructions
            ]
            messages.extend(self._message(message) for message in request.messages)
        body: dict[str, Any] = {
            'model': request.model,
            'messages': messages,
            'thinking': {'type': 'disabled'},
            'stream': False,
        }
        if request.options.temperature is not None:
            body['temperature'] = request.options.temperature
        if request.options.top_p is not None:
            body['top_p'] = request.options.top_p
        if request.options.stop:
            body['stop'] = list(request.options.stop)
        if request.max_output_tokens is not None:
            body['max_tokens'] = request.max_output_tokens
        if request.tools:
            body['tools'] = [{
                'type': 'function',
                'function': {
                    'name': tool.name,
                    'description': tool.description,
                    'parameters': tool.input_schema,
                },
            } for tool in request.tools]
        if request.options.tool_choice is not None:
            body['tool_choice'] = request.options.tool_choice
        for key, value in request.options.provider_options.items():
            if not key.startswith('_'):
                body[key] = value
        legacy_options = request.options.provider_options.get('_legacy_options')
        if isinstance(legacy_options, Mapping):
            # The old facade spread **options last; retain that pass-through.
            body.update(legacy_options)
        return body

    @staticmethod
    def _message(message: AIMessage) -> dict[str, Any]:
        result: dict[str, Any] = {'role': message.role.value, 'content': message.text}
        if message.tool_call_id is not None:
            result['tool_call_id'] = message.tool_call_id
        if message.tool_calls:
            result['tool_calls'] = [{
                'id': call.id,
                'type': 'function',
                'function': {
                    'name': call.name,
                    'arguments': (call.raw_arguments if call.raw_arguments is not None
                                  else json.dumps(dict(call.arguments), separators=(',', ':'))),
                },
            } for call in message.tool_calls]
        return result

    @staticmethod
    def _parse_tool_calls(value: Any, *, allow_invalid: bool) -> tuple[AIToolCall, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ToolCallingError('DeepSeek tool_calls must be a list')
        calls = []
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get('function'), dict):
                if allow_invalid:
                    continue
                raise ToolCallingError('DeepSeek returned a malformed tool call')
            function = item['function']
            call_id, name = item.get('id'), function.get('name')
            raw_arguments = function.get('arguments', '{}')
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                if allow_invalid:
                    continue
                raise ToolCallingError('DeepSeek tool call is missing an ID or function name')
            if not isinstance(raw_arguments, str):
                if allow_invalid:
                    continue
                raise ToolCallingError('DeepSeek tool call arguments must be JSON text')
            try:
                arguments = json.loads(raw_arguments)
            except ValueError as exc:
                if not allow_invalid:
                    raise ToolCallingError('DeepSeek tool call arguments are invalid JSON', cause=exc) from exc
                calls.append(AIToolCall(call_id, name, {}, raw_arguments, False))
                continue
            if not isinstance(arguments, dict):
                if not allow_invalid:
                    raise ToolCallingError('DeepSeek tool call arguments must decode to an object')
                calls.append(AIToolCall(call_id, name, {}, raw_arguments, False))
                continue
            calls.append(AIToolCall(call_id, name, arguments, raw_arguments, True))
        return tuple(calls)

    @staticmethod
    def _resolve(value: str | Callable[[], str]) -> str:
        resolved = value() if callable(value) else value
        return resolved if isinstance(resolved, str) else ''

    @staticmethod
    def _parse_usage(value: Any) -> TokenUsage:
        if not isinstance(value, dict):
            return TokenUsage()
        integer = lambda key: (value.get(key)
                               if isinstance(value.get(key), int)
                               and not isinstance(value.get(key), bool) else None)
        return TokenUsage(
            input_tokens=integer('prompt_tokens'),
            output_tokens=integer('completion_tokens'),
            total_tokens=integer('total_tokens'),
            cache_read_tokens=integer('prompt_cache_hit_tokens'),
            cache_write_tokens=integer('prompt_cache_miss_tokens'),
            source='provider',
        )

    @staticmethod
    def _header(response: Any, name: str) -> str | None:
        headers = getattr(response, 'headers', {}) or {}
        value = headers.get(name) or headers.get(name.title())
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _provider_code(payload: Any) -> str | None:
        if not isinstance(payload, dict) or not isinstance(payload.get('error'), dict):
            return None
        code = payload['error'].get('code') or payload['error'].get('type')
        return str(code) if code is not None else None

    def _raise_http_error(self, response: Any, payload: Any) -> None:
        status = int(response.status_code)
        common = {
            'http_status': status,
            'provider_code': self._provider_code(payload),
            'provider_request_id': self._header(response, 'x-request-id'),
        }
        if status in (401, 403):
            raise AuthenticationError('DeepSeek authentication failed', **common)
        if status == 429:
            retry_after = self._header(response, 'retry-after')
            try:
                retry_after_seconds = float(retry_after) if retry_after is not None else None
            except ValueError:
                retry_after_seconds = None
            raise RateLimitError(
                'DeepSeek rate limit exceeded', retryable=True,
                retry_after_seconds=retry_after_seconds, **common,
            )
        if status >= 500:
            raise ProviderUnavailableError(
                'DeepSeek provider is unavailable', retryable=True, **common,
            )
        raise InvalidProviderRequestError('DeepSeek rejected the request', **common)
