"""Provider adapter protocol."""

from typing import Any, Protocol

from .models import AIRequest, AIResponse, ProviderCapabilities


class AIProvider(Protocol):
    provider_id: str

    def capabilities(self, model: str) -> ProviderCapabilities:
        ...

    def generate(self, request: AIRequest) -> AIResponse[Any]:
        ...
