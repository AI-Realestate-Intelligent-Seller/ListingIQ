"""Provider-independent AI request, response, and dispatch contracts."""

from .models import (
    AIMessage,
    AIRequest,
    AIResponse,
    AIToolCall,
    AIToolDefinition,
    GenerationOptions,
    ProviderCapabilities,
    Role,
    StructuredOutputMode,
    TokenUsage,
)
from .service import AIService, ai_service

__all__ = [
    'AIMessage', 'AIRequest', 'AIResponse', 'AIService', 'AIToolCall',
    'AIToolDefinition', 'GenerationOptions', 'ProviderCapabilities', 'Role',
    'StructuredOutputMode', 'TokenUsage', 'ai_service',
]
