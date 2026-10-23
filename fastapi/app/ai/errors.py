"""Typed, privacy-safe errors shared by AI providers."""

from typing import Any


class AIError(RuntimeError):
    category = 'ai_error'

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        feature: str | None = None,
        operation_id: str | None = None,
        attempt_id: str | None = None,
        http_status: int | None = None,
        provider_code: str | None = None,
        provider_request_id: str | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
        ambiguous_completion: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.feature = feature
        self.operation_id = operation_id
        self.attempt_id = attempt_id
        self.http_status = http_status
        self.provider_code = provider_code
        self.provider_request_id = provider_request_id
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.ambiguous_completion = ambiguous_completion
        self.cause_type = type(cause).__name__ if cause is not None else None

    def with_request(self, request: Any) -> 'AIError':
        for name in ('provider', 'model', 'feature', 'operation_id', 'attempt_id'):
            if getattr(self, name) is None:
                setattr(self, name, getattr(request, name, None))
        return self


class AuthenticationError(AIError):
    category = 'authentication'


class ProviderConfigurationError(AuthenticationError):
    category = 'provider_configuration'


class RateLimitError(AIError):
    category = 'rate_limit'


class ProviderUnavailableError(AIError):
    category = 'provider_unavailable'


class ProviderTimeoutError(AIError):
    category = 'timeout'


class InvalidProviderRequestError(AIError):
    category = 'invalid_request'


class UnsupportedCapabilityError(AIError):
    category = 'unsupported_capability'


class ProviderNotImplementedError(AIError):
    category = 'provider_not_implemented'


class MalformedProviderResponseError(AIError):
    category = 'malformed_provider_response'


class MalformedOutputError(AIError):
    category = 'malformed_output'


class SchemaValidationError(AIError):
    category = 'schema_validation'


class ToolCallingError(AIError):
    category = 'tool_calling'
