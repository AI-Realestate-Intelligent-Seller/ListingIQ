"""Explicit provider registry; registration never implies activation."""

from .errors import ProviderNotImplementedError
from .provider import AIProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, AIProvider | None] = {
            'deepseek': None,
            'anthropic': None,
            'openai': None,
        }

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def register(self, provider_id: str, provider: AIProvider) -> None:
        if provider_id not in self._providers:
            raise ValueError(f'Unknown AI provider: {provider_id}')
        self._providers[provider_id] = provider

    def resolve(self, provider_id: str) -> AIProvider:
        if provider_id not in self._providers:
            raise ProviderNotImplementedError(
                f'AI provider is not supported: {provider_id}', provider=provider_id,
            )
        provider = self._providers[provider_id]
        if provider is None:
            raise ProviderNotImplementedError(
                f'AI provider is registered but not implemented: {provider_id}',
                provider=provider_id,
            )
        return provider
