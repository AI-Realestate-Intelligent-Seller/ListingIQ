"""Generation fixes measured on the 27 Oct 2026 live baseline (manual conversation).

Live baseline (before): the owner asked "Who is Bobbie, and how long has she
been with RE/MAX?" after Bobbie had already asked "What price were you hoping
to get for it?". Haiku answered well but re-asked the price question, the
repetition check scored it 0.667 (< 0.72) and the reviewer accepted it.
Provider HTTP is faked; these tests pin the pipeline, not live model wording.
"""

import json

import pytest

from app.core.config import settings
from app.sms import bobbie
from app.sms.policy import asked_questions, find_reply_policy_violations, premature_meeting_question
from test_ai_review_evidence import (
    LEGACY_AGENT,  # noqa: F401  (offline is a fixture)
    LIVE_KNOWLEDGE, PROVIDERS, VALID, _disposition, _generator_knowledge, _review_payload, offline, use,
)
from test_bio_question_fallback import HISTORY, QUESTION, _run

LIVE_BEFORE_DRAFT = ("I'm Bobbie Fisher, and I'm with Real Broker LLC now, not RE/MAX. My exact tenure with RE/MAX "
                     "isn't something I can confirm here. What price were you hoping to get for 1 Test St?")
NO_REPEAT_REPAIR = ("I'm Bobbie Fisher, and I'm with Real Broker LLC now, not RE/MAX. My exact tenure there isn't "
                    'confirmed, so I won’t guess. Are you hoping to sell soon?')
VISIT_DRAFT = ("I'm with Real Broker LLC now; my RE/MAX Premier work was earlier. Would you be open to me stopping "
               'by 1 Test St to take a look?')
BIO_DISPOSITION = _disposition(intent='asks_about_agent_background', lead_status='want_more_info',
                               qualification_focus='price')
PRIOR = [{'direction': d, 'text': t} for d, _event, t in HISTORY]


def _with_question(question):
    return PRIOR + [{'direction': 'inbound', 'text': question}]


def test_live_before_draft_now_counts_as_a_repeated_price_question():
    history = _with_question(QUESTION)
    assert 'repeated_question' in find_reply_policy_violations(LIVE_BEFORE_DRAFT, history=history)
    assert 'repeated_question' not in find_reply_policy_violations(NO_REPEAT_REPAIR, history=history)


def test_asked_questions_lists_every_earlier_bobbie_question():
    assert asked_questions(PRIOR) == ['Would you be open to a brief conversation?',
                                      'What price were you hoping to get for it?']


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_generation_is_told_which_questions_were_already_asked(
        offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)

    sent = _run(offline, monkeypatch, make_user, session, [BIO_DISPOSITION, NO_REPEAT_REPAIR, VALID])

    assert sent == [(NO_REPEAT_REPAIR, 'ai.reply')] and len(offline['bodies']) == 3
    generation = json.dumps(offline['bodies'][1])
    assert 'Questions already asked in this conversation (never ask these again' in generation
    assert 'What price were you hoping to get for it?' in generation


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_repeated_price_question_is_repaired_before_review(offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)

    sent = _run(offline, monkeypatch, make_user, session, [BIO_DISPOSITION, LIVE_BEFORE_DRAFT, NO_REPEAT_REPAIR, VALID])

    assert sent == [(NO_REPEAT_REPAIR, 'ai.reply')]
    repair = json.dumps(offline['bodies'][2])
    assert 'Fix these violations: repeated_question' in repair
    assert 'Do not ask any question the agent already asked in this conversation' in repair
    # form-only violation, so the blocked draft's supported facts are shown back for reuse
    assert 'the blocked draft was:' in repair
    assert _review_payload(offline['bodies'][3])['proposed_agent_reply'] == NO_REPEAT_REPAIR


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_premature_visit_invitation_is_repaired_for_a_biography_question(
        offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)

    sent = _run(offline, monkeypatch, make_user, session, [BIO_DISPOSITION, VISIT_DRAFT, NO_REPEAT_REPAIR, VALID])

    assert sent == [(NO_REPEAT_REPAIR, 'ai.reply')]
    repair = json.dumps(offline['bodies'][2])
    assert 'premature_meeting_request' in repair
    assert 'Do not mention, offer or ask about a visit, stop-by, call or meeting in this reply' in repair


@pytest.mark.parametrize('next_step,latest,expected', [
    ('answer_and_qualify', QUESTION, True),
    ('answer_only', 'Is Real Broker LLC the same as RE/MAX Premier?', True),
    ('offer_call', QUESTION, False),
    ('answer_and_qualify', 'What would it sell for?', False),  # prompt-sanctioned: value needs a look
    ('answer_and_qualify', 'What commission do you charge?', False),
])
def test_meeting_guard_only_applies_before_a_meeting_is_appropriate(next_step, latest, expected):
    assert bool(premature_meeting_question(VISIT_DRAFT, next_step, latest)) is expected


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_repeated_credentials_answer_is_not_resent_verbatim(offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)
    from app.models import Message
    answer = "I'm with Real Broker LLC now; my RE/MAX Premier work was earlier. Are you hoping to sell soon?"
    # The owner asks the credentials question again after Bobbie already answered it.
    monkeypatch.setattr('test_bio_question_fallback.HISTORY', HISTORY + [
        ('inbound', 'message.received', 'Who is Bobbie?'), ('outbound', 'ai.reply', answer)])
    different = ("I'm Bobbie Fisher with Real Broker LLC, and I don't have a verified RE/MAX tenure to share. "
                 'What matters most to you in a sale?')

    sent = _run(offline, monkeypatch, make_user, session, [BIO_DISPOSITION, answer, different, VALID])

    assert sent == [(different, 'ai.reply')]
    assert 'repeated_reply' in json.dumps(offline['bodies'][2])
    assert Message  # imported to keep the fixture's models loaded


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_approved_facts_reach_generator_and_reviewer_identically(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    from test_ai_review_evidence import LEGACY_AGENT_REAL_BROKER  # trusted account brokerage
    offline['knowledge'] = LIVE_KNOWLEDGE
    offline['replies'] = [NO_REPEAT_REPAIR, VALID]
    from test_ai_review_evidence import LIVE_CONVERSATION
    evidence = []
    draft = bobbie.generate_ai_reply(LIVE_CONVERSATION, _with_question(QUESTION), evidence_out=evidence,
                                     agent_context=LEGACY_AGENT_REAL_BROKER)
    bobbie.review_ai_reply(LIVE_CONVERSATION, _with_question(QUESTION), draft, {}, knowledge_evidence=evidence,
                           agent_context=LEGACY_AGENT_REAL_BROKER)

    used = _generator_knowledge(offline['bodies'][0])
    assert used['approved_facts']['current_brokerage'] == 'Real Broker LLC'
    assert 'overrides document_status' in used['approved_facts']['authority']
    assert 'Exact tenure, titles and years of experience are not approved facts' in used['approved_facts']['authority']
    payload = _review_payload(offline['bodies'][1])
    assert payload['approved_knowledge_evidence'] == [used]
    assert 'approved_facts entries come from the trusted authenticated account and outrank document_status' in (
        payload['evidence_rules'])
    assert 'the issues name the claim and the excerpt id or approved_facts entry' in payload['evidence_rules']


def test_no_approved_facts_without_business_approval(offline, monkeypatch):
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')
    offline['knowledge'] = LIVE_KNOWLEDGE
    offline['replies'] = [NO_REPEAT_REPAIR]
    from test_ai_review_evidence import LIVE_CONVERSATION
    bobbie.generate_ai_reply(LIVE_CONVERSATION, _with_question(QUESTION), agent_context=LEGACY_AGENT)
    assert 'approved_facts' not in _generator_knowledge(offline['bodies'][0])


# Second live run (after the first fixes): the draft *stated* a visit offer and
# re-asked price; the repair then asked to stop by. Exact model text below.
LIVE_AFTER_DRAFT = ("I'm with Real Broker LLC now, and I've previously been with RE/MAX Premier's North Shore division. "
                    "I can't confirm my exact tenure there. I'm happy to answer more questions, and I'd also like to "
                    'stop by and see the home in person so I can talk through your options. What price were you '
                    'hoping for?')


def test_stated_visit_offer_and_repeat_in_the_live_draft_are_both_caught():
    violations = find_reply_policy_violations(LIVE_AFTER_DRAFT, history=_with_question(QUESTION),
                                              next_step='answer_and_qualify')
    # Its 290-character length is no longer a violation; the repeat and the visit offer are.
    assert {'repeated_question', 'premature_meeting_request'} <= set(violations)
    assert 'over_provider_limit' not in violations
    assert premature_meeting_question(LIVE_AFTER_DRAFT, 'answer_and_qualify', QUESTION).startswith(
        "I'd also like to stop by")


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_repair_always_carries_the_no_meeting_rule_at_answer_steps(
        offline, monkeypatch, make_user, session, provider, model):
    use(monkeypatch, provider, model)
    # Only a repeated question: no meeting wording in the draft.
    repeated = NO_REPEAT_REPAIR.replace('Are you hoping to sell soon?', 'What price were you hoping to get for it?')

    sent = _run(offline, monkeypatch, make_user, session, [BIO_DISPOSITION, repeated, NO_REPEAT_REPAIR, VALID])

    assert sent == [(NO_REPEAT_REPAIR, 'ai.reply')]
    repair = json.dumps(offline['bodies'][2])
    assert 'Do not mention, offer or ask about a visit, stop-by, call or meeting in this reply' in repair
    generation = json.dumps(offline['bodies'][1])
    assert ('Meeting offer permitted in this reply: no. Do not mention, offer or ask about a visit, stop-by, '
            'call or meeting.') in generation
