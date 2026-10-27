"""Backward-compatible import path for the provider-neutral AI client."""

import requests

from ..ai.client import AiUnavailableError, completion, is_configured
from ..ai.service import ai_service
from ..core.config import settings

__all__ = [
    'AiUnavailableError',
    'ai_service',
    'completion',
    'is_configured',
    'requests',
    'settings',
]
