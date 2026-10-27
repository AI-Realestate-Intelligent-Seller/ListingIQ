"""Provider/model compatibility checks for the global AI configuration.

Configuration is read once at process start, so a provider switch is an
environment change plus a restart. These checks run at startup and before every
request, so a mismatched pair fails with a clear message instead of reaching a
provider and quietly falling back to templates.
"""

from collections.abc import Mapping
from typing import Any

from .providers.anthropic import SUPPORTED_MODELS as ANTHROPIC_MODELS

IMPLEMENTED_PROVIDERS = ('deepseek', 'anthropic')
# DeepSeek is reached through an OpenAI-compatible endpoint (AI_BASE_URL), so any
# model name is allowed except ones that clearly belong to another provider.
_FOREIGN_MODEL_PREFIXES = ('claude', 'anthropic.', 'gpt-', 'chatgpt-')


class AIConfigurationError(RuntimeError):
    """The configured AI provider and model cannot work together."""


def configuration_error(ai: Mapping[str, Any]) -> str | None:
    provider = ai.get('provider')
    model = ai.get('model') or ''
    if provider == 'openai':
        return 'AI_PROVIDER=openai is reserved for a future adapter and is not implemented yet'
    if provider not in IMPLEMENTED_PROVIDERS:
        return (f'Unsupported AI_PROVIDER={provider!r}; '
                f'use one of: {", ".join(IMPLEMENTED_PROVIDERS)}')
    if provider == 'deepseek':
        if not model:
            return 'No DeepSeek model configured; set DEEPSEEK_MODEL (or AI_MODEL)'
        if model.lower().startswith(_FOREIGN_MODEL_PREFIXES):
            return (f'AI_PROVIDER=deepseek cannot use model {model!r}; '
                    'set DEEPSEEK_MODEL (or AI_MODEL) to a DeepSeek model')
        return None
    if model not in ANTHROPIC_MODELS:
        return (f'AI_PROVIDER=anthropic cannot use model {model!r}; '
                f'set ANTHROPIC_MODEL to one of: {", ".join(ANTHROPIC_MODELS)}')
    for key in ('anthropic_simple_max_output_tokens', 'anthropic_complex_max_output_tokens'):
        if not isinstance(ai.get(key), int) or ai[key] <= 0:
            return f'{key.upper()} must be a positive integer'
    return None


def validate_ai_settings(ai: Mapping[str, Any]) -> None:
    error = configuration_error(ai)
    if error:
        raise AIConfigurationError(error)
