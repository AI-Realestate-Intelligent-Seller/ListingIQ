"""Privacy-safe provider-attempt events and replaceable persistence hook."""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from ..logger import get_logger, log_event
from .errors import AIError
from .models import AIRequest, AIResponse


logger = get_logger(__name__)


@dataclass(frozen=True)
class ProviderAttemptEvent:
    event_name: str
    operation_id: str
    attempt_id: str
    correlation_id: str | None
    feature: str
    provider: str
    requested_model: str
    response_model: str | None
    provider_request_id: str | None
    started_at: str
    completed_at: str | None
    duration_ms: float | None
    status: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    usage_source: str = 'unavailable'
    estimated_cost_usd: float | None = None
    cost_method: str = 'unavailable'
    pricing_version: str | None = None
    http_status: int | None = None
    error_category: str | None = None
    safe_error_code: str | None = None
    retryable: bool | None = None
    retry_after_ms: int | None = None
    retry_attempt: int = 1
    tool_calls_requested: int = 0
    tool_calls_returned: int | None = None
    structured_output_requested: bool = False
    structured_output_mode: str | None = None
    schema_validation_status: str | None = None


class AttemptEventSink(Protocol):
    def __call__(self, event: ProviderAttemptEvent) -> None:
        ...


def log_attempt_event(event: ProviderAttemptEvent) -> None:
    log_event(logger, event.event_name, **asdict(event))


def emit_safely(sink: AttemptEventSink, event: ProviderAttemptEvent) -> None:
    try:
        sink(event)
    except Exception as exc:  # instrumentation must never affect AI behavior
        log_event(
            logger,
            'ai.instrumentation.failed',
            level=logging.ERROR,
            operation_id=event.operation_id,
            attempt_id=event.attempt_id,
            sink_type=type(sink).__name__,
            cause_type=type(exc).__name__,
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def started_event(request: AIRequest, started_at: datetime) -> ProviderAttemptEvent:
    return ProviderAttemptEvent(
        event_name='ai.provider.attempt.started',
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        correlation_id=request.correlation_id,
        feature=request.feature,
        provider=request.provider,
        requested_model=request.model,
        response_model=None,
        provider_request_id=None,
        started_at=started_at.isoformat(),
        completed_at=None,
        duration_ms=None,
        status='started',
        tool_calls_requested=len(request.tools),
        structured_output_requested=request.output_schema is not None,
        structured_output_mode=(request.structured_output_mode.value
                                if request.output_schema is not None else None),
    )


def completed_event(
    request: AIRequest,
    response: AIResponse,
    started_at: datetime,
    completed_at: datetime,
) -> ProviderAttemptEvent:
    usage = response.usage
    validation = None
    if request.output_schema is not None:
        validation = 'valid' if response.structured_output_valid else 'not_validated'
    return ProviderAttemptEvent(
        event_name='ai.provider.attempt.completed',
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        correlation_id=request.correlation_id,
        feature=request.feature,
        provider=request.provider,
        requested_model=request.model,
        response_model=response.model,
        provider_request_id=response.provider_request_id,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_ms=max(0.0, (completed_at - started_at).total_seconds() * 1000),
        status='success',
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        usage_source=usage.source,
        tool_calls_requested=len(request.tools),
        tool_calls_returned=len(response.tool_calls),
        structured_output_requested=request.output_schema is not None,
        structured_output_mode=(request.structured_output_mode.value
                                if request.output_schema is not None else None),
        schema_validation_status=validation,
    )


def failed_event(
    request: AIRequest,
    error: AIError,
    started_at: datetime,
    completed_at: datetime,
) -> ProviderAttemptEvent:
    return ProviderAttemptEvent(
        event_name='ai.provider.attempt.failed',
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        correlation_id=request.correlation_id,
        feature=request.feature,
        provider=request.provider,
        requested_model=request.model,
        response_model=None,
        provider_request_id=error.provider_request_id,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_ms=max(0.0, (completed_at - started_at).total_seconds() * 1000),
        status='failure',
        http_status=error.http_status,
        error_category=error.category,
        safe_error_code=error.provider_code,
        retryable=error.retryable,
        retry_after_ms=(int(error.retry_after_seconds * 1000)
                        if error.retry_after_seconds is not None else None),
        tool_calls_requested=len(request.tools),
        structured_output_requested=request.output_schema is not None,
        structured_output_mode=(request.structured_output_mode.value
                                if request.output_schema is not None else None),
        schema_validation_status=('failed' if request.output_schema is not None else None),
    )
