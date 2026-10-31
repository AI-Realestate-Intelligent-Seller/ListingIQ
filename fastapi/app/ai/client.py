"""Provider-neutral feature entry point over the common AI service."""

import json
from typing import Any
from uuid import uuid4

from ..core.config import settings
from ..logger import session_id_var
from .configuration import configuration_error
from .errors import AIError, MalformedProviderResponseError
from .models import (
    AIMessage,
    AIRequest,
    AIToolCall,
    AIToolDefinition,
    GenerationOptions,
    Role,
)
from .service import ai_service


class AiUnavailableError(RuntimeError):
    """Feature-safe failure type caught by the existing AI fallbacks.

    ``category`` is a sanitized failure class (for example ``timeout`` or
    ``provider_configuration``) that features may report; it never contains
    request or response content.
    """

    def __init__(self, message: str, *, category: str = 'ai_error') -> None:
        super().__init__(message)
        self.category = category


def is_configured() -> bool:
    if configuration_error(settings['ai']):
        return False
    provider = settings['ai']['provider']
    if provider == 'anthropic':
        return bool(settings['ai']['anthropic_api_key'])
    return bool(settings['ai']['api_key'])


def _native_schema_supported(provider: str) -> bool:
    try:
        adapter = ai_service.registry.resolve(provider)
    except AIError as exc:
        raise AiUnavailableError(str(exc), category=exc.category) from exc
    return adapter.capabilities(settings['ai']['model']).native_structured_output


def completion(messages: list, **options) -> dict:
    """Return one legacy-compatible assistant message via the selected provider."""
    provider = settings['ai']['provider']
    config_error = configuration_error(settings['ai'])
    if config_error:
        raise AiUnavailableError(config_error, category='provider_configuration')
    if not is_configured():
        if provider == 'deepseek':
            message = 'DEEPSEEK_API_KEY (or AI_API_KEY) is not configured'
        else:
            message = f'AI credentials are not configured for provider: {provider}'
        raise AiUnavailableError(message, category='provider_configuration')

    operation_id = uuid4().hex
    correlation_id = session_id_var.get()
    # The feature schema is the shared response contract. Only providers that
    # enforce schemas natively receive it; DeepSeek keeps its original request.
    # Every provider's output is then normalized and validated by the feature's
    # existing parser, so results do not depend on which provider answered.
    output_schema = options.pop('output_schema', None)
    if output_schema is not None and not _native_schema_supported(provider):
        output_schema = None
    feature = options.pop('feature', 'ai.completion')
    anthropic_token_budget = options.pop('anthropic_token_budget', None)
    # Tools that were offered earlier in this conversation. A follow-up request
    # that must not call tools passes them here instead of `tools`.
    history_tools = options.pop('history_tools', None) or []
    # Long-running background features (document extraction) may need a longer
    # timeout than interactive requests. Other callers keep the global value.
    timeout_seconds = options.pop('timeout_seconds', None)
    if timeout_seconds is None:
        timeout_seconds = settings['ai']['timeout_seconds']
    legacy_options = dict(options)
    requested_max_tokens = options.pop('max_tokens', None)
    tools = tuple(_tool_from_legacy(item) for item in options.pop('tools', []))
    tool_choice = options.pop('tool_choice', None)
    if provider == 'anthropic':
        if isinstance(anthropic_token_budget, int) and not isinstance(anthropic_token_budget, bool):
            # An explicit limit for a feature whose output is known to be large.
            if anthropic_token_budget <= 0:
                raise AiUnavailableError('Anthropic token budget must be positive',
                                         category='invalid_request')
            requested_max_tokens = anthropic_token_budget
        elif anthropic_token_budget not in (None, 'simple', 'complex'):
            raise AiUnavailableError('Anthropic token budget must be simple or complex',
                                     category='invalid_request')
        else:
            budget = anthropic_token_budget or ('complex' if tools else 'simple')
            requested_max_tokens = settings['ai'][f'anthropic_{budget}_max_output_tokens']
        if history_tools and not tools:
            # Claude accepts replayed tool turns and thinking blocks only while
            # the tool list before them is unchanged, so resend it and forbid
            # new calls. DeepSeek ignores this and keeps its original request.
            tools = tuple(_tool_from_legacy(item) for item in history_tools)
            tool_choice = 'none'
    request = AIRequest(
        feature=feature,
        operation_id=operation_id,
        attempt_id=uuid4().hex,
        correlation_id=correlation_id if correlation_id != '-' else None,
        provider=provider,
        model=settings['ai']['model'],
        messages=tuple(_message_from_legacy(item) for item in messages),
        tools=tools,
        output_schema=output_schema,
        max_output_tokens=requested_max_tokens,
        timeout_seconds=timeout_seconds,
        options=GenerationOptions(
            temperature=options.pop('temperature', None),
            top_p=options.pop('top_p', None),
            stop=_normalize_stop(options.pop('stop', ())),
            tool_choice=tool_choice,
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
        # Preserve the historical empty-envelope behavior used by feature fallbacks.
        return {}
    except AIError as exc:
        raise AiUnavailableError(str(exc), category=exc.category) from exc

    if provider == 'anthropic' and response.stop_reason in {'max_tokens', 'refusal'}:
        raise AiUnavailableError(
            f'Anthropic response stopped before a usable result: {response.stop_reason}',
            category='incomplete_output' if response.stop_reason == 'max_tokens' else 'refusal',
        )

    raw_message = response.provider_metadata.get('_legacy_message')
    if isinstance(raw_message, dict):
        return raw_message

    result: dict[str, Any] = {'role': 'assistant', 'content': response.text}
    if response.tool_calls:
        result['tool_calls'] = [_tool_call_to_legacy(call) for call in response.tool_calls]
    provider_state = response.provider_metadata.get('_provider_state')
    if provider_state is not None:
        result['_provider_state'] = provider_state
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
        provider_state=value.get('_provider_state'),
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
