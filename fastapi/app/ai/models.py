"""Stable provider-neutral data models for one AI provider attempt."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Mapping, TypeVar


JsonObject = dict[str, Any]
StructuredT = TypeVar('StructuredT')


class Role(str, Enum):
    SYSTEM = 'system'
    USER = 'user'
    ASSISTANT = 'assistant'
    TOOL = 'tool'


class StructuredOutputMode(str, Enum):
    NATIVE_REQUIRED = 'native_required'
    VALIDATE_AFTER_GENERATION = 'validate_after_generation'


@dataclass(frozen=True)
class AIToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]
    # Retained only for the legacy OpenAI-compatible continuation path. It is
    # never emitted to operational logs.
    raw_arguments: str | None = None
    arguments_valid: bool = True


@dataclass(frozen=True)
class AIMessage:
    role: Role
    text: str = ''
    tool_call_id: str | None = None
    tool_calls: tuple[AIToolCall, ...] = ()
    provider_state: Any | None = None


@dataclass(frozen=True)
class AIToolDefinition:
    name: str
    description: str
    input_schema: JsonObject


@dataclass(frozen=True)
class GenerationOptions:
    temperature: float | None = None
    top_p: float | None = None
    stop: tuple[str, ...] = ()
    tool_choice: str | Mapping[str, Any] | None = None
    provider_options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIRequest:
    feature: str
    operation_id: str
    attempt_id: str
    provider: str
    model: str
    messages: tuple[AIMessage, ...]
    correlation_id: str | None = None
    system_instructions: tuple[str, ...] = ()
    tools: tuple[AIToolDefinition, ...] = ()
    output_schema: JsonObject | None = None
    structured_output_mode: StructuredOutputMode = StructuredOutputMode.VALIDATE_AFTER_GENERATION
    max_output_tokens: int | None = None
    timeout_seconds: float = 30.0
    options: GenerationOptions = field(default_factory=GenerationOptions)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    source: str = 'unavailable'


@dataclass(frozen=True)
class AIResponse(Generic[StructuredT]):
    text: str
    structured: StructuredT | None
    tool_calls: tuple[AIToolCall, ...]
    stop_reason: str | None
    provider: str
    model: str
    provider_request_id: str | None
    usage: TokenUsage
    latency_ms: float
    structured_output_valid: bool | None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    text_generation: bool = True
    native_structured_output: bool = False
    tool_calling: bool = False
    parallel_tool_calls: bool = False
    thinking_or_reasoning_controls: bool = False
    prompt_caching: bool = False
    provider_token_usage: bool = False
    cache_token_usage: bool = False
    supported_generation_options: frozenset[str] = frozenset()
    maximum_context_tokens: int | None = None
    maximum_output_tokens: int | None = None
