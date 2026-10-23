"""Backward-compatible DeepSeek completion facade over the common AI service."""

import json
from typing import Any
from uuid import uuid4

import requests  # retained so historical tests can patch deepseek.requests.post

from ..ai.errors import AIError, MalformedProviderResponseError
from ..ai.models import (
    AIMessage,
    AIRequest,
    AIToolCall,
    AIToolDefinition,
    GenerationOptions,
    Role,
)
from ..ai.service import ai_service
from ..core.config import settings
from ..logger import session_id_var


class AiUnavailableError(RuntimeError):
    """Legacy failure type caught by existing feature-level fallbacks."""


def is_configured() -> bool:
    return bool(settings['ai']['api_key'])


def completion(messages: list, **options) -> dict:
    """Return one legacy OpenAI-compatible assistant message dictionary."""
    if not settings['ai']['api_key']:
        raise AiUnavailableError('DEEPSEEK_API_KEY (or AI_API_KEY) is not configured')

    operation_id = uuid4().hex
    correlation_id = session_id_var.get()
    legacy_options = dict(options)
    request = AIRequest(
        feature='legacy.deepseek.completion',
        operation_id=operation_id,
        attempt_id=uuid4().hex,
        correlation_id=correlation_id if correlation_id != '-' else None,
        provider='deepseek',
        model=settings['ai']['model'],
        messages=tuple(_message_from_legacy(item) for item in messages),
        tools=tuple(_tool_from_legacy(item) for item in options.pop('tools', [])),
        max_output_tokens=options.pop('max_tokens', None),
        timeout_seconds=settings['ai']['timeout_seconds'],
        options=GenerationOptions(
            temperature=options.pop('temperature', None),
            top_p=options.pop('top_p', None),
            stop=_normalize_stop(options.pop('stop', ())),
            tool_choice=options.pop('tool_choice', None),
            provider_options={
                '_legacy_messages': messages,
                '_legacy_options': legacy_options,
                '_legacy_response': True,
            },
        ),
    )
    try:
        response = ai_service.generate(request)
    except MalformedProviderResponseError:
        # The historical client returned {} for invalid JSON, missing choices,
        # an empty choices list, and a missing message. Preserve that boundary.
        return {}
    except AIError as exc:
        raise AiUnavailableError(str(exc)) from exc

    raw_message = response.provider_metadata.get('_legacy_message')
    if isinstance(raw_message, dict):
        return raw_message

    result: dict[str, Any] = {'role': 'assistant', 'content': response.text}
    if response.tool_calls:
        result['tool_calls'] = [_tool_call_to_legacy(call) for call in response.tool_calls]
    return result


def _message_from_legacy(value: Any) -> AIMessage:
    if not isinstance(value, dict):
        raise AiUnavailableError('AI message must be an object')
    try:
        role = Role(value.get('role'))
    except ValueError as exc:
        raise AiUnavailableError('AI message role is invalid') from exc
    content = value.get('content')
    text = '' if content is None else content
    if not isinstance(text, str):
        raise AiUnavailableError('AI message content must be text or null')
    calls = tuple(_tool_call_from_legacy(item) for item in value.get('tool_calls', []) or [])
    return AIMessage(
        role=role,
        text=text,
        tool_call_id=value.get('tool_call_id'),
        tool_calls=calls,
    )


def _tool_from_legacy(value: Any) -> AIToolDefinition:
    if not isinstance(value, dict) or not isinstance(value.get('function'), dict):
        raise AiUnavailableError('AI tool definition is invalid')
    function = value['function']
    name = function.get('name')
    parameters = function.get('parameters', {})
    if not isinstance(name, str) or not isinstance(parameters, dict):
        raise AiUnavailableError('AI tool definition is invalid')
    return AIToolDefinition(
        name=name,
        description=function.get('description') if isinstance(function.get('description'), str) else '',
        input_schema=parameters,
    )


def _tool_call_from_legacy(value: Any) -> AIToolCall:
    if not isinstance(value, dict) or not isinstance(value.get('function'), dict):
        raise AiUnavailableError('AI tool call is invalid')
    function = value['function']
    raw_arguments = function.get('arguments', '{}')
    try:
        arguments = json.loads(raw_arguments)
    except (TypeError, ValueError):
        arguments = {}
    valid = isinstance(arguments, dict)
    return AIToolCall(
        id=str(value.get('id') or ''),
        name=str(function.get('name') or ''),
        arguments=arguments if valid else {},
        raw_arguments=raw_arguments if isinstance(raw_arguments, str) else None,
        arguments_valid=valid,
    )


def _tool_call_to_legacy(call: AIToolCall) -> dict[str, Any]:
    return {
        'id': call.id,
        'type': 'function',
        'function': {
            'name': call.name,
            'arguments': (call.raw_arguments if call.raw_arguments is not None
                          else json.dumps(dict(call.arguments), separators=(',', ':'))),
        },
    }


def _normalize_stop(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise AiUnavailableError('AI stop option must be text or a list of text')
