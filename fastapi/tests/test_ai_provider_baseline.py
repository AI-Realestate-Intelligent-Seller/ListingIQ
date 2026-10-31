"""Offline baseline contracts for the existing DeepSeek-backed AI features.

These tests intentionally exercise the current production functions through
mocked completion responses.  They are the behavioral baseline for the future
provider adapters; no provider or SMS network request is permitted here.
"""

import json
from types import SimpleNamespace

import pytest

from app.ai import client as ai_client
from app.leads import reason_suggest
from app.sms import bobbie, reply_suggest, service
from app.sms.deepseek import AiUnavailableError
from test_ai_review_evidence import LEGACY_AGENT  # noqa: E402  trusted test identity


def test_ai_features_bind_to_the_provider_neutral_client():
    assert bobbie.completion is ai_client.completion
    assert reply_suggest.ai_client is ai_client
    assert reason_suggest.ai_client is ai_client


@pytest.fixture
def conversation():
    return SimpleNamespace(
        id=41,
        contact='+13125550123',
        name='Jordan Lee',
        property_address='123 Example Ave, Austin, TX 78701',
        lead_status='processing',
        lead_context=json.dumps({
            'available': True,
            'outreach_reason': 'The listing is no longer active.',
            'property_details': {'Bedrooms': 3, 'Bathrooms': 2},
            'phone_number_source': {'available': False},
        }),
    )


@pytest.fixture
def history():
    return [
        {'direction': 'outbound', 'text': 'Would you consider selling the property?'},
        {'direction': 'inbound', 'text': 'Maybe, if the timing and price make sense.'},
    ]


def test_disposition_normalizes_a_valid_structured_response(monkeypatch, conversation, history):
    seen = {}

    def fake_completion(messages, **options):
        seen['messages'] = messages
        seen['options'] = options
        return {'content': json.dumps({
            'action': 'continue',
            'intent': 'conditional_interest',
            'outcome': 'positive',
            'lead_status': 'interested',
            'confidence': 1.7,
            'conversation_stage': 'qualified_for_call',
            'next_step': 'answer_and_qualify',
            'qualification_focus': 'timing',
            'calendar': {
                'state': 'none',
                'should_fetch_availability': True,
                'requested_time_text': '',
                'reason': 'No meeting requested.',
            },
            'reason': 'The owner left a conditional opening.',
        })}

    monkeypatch.setattr(bobbie, 'completion', fake_completion)

    result = bobbie.analyze_conversation_disposition(conversation, history, agent_context=LEGACY_AGENT)

    assert result['action'] == 'continue'
    assert result['lead_status'] == 'interested'
    assert result['conversation_stage'] == 'qualified_for_call'
    # Qualified conversations are advanced to the meeting invitation contract.
    assert result['next_step'] == 'offer_call'
    assert result['confidence'] == 1.0
    # The server derives this flag from the validated calendar state.
    assert result['calendar']['should_fetch_availability'] is False
    assert seen['messages'][0]['role'] == 'system'
    assert seen['options']['temperature'] == 0
    assert seen['options']['max_tokens'] == 240
    assert seen['options']['feature'] == 'sms.disposition'
    assert seen['options']['output_schema'] == bobbie.DISPOSITION_SCHEMA


def test_disposition_uses_the_deterministic_fallback_for_malformed_json(
        monkeypatch, conversation, history):
    monkeypatch.setattr(bobbie, 'completion', lambda *_args, **_kwargs: {'content': 'not json'})

    result = bobbie.analyze_conversation_disposition(conversation, history, agent_context=LEGACY_AGENT)

    assert result['action'] == 'continue'
    assert result['calendar']['state'] == 'none'
    assert result['calendar']['should_fetch_availability'] is False
    assert result['confidence'] == 0
    assert 'fallback' in result['reason'].lower()


def test_explicit_stop_bypasses_every_llm_stage(monkeypatch, conversation):
    class Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return conversation

    class Session:
        def query(self, *_args, **_kwargs):
            return Query()

        def close(self):
            pass

    effects = []
    conversation.user_id = 7
    conversation.ai_enabled = True
    conversation.handled_by = 'bobbie'
    conversation.dnc_alert = False
    monkeypatch.setattr(service, 'SessionLocal', Session)
    monkeypatch.setattr(
        service.bobbie,
        'analyze_conversation_disposition',
        lambda *_args, **_kwargs: pytest.fail('STOP must bypass the LLM'),
    )
    monkeypatch.setattr(service, 'hand_to_broker',
                        lambda _session, _conversation: effects.append('handoff'))
    monkeypatch.setattr(service, 'mark_lead_completed',
                        lambda _session, _conversation, **_values: effects.append('completed'))
    monkeypatch.setattr(service, '_log_status_change', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        'send_and_store_message',
        lambda _session, _conversation, text, event_type: effects.append((event_type, text)),
    )

    service.process_ai_reply(conversation.id, 'STOP')

    assert effects[0:2] == ['handoff', 'completed']
    assert effects[2][0] == 'ai.reply'
    assert effects[2][1].startswith('Understood')


def test_reply_generation_dispatches_tools_and_returns_the_followup_completion(
        monkeypatch, conversation, history):
    calls = []
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)

    def fake_completion(messages, **options):
        calls.append((messages, options))
        if len(calls) == 1:
            return {
                'content': '',
                'tool_calls': [{
                    'id': 'tool-call-1',
                    'type': 'function',
                    'function': {'name': 'get_lead_details', 'arguments': '{}'},
                }],
            }
        return {'content': 'That makes sense. What timing would work best for you?'}

    monkeypatch.setattr(bobbie, 'completion', fake_completion)

    result = bobbie.generate_ai_reply(conversation, history, agent_context=LEGACY_AGENT)

    assert result
    assert len(calls) == 2
    assert calls[0][1]['tool_choice'] == 'auto'
    continuation = calls[1][0]
    assert any(message.get('role') == 'tool'
               and message.get('tool_call_id') == 'tool-call-1'
               for message in continuation)
    assert any(message.get('role') == 'system'
               and message.get('content', '').startswith('Approved runtime context')
               for message in calls[0][0])


def test_reply_generation_executes_multiple_tools_before_continuing(
        monkeypatch, conversation, history):
    calls = []
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)
    monkeypatch.setattr(
        bobbie.knowledge,
        'search_bobbie_knowledge',
        lambda query, limit: {'query': query, 'limit': limit, 'results': []},
    )

    def fake_completion(messages, **options):
        calls.append((messages, options))
        if len(calls) == 1:
            return {
                'content': '',
                'tool_calls': [
                    {
                        'id': 'tool-call-lead',
                        'type': 'function',
                        'function': {'name': 'get_lead_details', 'arguments': '{}'},
                    },
                    {
                        'id': 'tool-call-knowledge',
                        'type': 'function',
                        'function': {
                            'name': 'search_agent_knowledge',
                            'arguments': '{"query":"commission"}',
                        },
                    },
                ],
            }
        return {'content': 'That makes sense. What timing would work best for you?'}

    monkeypatch.setattr(bobbie, 'completion', fake_completion)

    result = bobbie.generate_ai_reply(conversation, history, agent_context=LEGACY_AGENT)

    assert result
    assert len(calls) == 2
    tool_results = [message for message in calls[1][0] if message.get('role') == 'tool']
    assert [message.get('tool_call_id') for message in tool_results] == [
        'tool-call-lead',
        'tool-call-knowledge',
    ]


def test_empty_reply_is_retried_once_with_the_existing_contract(
        monkeypatch, conversation, history):
    replies = iter([
        {'content': ''},
        {'content': 'What timing would work best for you?'},
    ])
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)
    monkeypatch.setattr(bobbie, 'completion', lambda *_args, **_kwargs: next(replies))

    assert bobbie.generate_ai_reply(conversation, history, agent_context=LEGACY_AGENT)


def test_reply_policy_violation_runs_the_existing_repair_call(
        monkeypatch, conversation, history):
    replies = iter([
        {'content': 'I charge exactly 3% commission.'},
        {'content': 'I can explain the options after seeing the property.'},
    ])
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)
    monkeypatch.setattr(bobbie, 'completion', lambda *_args, **_kwargs: next(replies))

    result = bobbie.generate_ai_reply(conversation, history, agent_context=LEGACY_AGENT)

    assert '3%' not in result
    assert result


def test_reply_generation_propagates_provider_unavailability(
        monkeypatch, conversation, history):
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)

    def unavailable(*_args, **_kwargs):
        raise AiUnavailableError('provider unavailable')

    monkeypatch.setattr(bobbie, 'completion', unavailable)

    with pytest.raises(AiUnavailableError):
        bobbie.generate_ai_reply(conversation, history, agent_context=LEGACY_AGENT)


def test_review_accepts_valid_json_and_keeps_a_policy_checked_draft(
        monkeypatch, conversation, history):
    monkeypatch.setattr(bobbie, 'completion', lambda *_args, **_kwargs: {
        'content': json.dumps({
            'valid': True,
            'issues': [],
            'rewrite_instruction': '',
            'reason': 'The response answers the owner.',
        }),
    })

    result = bobbie.review_ai_reply(
        conversation, history, 'What timing would work best?',
        {'next_step': 'answer_and_qualify'},
    agent_context=LEGACY_AGENT)

    assert result['valid'] is True
    assert result['issues'] == []


@pytest.mark.parametrize('content', ['', 'not json', '{"valid":'])
def test_malformed_review_response_withholds_the_draft(
        monkeypatch, conversation, history, content):
    # Fail closed: an unreadable verdict is never treated as approval (release blocker fix).
    monkeypatch.setattr(bobbie, 'completion', lambda *_args, **_kwargs: {'content': content})

    with pytest.raises(bobbie.ReviewUnavailableError):
        bobbie.review_ai_reply(
            conversation, history, 'What timing would work best?',
            {'next_step': 'answer_and_qualify'},
            agent_context=LEGACY_AGENT)
    with pytest.raises(bobbie.ReviewUnavailableError):
        bobbie.review_and_repair_ai_reply(
            conversation, history, 'What timing would work best?',
            {'next_step': 'answer_and_qualify'},
            agent_context=LEGACY_AGENT)


REJECTION = {
    'valid': False,
    'issues': ['did_not_answer_owner'],
    'rewrite_instruction': 'Answer the condition first.',
    'reason': 'The draft skipped the owner condition.',
}


def test_rejected_review_invokes_one_fresh_generation_that_is_reviewed_again(
        monkeypatch, conversation, history):
    verdicts = [REJECTION, {'valid': True, 'issues': [], 'rewrite_instruction': '', 'reason': 'ok'}]
    reviewed = []

    def fake_review(_conversation, _history, draft, *_args, **_kwargs):
        reviewed.append(draft)
        return verdicts.pop(0)

    monkeypatch.setattr(bobbie, 'review_ai_reply', fake_review)
    seen = []

    def fake_generate(*args, **kwargs):
        seen.append(args)
        return 'I understand the condition. What timing would work best?'

    monkeypatch.setattr(bobbie, 'generate_ai_reply', fake_generate)

    result = bobbie.review_and_repair_ai_reply(
        conversation, history, 'Could I stop by?',
        {'next_step': 'answer_and_qualify'},
        agent_context=LEGACY_AGENT)

    assert result == 'I understand the condition. What timing would work best?'
    assert len(seen) == 1 and 'Independent reviewer found blocking issues' in seen[0][3]
    assert reviewed == ['Could I stop by?', result]


def test_persistent_rejection_is_bounded_and_returns_no_draft(monkeypatch, conversation, history):
    monkeypatch.setattr(bobbie, 'review_ai_reply', lambda *_args, **_kwargs: dict(REJECTION))
    generated = []
    monkeypatch.setattr(bobbie, 'generate_ai_reply',
                        lambda *args, **kwargs: generated.append(1) or f'Attempt {len(generated)}.')

    with pytest.raises(bobbie.ReviewRejectedError):
        bobbie.review_and_repair_ai_reply(
            conversation, history, 'Could I stop by?', {'next_step': 'answer_and_qualify'},
            agent_context=LEGACY_AGENT)
    assert len(generated) == bobbie.MAX_REVIEW_REGENERATIONS


def test_followup_generation_falls_back_without_failing_delivery_work(
        monkeypatch, conversation, history):
    def unavailable(*_args, **_kwargs):
        raise AiUnavailableError('provider unavailable')

    monkeypatch.setattr(bobbie, 'completion', unavailable)

    result = bobbie.generate_no_response_followup(conversation, history, attempt=1, maximum=3, agent_context=LEGACY_AGENT)

    assert conversation.property_address in result
    assert len(result) <= 240


def test_dormant_calendar_resolver_fails_closed_after_two_malformed_responses(
        monkeypatch, conversation, history):
    attempts = []

    def malformed(*_args, **options):
        attempts.append(options)
        return {'content': 'not json'}

    monkeypatch.setattr(bobbie, 'completion', malformed)

    result = bobbie.resolve_calendar_action(
        conversation,
        history,
        {'current_time': '2026-10-19T09:00:00-05:00',
         'timezone': 'America/Chicago', 'slots': []},
        {'state': 'time_proposed', 'requested_time_text': 'Tuesday morning'},
    )

    assert len(attempts) == 2
    assert result['action'] == 'resolution_failed'
    assert result['start_at'] is None


def test_campaign_reason_parser_normalizes_deduplicates_and_bounds_output():
    overlong = 'x' * (reason_suggest.MAX_REASON_CHARS + 20)
    parsed = reason_suggest._parse(json.dumps({
        'suggestions': ['  One useful reason.  ', 'One useful reason.', overlong, 7],
    }))

    assert parsed[0] == 'One useful reason.'
    assert len(parsed) == 2
    assert len(parsed[1]) == reason_suggest.MAX_REASON_CHARS


def test_human_reply_parser_requires_the_documented_object_schema():
    assert reply_suggest._parse('["not the documented object"]') == []
    parsed = reply_suggest._parse(json.dumps({
        'suggestions': [' First option. ', 'First option.', 'Second option.'],
    }))
    assert parsed == ['First option.', 'Second option.']
