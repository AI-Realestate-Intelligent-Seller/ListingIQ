"""Provider/model configuration, validation, and switching by restart.

Settings are computed once when the process starts, so each "restart" below
rebuilds them with load_config() from a fresh set of environment variables.
Provider HTTP is faked; nothing leaves the machine.
"""

import pytest
import requests

from app import main
from app.ai import client as ai_client
from app.ai.configuration import AIConfigurationError, configuration_error, validate_ai_settings
from app.core import config as core_config
from app.core.config import settings

AI_VARIABLES = (
    'AI_PROVIDER', 'AI_MODEL', 'DEEPSEEK_MODEL', 'ANTHROPIC_MODEL',
    'ANTHROPIC_SIMPLE_MAX_OUTPUT_TOKENS', 'ANTHROPIC_COMPLEX_MAX_OUTPUT_TOKENS',
)


def restart(monkeypatch, **env):
    """Return the AI settings a fresh process would build from these variables."""
    for name in AI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return core_config.load_config()['ai']


@pytest.mark.parametrize('env,provider,model', [
    ({}, 'deepseek', 'deepseek-v4-flash'),
    ({'AI_MODEL': 'deepseek-chat'}, 'deepseek', 'deepseek-chat'),
    ({'DEEPSEEK_MODEL': 'deepseek-reasoner', 'AI_MODEL': 'deepseek-chat'}, 'deepseek', 'deepseek-reasoner'),
    ({'AI_PROVIDER': 'anthropic', 'ANTHROPIC_MODEL': 'claude-haiku-5-5',
      'AI_MODEL': 'deepseek-v4-flash'}, 'anthropic', 'claude-haiku-5-5'),
    ({'AI_PROVIDER': 'Anthropic', 'AI_MODEL': 'claude-sonnet-5-5'}, 'anthropic', 'claude-sonnet-5-5'),
    ({'AI_PROVIDER': 'deepseek', 'ANTHROPIC_MODEL': 'claude-haiku-5-5',
      'AI_MODEL': 'deepseek-v4-flash'}, 'deepseek', 'deepseek-v4-flash'),
], ids=['defaults', 'legacy_ai_model', 'provider_model_wins', 'claude_with_deepseek_ai_model',
        'claude_via_legacy_ai_model', 'rollback_keeps_claude_model_aside'])
def test_valid_configurations_resolve_the_expected_model(monkeypatch, env, provider, model):
    ai = restart(monkeypatch, **env)

    assert (ai['provider'], ai['model']) == (provider, model)
    assert configuration_error(ai) is None


@pytest.mark.parametrize('env,message', [
    ({'AI_PROVIDER': 'anthropic', 'AI_MODEL': 'deepseek-v4-flash'}, 'set ANTHROPIC_MODEL'),
    ({'AI_PROVIDER': 'anthropic'}, 'set ANTHROPIC_MODEL'),
    ({'AI_PROVIDER': 'anthropic', 'ANTHROPIC_MODEL': 'claude-opus-5-5'}, 'claude-haiku-5-5, claude-sonnet-5-5'),
    ({'AI_PROVIDER': 'deepseek', 'AI_MODEL': 'claude-haiku-5-5'}, 'set DEEPSEEK_MODEL'),
    ({'AI_PROVIDER': 'deepseek', 'DEEPSEEK_MODEL': 'gpt-5'}, 'cannot use model'),
    ({'AI_PROVIDER': 'openai'}, 'not implemented yet'),
    ({'AI_PROVIDER': 'gemini'}, 'Unsupported AI_PROVIDER'),
    ({'AI_PROVIDER': 'anthropic', 'ANTHROPIC_MODEL': 'claude-haiku-5-5',
      'ANTHROPIC_SIMPLE_MAX_OUTPUT_TOKENS': '0'}, 'must be a positive integer'),
], ids=['claude_with_deepseek_model', 'claude_without_model', 'unsupported_claude_model',
        'deepseek_with_claude_model', 'deepseek_with_openai_model', 'openai_reserved',
        'unknown_provider', 'zero_output_budget'])
def test_mismatched_configurations_fail_clearly(monkeypatch, env, message):
    ai = restart(monkeypatch, **env)

    with pytest.raises(AIConfigurationError, match=message):
        validate_ai_settings(ai)


def test_startup_refuses_an_invalid_pair_and_accepts_a_valid_one(monkeypatch):
    monkeypatch.setitem(settings, 'ai', restart(monkeypatch, AI_PROVIDER='anthropic',
                                                AI_MODEL='deepseek-v4-flash'))
    with pytest.raises(AIConfigurationError, match='set ANTHROPIC_MODEL'):
        main._validate_ai_configuration()

    monkeypatch.setitem(settings, 'ai', restart(monkeypatch, AI_PROVIDER='anthropic',
                                                ANTHROPIC_MODEL='claude-haiku-5-5'))
    main._validate_ai_configuration()


def test_misconfiguration_never_reaches_a_provider(monkeypatch):
    ai = restart(monkeypatch, AI_PROVIDER='anthropic', AI_MODEL='deepseek-v4-flash')
    ai['anthropic_api_key'] = 'offline-key'
    monkeypatch.setitem(settings, 'ai', ai)
    monkeypatch.setattr(requests, 'post', lambda *a, **k: pytest.fail('no request may be sent'))

    assert ai_client.is_configured() is False
    with pytest.raises(ai_client.AiUnavailableError, match='set ANTHROPIC_MODEL'):
        ai_client.completion([{'role': 'user', 'content': 'hello'}])


def test_switching_deepseek_to_claude_and_back_needs_only_configuration(monkeypatch):
    seen = []

    class Reply:
        ok = True
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append((url, json['model']))
        if url.endswith('/v1/messages'):
            return Reply({'type': 'message', 'model': json['model'], 'stop_reason': 'end_turn',
                          'content': [{'type': 'text', 'text': 'from claude'}], 'usage': {}})
        return Reply({'model': json['model'], 'choices': [{
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'from deepseek'}}]})

    monkeypatch.setattr(requests, 'post', fake_post)
    shared = {'AI_MODEL': 'deepseek-v4-flash', 'ANTHROPIC_MODEL': 'claude-haiku-5-5'}
    answers = []
    for provider in ('deepseek', 'anthropic', 'deepseek'):
        ai = restart(monkeypatch, AI_PROVIDER=provider, **shared)
        ai.update(api_key='offline-deepseek', anthropic_api_key='offline-anthropic',
                  base_url='https://api.deepseek.com', anthropic_base_url='https://api.anthropic.com')
        monkeypatch.setitem(settings, 'ai', ai)
        main._validate_ai_configuration()
        answers.append(ai_client.completion([{'role': 'user', 'content': 'hello'}])['content'])

    assert answers == ['from deepseek', 'from claude', 'from deepseek']
    assert seen == [
        ('https://api.deepseek.com/chat/completions', 'deepseek-v4-flash'),
        ('https://api.anthropic.com/v1/messages', 'claude-haiku-5-5'),
        ('https://api.deepseek.com/chat/completions', 'deepseek-v4-flash'),
    ]
