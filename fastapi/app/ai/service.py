"""Provider selection, capability validation, typed results, and tracing."""

import json
from dataclasses import is_dataclass, replace
from typing import Any, Callable, Mapping, TypeVar

import requests

from ..core.config import settings
from .errors import (
    AIError,
    InvalidProviderRequestError,
    MalformedOutputError,
    SchemaValidationError,
    ToolCallingError,
    UnsupportedCapabilityError,
)
from .instrumentation import (
    AttemptEventSink,
    completed_event,
    emit_safely,
    failed_event,
    log_attempt_event,
    started_event,
    utc_now,
)
from .models import AIRequest, AIResponse, StructuredOutputMode
from .provider import AIProvider
from .providers import DeepSeekProvider
from .registry import ProviderRegistry


StructuredT = TypeVar('StructuredT')


class AIService:
    def __init__(
        self,
        registry: ProviderRegistry,
        event_sink: AttemptEventSink = log_attempt_event,
    ) -> None:
        self.registry = registry
        self.event_sink = event_sink

    def generate(self, request: AIRequest) -> AIResponse[Any]:
        return self._execute(request, lambda response: response)

    def generate_text(self, request: AIRequest) -> AIResponse[None]:
        if request.tools or request.output_schema is not None:
            raise InvalidProviderRequestError(
                'Text generation cannot include tools or an output schema',
            ).with_request(request)

        def require_text(response: AIResponse[Any]) -> AIResponse[None]:
            if not response.text.strip():
                raise MalformedOutputError('Provider returned no usable text')
            return replace(response, structured=None)

        return self._execute(request, require_text)

    def generate_structured(
        self,
        request: AIRequest,
        result_type: type[StructuredT] = dict,
    ) -> AIResponse[StructuredT]:
        if request.output_schema is None:
            raise InvalidProviderRequestError(
                'Structured generation requires an output schema',
            ).with_request(request)
        if request.tools:
            raise InvalidProviderRequestError(
                'Structured generation cannot include tools',
            ).with_request(request)

        def parse(response: AIResponse[Any]) -> AIResponse[StructuredT]:
            try:
                value = json.loads(response.text)
            except (TypeError, ValueError) as exc:
                raise MalformedOutputError(
                    'Provider output is not valid JSON', cause=exc,
                ) from exc
            _validate_json_schema(value, request.output_schema)
            structured = _coerce_result(value, result_type)
            return replace(
                response,
                structured=structured,
                structured_output_valid=True,
            )

        return self._execute(request, parse)

    def generate_with_tools(self, request: AIRequest) -> AIResponse[None]:
        if not request.tools:
            raise InvalidProviderRequestError(
                'Tool generation requires at least one tool definition',
            ).with_request(request)
        if request.output_schema is not None:
            raise InvalidProviderRequestError(
                'Tool generation cannot include an output schema',
            ).with_request(request)

        def require_valid_calls(response: AIResponse[Any]) -> AIResponse[None]:
            if any(not call.arguments_valid for call in response.tool_calls):
                raise ToolCallingError('Provider returned invalid tool call arguments')
            return replace(response, structured=None)

        return self._execute(request, require_valid_calls)

    def _execute(
        self,
        request: AIRequest,
        postprocess: Callable[[AIResponse[Any]], AIResponse[Any]],
    ) -> AIResponse[Any]:
        provider = self.registry.resolve(request.provider)
        self._validate_request(request, provider)
        started_at = utc_now()
        emit_safely(self.event_sink, started_event(request, started_at))
        try:
            response = provider.generate(request)
            response = postprocess(response)
        except AIError as exc:
            exc.with_request(request)
            completed_at = utc_now()
            emit_safely(self.event_sink, failed_event(request, exc, started_at, completed_at))
            raise
        completed_at = utc_now()
        emit_safely(self.event_sink, completed_event(request, response, started_at, completed_at))
        return response

    @staticmethod
    def _validate_request(request: AIRequest, provider: AIProvider) -> None:
        if not request.feature or not request.operation_id or not request.attempt_id:
            raise InvalidProviderRequestError(
                'Feature, operation ID, and attempt ID are required',
            ).with_request(request)
        if not request.model:
            raise InvalidProviderRequestError('Model is required').with_request(request)
        if request.timeout_seconds <= 0:
            raise InvalidProviderRequestError(
                'Timeout must be greater than zero',
            ).with_request(request)
        if request.max_output_tokens is not None and request.max_output_tokens <= 0:
            raise InvalidProviderRequestError(
                'Maximum output tokens must be greater than zero',
            ).with_request(request)
        if request.options.temperature is not None and not 0 <= request.options.temperature <= 2:
            raise InvalidProviderRequestError(
                'Temperature must be between zero and two',
            ).with_request(request)
        if request.options.top_p is not None and not 0 <= request.options.top_p <= 1:
            raise InvalidProviderRequestError(
                'Top-p must be between zero and one',
            ).with_request(request)

        capabilities = provider.capabilities(request.model)
        if not capabilities.text_generation:
            raise UnsupportedCapabilityError(
                'Selected provider does not support text generation',
            ).with_request(request)
        if request.tools and not capabilities.tool_calling:
            raise UnsupportedCapabilityError(
                'Selected provider does not support tool calling',
            ).with_request(request)
        if (request.output_schema is not None
                and request.structured_output_mode == StructuredOutputMode.NATIVE_REQUIRED
                and not capabilities.native_structured_output):
            raise UnsupportedCapabilityError(
                'Selected provider cannot enforce native structured output',
            ).with_request(request)
        if request.output_schema is not None:
            _validate_schema_definition(request.output_schema)
        if (request.max_output_tokens is not None
                and capabilities.maximum_output_tokens is not None
                and request.max_output_tokens > capabilities.maximum_output_tokens):
            raise UnsupportedCapabilityError(
                'Requested output token limit exceeds the model capability',
            ).with_request(request)
        requested_options = {
            name for name, present in (
                ('temperature', request.options.temperature is not None),
                ('top_p', request.options.top_p is not None),
                ('stop', bool(request.options.stop)),
                ('max_output_tokens', request.max_output_tokens is not None),
                ('tool_choice', request.options.tool_choice is not None),
            ) if present
        }
        requested_options.update(
            key for key in request.options.provider_options if not key.startswith('_')
        )
        unsupported = requested_options - capabilities.supported_generation_options
        if unsupported:
            raise UnsupportedCapabilityError(
                f'Selected provider does not support options: {", ".join(sorted(unsupported))}',
            ).with_request(request)


def _coerce_result(value: Any, result_type: type[StructuredT]) -> StructuredT:
    if result_type in (dict, list, str, int, float, bool):
        if not isinstance(value, result_type):
            raise SchemaValidationError('Parsed JSON does not match the requested result type')
        return value
    parse_obj = getattr(result_type, 'parse_obj', None)
    if callable(parse_obj):
        try:
            return parse_obj(value)
        except Exception as exc:
            raise SchemaValidationError(
                'Parsed JSON failed result-type validation', cause=exc,
            ) from exc
    if is_dataclass(result_type):
        try:
            return result_type(**value)
        except (TypeError, ValueError) as exc:
            raise SchemaValidationError(
                'Parsed JSON failed dataclass validation', cause=exc,
            ) from exc
    raise UnsupportedCapabilityError('Requested structured result type is not supported')


_SCHEMA_KEYWORDS = frozenset({
    '$schema', 'title', 'description', 'type', 'properties', 'required',
    'additionalProperties', 'items', 'enum', 'minLength', 'maxLength',
    'minItems', 'maxItems', 'minimum', 'maximum',
})


def _validate_schema_definition(schema: Mapping[str, Any]) -> None:
    if not isinstance(schema, Mapping):
        raise InvalidProviderRequestError('Output schema must be an object')
    unsupported = set(schema) - _SCHEMA_KEYWORDS
    if unsupported:
        raise UnsupportedCapabilityError(
            f'Output schema uses unsupported keywords: {", ".join(sorted(unsupported))}',
        )
    properties = schema.get('properties', {})
    if not isinstance(properties, Mapping):
        raise InvalidProviderRequestError('Schema properties must be an object')
    for child in properties.values():
        _validate_schema_definition(child)
    if 'items' in schema:
        _validate_schema_definition(schema['items'])


def _validate_json_schema(value: Any, schema: Mapping[str, Any], path: str = '$') -> None:
    if 'enum' in schema and value not in schema['enum']:
        raise SchemaValidationError(f'{path} is not one of the allowed values')
    expected = schema.get('type')
    type_checks = {
        'object': lambda item: isinstance(item, dict),
        'array': lambda item: isinstance(item, list),
        'string': lambda item: isinstance(item, str),
        'number': lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        'integer': lambda item: isinstance(item, int) and not isinstance(item, bool),
        'boolean': lambda item: isinstance(item, bool),
        'null': lambda item: item is None,
    }
    if expected is not None:
        if expected not in type_checks:
            raise UnsupportedCapabilityError(f'Unsupported JSON Schema type: {expected}')
        if not type_checks[expected](value):
            raise SchemaValidationError(f'{path} must be of type {expected}')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if not isinstance(properties, Mapping):
            raise InvalidProviderRequestError('Schema properties must be an object')
        required = schema.get('required', [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise InvalidProviderRequestError('Schema required must be a string list')
        missing = [name for name in required if name not in value]
        if missing:
            raise SchemaValidationError(f'{path} is missing required fields: {", ".join(missing)}')
        if schema.get('additionalProperties') is False:
            extras = set(value) - set(properties)
            if extras:
                raise SchemaValidationError(f'{path} contains unsupported fields')
        for name, child in value.items():
            if name in properties:
                _validate_json_schema(child, properties[name], f'{path}.{name}')
    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            raise SchemaValidationError(f'{path} contains too few items')
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            raise SchemaValidationError(f'{path} contains too many items')
        if 'items' in schema:
            for index, child in enumerate(value):
                _validate_json_schema(child, schema['items'], f'{path}[{index}]')
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']:
            raise SchemaValidationError(f'{path} is too short')
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            raise SchemaValidationError(f'{path} is too long')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in schema and value < schema['minimum']:
            raise SchemaValidationError(f'{path} is below the minimum')
        if 'maximum' in schema and value > schema['maximum']:
            raise SchemaValidationError(f'{path} is above the maximum')


default_registry = ProviderRegistry()
default_registry.register('deepseek', DeepSeekProvider(
    # Resolve per request, matching the original client's configuration timing.
    api_key=lambda: settings['ai']['api_key'] or '',
    base_url=lambda: settings['ai']['base_url'],
    # Keep requests.post dynamically patchable at the historical module path.
    http_post=lambda *args, **kwargs: requests.post(*args, **kwargs),
))
ai_service = AIService(default_registry)
