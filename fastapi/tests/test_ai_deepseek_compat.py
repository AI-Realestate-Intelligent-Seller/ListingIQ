"""DeepSeek behaviour pinned to the committed baseline (HEAD f6692e3).

Every expected value below was produced by running the committed code with the
same fake DeepSeek responses. Disposition, review, and suggestions must send the
same request and reach the same business result as before structured schemas
were introduced. No network: DeepSeek's HTTP call is replaced by a fake.
"""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
import requests

from app.ai import client as ai_client
from app.core.config import settings
from app.models import Conversation, Message
from app.sms import bobbie, service
from test_ai_review_evidence import LEGACY_AGENT  # noqa: E402  trusted test identity

GOOD = {
    'action': 'continue', 'intent': 'price_question', 'outcome': 'positive',
    'lead_status': 'interested', 'confidence': 0.8, 'conversation_stage': 'discovery',
    'next_step': 'answer_and_qualify', 'qualification_focus': 'timeline',
    'calendar': {'state': 'none', 'should_fetch_availability': False,
                 'requested_time_text': '', 'reason': 'no call yet'},
    'reason': 'owner asked about price',
}
CONVERSATION = SimpleNamespace(
    contact='+10000000000', name='Owner', property_address='1 Test St',
    lead_status='processing', lead_context=json.dumps({'available': True}),
)
HISTORY = [
    {'direction': 'outbound', 'text': 'Hi, Bobbie here about 1 Test St.'},
    {'direction': 'inbound', 'text': 'What would it sell for?'},
]


def _disposition(**changes):
    return json.dumps({**GOOD, **changes})


def _without(key):
    return json.dumps({name: value for name, value in GOOD.items() if name != key})


@pytest.fixture
def deepseek(monkeypatch):
    """Select DeepSeek and capture every request body it would receive."""
    monkeypatch.setitem(settings['ai'], 'provider', 'deepseek')
    monkeypatch.setitem(settings['ai'], 'model', 'deepseek-v4-flash')
    monkeypatch.setitem(settings['ai'], 'api_key', 'offline-test-key')
    monkeypatch.setitem(settings['ai'], 'base_url', 'https://api.deepseek.com')
    state = {'bodies': [], 'replies': []}

    class Reply:
        ok = True
        status_code = 200
        headers = {}

        def __init__(self, content):
            self._content = content

        def json(self):
            return {'model': 'deepseek-v4-flash', 'choices': [{
                'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': self._content},
            }]}

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url == 'https://api.deepseek.com/chat/completions'
        state['bodies'].append(json)
        replies = state['replies']
        return Reply(replies.pop(0) if len(replies) > 1 else replies[0])

    monkeypatch.setattr(requests, 'post', fake_post)
    return state


# (case, model output, action, lead_status, outcome, stage, next_step,
#  calendar state, should_fetch_availability, confidence, len(intent))
DISPOSITION_BASELINE = [
    ('valid', _disposition(), 'continue', 'interested', 'positive', 'discovery',
     'answer_and_qualify', 'none', False, 0.8, 14),
    ('reason_241', _disposition(reason='r' * 241), 'continue', 'interested', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 0.8, 14),
    ('intent_100', _disposition(intent='i' * 100), 'continue', 'interested', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 0.8, 80),
    ('location_discussion', _disposition(lead_status='location_discussion'), 'continue',
     'location_discussion', 'positive', 'discovery', 'answer_and_qualify', 'none', False, 0.8, 14),
    ('extra_key', json.dumps({**GOOD, 'notes': 'x'}), 'continue', 'interested', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 0.8, 14),
    ('fenced', '```json\n' + _disposition() + '\n```', 'continue', 'interested', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 0.8, 14),
    ('prose_wrapped', 'Here you go: ' + _disposition() + ' thanks', 'continue', 'interested',
     'positive', 'discovery', 'answer_and_qualify', 'none', False, 0.8, 14),
    ('confidence_str', _disposition(confidence='0.9'), 'continue', 'interested', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 0.9, 14),
    ('confidence_big', _disposition(confidence=7), 'continue', 'interested', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 1.0, 14),
    ('invalid_status', _disposition(lead_status='unclear'), 'continue', 'processing', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 0.8, 14),
    ('missing_calendar', _without('calendar'), 'continue', 'interested', 'positive',
     'discovery', 'answer_and_qualify', 'none', False, 0.8, 14),
    ('call_accepted_long_reason', _disposition(
        reason='z' * 300,
        calendar={'state': 'call_accepted', 'should_fetch_availability': True,
                  'requested_time_text': '', 'reason': 'c' * 250}),
     'continue', 'interested', 'positive', 'discovery', 'answer_and_qualify',
     'call_accepted', True, 0.8, 14),
    # The critical field: a missing or invalid action is never accepted.
    ('missing_action', _without('action'), 'continue', 'processing', 'neutral', 'discovery',
     'answer_and_qualify', 'none', False, 0, 7),
    ('invalid_action', _disposition(action='pause'), 'continue', 'processing', 'neutral',
     'discovery', 'answer_and_qualify', 'none', False, 0, 7),
    ('not_json', 'I think they want to sell.', 'continue', 'processing', 'neutral', 'discovery',
     'answer_and_qualify', 'none', False, 0, 7),
]


@pytest.mark.parametrize(
    'case,content,action,lead_status,outcome,stage,next_step,calendar_state,fetch,confidence,intent_len',
    DISPOSITION_BASELINE, ids=[row[0] for row in DISPOSITION_BASELINE])
def test_deepseek_disposition_matches_committed_baseline(
        deepseek, case, content, action, lead_status, outcome, stage, next_step,
        calendar_state, fetch, confidence, intent_len):
    deepseek['replies'] = [content]

    result = bobbie.analyze_conversation_disposition(CONVERSATION, HISTORY, agent_context=LEGACY_AGENT)

    assert result['action'] == action
    assert result['lead_status'] == lead_status
    assert result['outcome'] == outcome
    assert result['conversation_stage'] == stage
    assert result['next_step'] == next_step
    assert result['calendar']['state'] == calendar_state
    assert result['calendar']['should_fetch_availability'] is fetch
    assert result['confidence'] == confidence
    assert len(result['intent']) == intent_len
    assert len(result['reason']) <= 240
    fell_back = 'fallback' in result['reason'].lower()
    assert fell_back is (case in {'missing_action', 'invalid_action', 'not_json'})


def _review(**changes):
    return json.dumps({**{'valid': False, 'issues': ['too_pushy'],
                          'rewrite_instruction': 'soften', 'reason': 'pushy'}, **changes})


# (case, model output, valid, issue count, longest issue, rewrite length, reason prefix)
REVIEW_BASELINE = [
    ('valid_true', _review(valid=True, issues=[]), True, 0, 0, 6, 'pushy'),
    ('invalid', _review(), False, 1, 9, 6, 'pushy'),
    ('issues_8_long', _review(issues=['x' * 150] * 8), False, 6, 100, 6, 'pushy'),
    ('rewrite_500', _review(rewrite_instruction='w' * 500), False, 1, 9, 400, 'pushy'),
    ('extra_key', json.dumps({'valid': True, 'issues': [], 'rewrite_instruction': '',
                              'reason': 'ok', 'score': 9}), True, 0, 0, 0, 'ok'),
    ('fenced_true', '```json\n' + _review(valid=True, issues=[]) + '\n```', True, 0, 0, 6, 'pushy'),
    # A draft is approved only on an explicit boolean true.
    ('missing_valid', json.dumps({'issues': [], 'rewrite_instruction': '', 'reason': 'x'}),
     False, 0, 0, 0, 'x'),
    ('valid_string', _review(valid='true'), False, 1, 9, 6, 'pushy'),
]


@pytest.mark.parametrize('case,content,valid,issue_count,longest,rewrite_len,reason',
                         REVIEW_BASELINE, ids=[row[0] for row in REVIEW_BASELINE])
def test_deepseek_review_matches_committed_baseline(
        deepseek, case, content, valid, issue_count, longest, rewrite_len, reason):
    deepseek['replies'] = [content]

    result = bobbie.review_ai_reply(CONVERSATION, HISTORY, 'Draft SMS text.',
                                    {'next_step': 'answer_and_qualify'}, agent_context=LEGACY_AGENT)

    assert result['valid'] is valid
    assert len(result['issues']) == issue_count
    assert max([len(issue) for issue in result['issues']] or [0]) == longest
    assert len(result['rewrite_instruction']) == rewrite_len
    assert result['reason'].startswith(reason)


def test_deepseek_structured_requests_are_the_committed_request_bodies(deepseek):
    deepseek['replies'] = [_disposition()]
    bobbie.analyze_conversation_disposition(CONVERSATION, HISTORY, agent_context=LEGACY_AGENT)
    deepseek['replies'] = [_review(valid=True, issues=[])]
    bobbie.review_ai_reply(CONVERSATION, HISTORY, 'Draft.', {}, agent_context=LEGACY_AGENT)

    disposition, review = deepseek['bodies']
    assert {key: value for key, value in disposition.items() if key != 'messages'} == {
        'model': 'deepseek-v4-flash', 'stream': False, 'temperature': 0,
        'max_tokens': 240, 'thinking': {'type': 'disabled'},
    }
    assert [message['role'] for message in disposition['messages']] == ['system', 'user']
    assert {key: value for key, value in review.items() if key != 'messages'} == {
        'model': 'deepseek-v4-flash', 'stream': False, 'temperature': 0,
        # Raised from 160: the hard-block/advisory review JSON is longer.
        'max_tokens': 400, 'thinking': {'type': 'disabled'},
    }


def test_deepseek_never_receives_schema_or_json_mode_from_the_shared_client(deepseek):
    deepseek['replies'] = [json.dumps({'suggestions': ['One', 'Two']})]

    message = ai_client.completion(
        [{'role': 'user', 'content': 'Reply with JSON only.'}],
        temperature=0.7, output_schema={'type': 'object'}, feature='test.structured',
    )

    assert message['content'] == json.dumps({'suggestions': ['One', 'Two']})
    body = deepseek['bodies'][0]
    assert 'response_format' not in body
    assert body['temperature'] == 0.7


def test_deepseek_keeps_a_caller_requested_json_mode(deepseek):
    deepseek['replies'] = ['{"action":"none"}']

    ai_client.completion([{'role': 'user', 'content': 'JSON please'}],
                         temperature=0, max_tokens=180,
                         response_format={'type': 'json_object'})

    assert deepseek['bodies'][0]['response_format'] == {'type': 'json_object'}


def test_deepseek_unparseable_review_withholds_the_draft(deepseek):
    # Formerly kept the draft as valid ("Reviewer unavailable"); now it fails closed.
    deepseek['replies'] = ['Looks fine to me.']
    with pytest.raises(bobbie.ReviewUnavailableError):
        bobbie.review_ai_reply(CONVERSATION, HISTORY, 'Draft SMS text.',
                               {'next_step': 'answer_and_qualify'}, agent_context=LEGACY_AGENT)


def test_deepseek_review_rejection_still_runs_the_repair(deepseek, monkeypatch):
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)
    # Over-long issues used to make the strict validator drop the review and
    # keep the rejected draft. The committed behaviour repairs it.
    deepseek['replies'] = [
        _review(issues=['x' * 150] * 8, rewrite_instruction='Answer the price question.'),
        'Fair question. Could I stop by to give you a real number?',
        _review(valid=True, issues=[]),
    ]

    result = bobbie.review_and_repair_ai_reply(
        CONVERSATION, HISTORY, 'Rejected draft.', {'next_step': 'answer_and_qualify'}, agent_context=LEGACY_AGENT)

    assert result == 'Fair question. Could I stop by to give you a real number?'
    # review + regeneration + review of the regeneration
    assert len(deepseek['bodies']) == 3
    assert 'tools' in deepseek['bodies'][1]
    assert 'Fair question. Could I stop by' in json.dumps(deepseek['bodies'][2])


@pytest.mark.parametrize('draft', ['', 'Call me at 312 555 0199 anytime.'], ids=['empty_draft_retry', 'policy_repair'])
def test_deepseek_follow_up_after_tool_calls_is_the_committed_request(deepseek, monkeypatch, draft):
    monkeypatch.setattr(bobbie.knowledge, 'needs_agent_knowledge', lambda _text: False)
    monkeypatch.setattr(bobbie.knowledge, 'search_bobbie_knowledge',
                        lambda query, limit: {'query': query, 'results': []})
    tool_calls = [
        {'id': 'c1', 'type': 'function', 'function': {'name': 'get_lead_details', 'arguments': '{}'}},
        {'id': 'c2', 'type': 'function',
         'function': {'name': 'search_agent_knowledge', 'arguments': '{"query":"bobbie"}'}},
    ]
    replies = iter([
        {'role': 'assistant', 'content': None, 'tool_calls': tool_calls},
        {'role': 'assistant', 'content': draft},
        {'role': 'assistant', 'content': 'Short answer. What timing works for you?'},
    ])

    class Reply:
        ok = True
        status_code = 200
        headers = {}

        def __init__(self, message):
            self._message = message

        def json(self):
            return {'model': 'deepseek-v4-flash',
                    'choices': [{'finish_reason': 'stop', 'message': self._message}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        deepseek['bodies'].append(json)
        return Reply(next(replies))

    monkeypatch.setattr(requests, 'post', fake_post)

    reply = bobbie.generate_ai_reply(CONVERSATION, HISTORY, agent_context=LEGACY_AGENT)

    first, after_tools, follow = deepseek['bodies']
    assert reply == 'Short answer. What timing works for you?'
    # The committed follow-up request: no tools, no tool_choice, its own sampling.
    assert {key: value for key, value in follow.items() if key != 'messages'} == {
        'model': 'deepseek-v4-flash', 'stream': False, 'temperature': 0.1,
        # Raised from 80: a token cap must not act as a hidden SMS length limit.
        'max_tokens': 400, 'thinking': {'type': 'disabled'},
    }
    assert follow['messages'][:-1] == after_tools['messages']
    assert follow['messages'][-1]['role'] == 'system'
    assert after_tools['messages'][-3]['tool_calls'] == tool_calls


def test_deepseek_meeting_acceptance_with_long_reason_hands_off_to_a_person(
        deepseek, make_user, session, monkeypatch):
    broker = make_user('handoff-broker@linchpinglobal.net', role='broker')
    conversation = Conversation(
        contact='+13125550123', user_id=broker.id, name='Owner',
        property_address='1 Test St', ai_enabled=True, handled_by='bobbie',
        lead_status='interested',
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    session.add_all([
        Message(conversation_id=conversation.id, direction='outbound', text='Could I stop by?',
                status='delivered', event_type='ai.reply', created_at=datetime.utcnow()),
        Message(conversation_id=conversation.id, direction='inbound', text='Sure, come by Saturday morning.',
                status='received', event_type='message.received', created_at=datetime.utcnow()),
    ])
    session.commit()
    deepseek['replies'] = [_disposition(
        reason='Owner accepted the visit. ' * 20,
        calendar={'state': 'call_accepted', 'should_fetch_availability': True,
                  'requested_time_text': '', 'reason': 'accepted ' * 40})]
    sent = []
    monkeypatch.setattr(service, 'send_and_store_message',
                        lambda _session, _conversation, text, event_type, *rest:
                        sent.append((text, event_type)) or Message(id=1))

    service.process_ai_reply(conversation.id, 'Sure, come by Saturday morning.')

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert updated.handled_by == 'broker'
    assert updated.ai_enabled is False
    assert updated.lead_status == 'location_discussion'
    assert len(sent) == 1
    assert 'Our team will reach out to coordinate' in sent[0][0] and 'Saturday morning' in sent[0][0]
    assert len(deepseek['bodies']) == 1, 'hand-off happens before any reply is drafted'
