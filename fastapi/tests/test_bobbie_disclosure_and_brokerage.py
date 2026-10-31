"""Truthful AI disclosure, brokerage approval gate, outreach signature and log masking.

Offline only: provider HTTP is faked, so these tests pin policy decisions and
request contents, not how a live model will write.
"""

import json
import logging

import pytest

from app.core.config import settings
from app.sms import bobbie
from app.sms.outreach import build_initial_outreach, outreach_signature
from app.sms.policy import find_reply_policy_violations, is_ai_identity_question, safe_grounded_fallback
from app.sms.prompt import ai_disclosure_rule, build_agent_prompt
from test_ai_review_evidence import (
    LEGACY_AGENT, LEGACY_AGENT_REAL_BROKER,  # noqa: F401  (offline is a fixture)
    LIVE_CONVERSATION, LIVE_KNOWLEDGE, PROVIDERS, VALID, _review_payload, offline, use,
)

OPENER = {'direction': 'outbound', 'text': 'Hi, this is Bobbie Fisher. Would you be open to a brief conversation?'}


# Name-based identity checks are judged against the trusted agent name that
# production passes (generation, repair, fallback and follow-up review all do).
AGENT = LEGACY_AGENT.agent_name


def _asked(question):
    return [OPENER, {'direction': 'inbound', 'text': question}]


def _violations(reply, question):
    return find_reply_policy_violations(reply, phone_source_known=False, history=_asked(question),
                                        agent_name=AGENT)


@pytest.mark.parametrize('question', [
    'Are you an AI?', 'Am I speaking with Bobbie?', 'Is this automated?', 'is this a real person',
    'r u a bot', 'Am I texting with a real person?', 'Is this really Bobbie?', 'Is this an automated message?',
])
def test_direct_identity_questions_are_detected(question):
    assert is_ai_identity_question(question, AGENT)


@pytest.mark.parametrize('text', [
    'Who is Bobbie Fisher?', 'Are you a real estate agent?', 'Are you available Tuesday?',
    'Is this house still listed?', 'Is this real estate company local?',
])
def test_ordinary_questions_are_not_identity_questions(text):
    assert not is_ai_identity_question(text, AGENT)


@pytest.mark.parametrize('reply', [
    "These replies are automated (AI) on Bobbie Fisher's behalf. Are you thinking about selling?",
    "I'm Bobbie Fisher's AI assistant, texting on her behalf. What price are you hoping for?",
    'This is an automated assistant for Bobbie Fisher. Bobbie can follow up if you want.',
])
def test_truthful_disclosure_passes_policy_when_asked(reply):
    assert _violations(reply, 'Am I talking to a real person or a bot?') == []


@pytest.mark.parametrize('reply', [
    "Yes, this is really Bobbie. Are you thinking about selling?",
    "I'm a real person, not a bot. What price are you hoping for?",
    "I'm not an AI. Are you thinking about selling?",
    "It's actually me texting. Are you thinking about selling?",
])
def test_false_human_claims_are_always_blocked(reply):
    assert 'false_human_claim' in _violations(reply, 'Are you a bot?')
    assert 'false_human_claim' in find_reply_policy_violations(reply, agent_name=AGENT)  # even when not asked


def test_identity_question_answer_without_disclosure_is_blocked():
    assert 'missing_ai_disclosure' in _violations(
        "I'm a Chicago-area real estate professional. Are you thinking about selling?", 'Is this automated?')


def test_unprompted_messages_keep_first_person_and_need_no_disclaimer():
    history = [OPENER, {'direction': 'inbound', 'text': 'What would you do differently with my listing?'}]
    reply = "I'd review the prior pricing and buyer feedback first. What price are you hoping for?"
    assert find_reply_policy_violations(reply, history=history, agent_name=AGENT) == []
    assert 'identity_switch' in find_reply_policy_violations("I'm Bobbie Fisher's AI assistant.", history=history,
                                                             agent_name=AGENT)


def test_grounded_fallback_for_identity_questions_is_a_truthful_disclosure():
    history = _asked('Are you a bot?')
    fallback = safe_grounded_fallback('Are you a bot?', ['missing_ai_disclosure'], history, agent_name=AGENT)
    assert fallback.startswith('These replies are automated (AI) on Bobbie Fisher’s behalf.')
    assert _violations(fallback, 'Are you a bot?') == []


def test_prompt_states_truthful_disclosure_rule():
    prompt = build_agent_prompt(LIVE_CONVERSATION, LEGACY_AGENT)
    assert 'Never call yourself an assistant unprompted.' in prompt
    assert ai_disclosure_rule('Bobbie Fisher') in prompt
    assert "these replies are automated (AI) on Bobbie Fisher's behalf" in prompt
    assert 'never claim a human is personally typing' in prompt


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_false_human_draft_is_repaired_into_disclosure_without_echoing_it(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    history = _asked('Am I talking to a real person or a bot?')
    disclosure = "These replies are automated (AI) on Bobbie Fisher's behalf. Are you thinking about selling?"
    offline['replies'] = ["Yes, this is really Bobbie. Are you thinking about selling?", disclosure]

    reply = bobbie.generate_ai_reply(LIVE_CONVERSATION, history, agent_context=LEGACY_AGENT)

    assert reply == disclosure and len(offline['bodies']) == 2
    repair = json.dumps(offline['bodies'][1])
    assert 'false_human_claim' in repair and 'blocked draft' not in repair and 'really Bobbie' not in repair


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_failed_disclosure_repair_falls_back_to_truthful_disclosure(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    history = _asked('Is this automated?')
    offline['replies'] = ["I'm here to help. Are you thinking about selling?",
                          "Happy to help. What price are you hoping for?"]

    reply = bobbie.generate_ai_reply(LIVE_CONVERSATION, history, agent_context=LEGACY_AGENT)

    assert reply.startswith('These replies are automated (AI) on Bobbie Fisher’s behalf.')
    assert len(offline['bodies']) == 2


def test_brokerage_stays_unconfirmed_without_a_trusted_account_brokerage():
    # The brokerage now comes only from the trusted account (LEGACY_AGENT has none).
    prompt = build_agent_prompt(LIVE_CONVERSATION, LEGACY_AGENT)
    assert 'No current brokerage is available in trusted account data' in prompt
    assert 'never state a brokerage as my confirmed current firm' in prompt
    assert 'Real Broker' not in prompt and 'RE/MAX' not in prompt
    rules = bobbie.review_evidence_rules(LEGACY_AGENT)
    assert rules.startswith(bobbie.REVIEW_EVIDENCE_RULES)
    assert "stating a brokerage as the agent's confirmed current firm is an issue" in rules
    assert outreach_signature(LEGACY_AGENT) == 'Bobbie Fisher'


def test_trusted_account_brokerage_is_stated_plainly():
    prompt = build_agent_prompt(LIVE_CONVERSATION, LEGACY_AGENT_REAL_BROKER)
    assert 'say so plainly, e.g. "I\'m with Real Broker LLC."' in prompt
    assert 'No current brokerage is available' not in prompt
    rules = bobbie.review_evidence_rules(LEGACY_AGENT_REAL_BROKER)
    assert 'a plain statement such as "I\'m with Real Broker LLC" is correct' in rules
    assert build_initial_outreach('Maya Chen', '1 Elm St', 'It came off the market', LEGACY_AGENT_REAL_BROKER
                                  ).endswith('Bobbie Fisher – Real Broker LLC')


def test_default_outreach_signature_has_no_stale_brokerage():
    text = build_initial_outreach('Maya Chen', '1 Elm St.', 'It came off the market.', LEGACY_AGENT)
    assert text == ('Hey Maya, I’m reaching out about 1 Elm St. It came off the market. '
                    'Would you be open to a brief conversation? Bobbie Fisher')


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_bobbie_ai_logs_show_only_the_last_four_digits(offline, monkeypatch, provider, model, caplog):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    # A policy-blocked draft (invented phone number) exercises the [ai-policy] log line.
    offline['replies'] = ['Call me at 312 555 0199.', "I'm with you. What price are you hoping for?", VALID]
    history = _asked('Who is Bobbie Fisher?')
    caplog.set_level(logging.INFO)
    evidence = []
    draft = bobbie.generate_ai_reply(LIVE_CONVERSATION, history, evidence_out=evidence, agent_context=LEGACY_AGENT)
    bobbie.review_and_repair_ai_reply(LIVE_CONVERSATION, history, draft, {}, knowledge_evidence=evidence, agent_context=LEGACY_AGENT)

    text = caplog.text
    assert '[ai-policy]' in text and '[ai-review]' in text
    assert LIVE_CONVERSATION.contact not in text and '3125550142' not in text
    assert 'contact=…0142' in text


# ---------------------------------------------------------------------------
# First-person work history vs. invented local track record (27 Oct 2026 fix).
LIVE4_DRAFT = ("I'm a Chicago-area real estate professional publicly listed with Real Broker LLC, and previously with "
               "RE/MAX Premier on the North Shore. I've worked in brokerage growth and agent recruiting. Would you be "
               "open to me stopping by 100 Example Ave to take a look and talk through your options?")
LIVE4_REPAIR = ("I'm a Chicago-area real estate professional. My most recent public listings are with Real Broker LLC, "
                "and I've worked in brokerage growth and agent leadership. Would you be open to discussing your "
                "options for 100 Example Ave?")
APPROVED_REPLY = ("I'm with Real Broker LLC, and earlier I've worked with RE/MAX Premier on brokerage growth and "
                  'agent recruiting. Are you thinking about selling 100 Example Ave?')


def _policy(text):
    return find_reply_policy_violations(text, phone_source_known=False)


@pytest.mark.parametrize('text', [
    "I've worked with RE/MAX Premier on brokerage growth. Are you thinking about selling?",
    "I'm with Real Broker LLC, and I've worked in brokerage growth and agent recruiting.",
    "I've helped agents grow through recruiting and leadership development.",
    "I have worked with RE/MAX Premier's North Shore division on recruiting.",
    "I've handled agent recruiting and leadership development.",
    'The house is 20 years old, so condition matters. What updates have you made?',
    LIVE4_REPAIR,
    APPROVED_REPLY,
])
def test_supported_first_person_work_history_is_allowed(text):
    assert _policy(text) == []


@pytest.mark.parametrize('text,violation', [
    ("I've sold 100 homes in your neighborhood.", 'unsupported_production'),
    ("I've handled 200 transactions nearby.", 'unsupported_production'),
    ("I've worked with many sellers in your area.", 'unsupported_local_experience'),
    ("I've helped homeowners in your neighborhood sell fast.", 'unsupported_local_experience'),
    ('I work with buyers nearby every week.', 'unsupported_local_experience'),
    ("I've helped 50 families sell this year.", 'unsupported_local_experience'),
    ('I know the Grayslake market well.', 'unsupported_local_experience'),
    ('My local experience helps me price homes.', 'unsupported_local_experience'),
    ('I have 20 years of local experience.', 'unsupported_tenure'),
    ("I've been in real estate for 35 years.", 'unsupported_tenure'),
    ('I bring 35 years of experience.', 'unsupported_tenure'),
    ('I have 35 years in real estate.', 'unsupported_tenure'),
    ('Decades of experience help me price homes.', 'unsupported_tenure'),
    ("I've been selling homes since 2011.", 'unsupported_tenure'),
])
def test_invented_local_track_record_and_years_stay_blocked(text, violation):
    assert violation in _policy(text)


def test_live4_draft_now_passes_policy():
    # Run 4's first draft was blocked for "I've worked" and later only for length; neither blocks now.
    assert _policy(LIVE4_DRAFT) == []


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_trusted_brokerage_reaches_generation_review_and_outreach(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    offline['replies'] = [APPROVED_REPLY, VALID]
    history = _asked('Who is Bobbie Fisher, which brokerage is she currently with, and what is her experience?')
    evidence = []
    draft = bobbie.generate_ai_reply(LIVE_CONVERSATION, history, evidence_out=evidence,
                                     agent_context=LEGACY_AGENT_REAL_BROKER)

    result = bobbie.review_and_repair_ai_reply(
        LIVE_CONVERSATION, history, draft, {'next_step': 'answer_and_qualify'}, knowledge_evidence=evidence,
        agent_context=LEGACY_AGENT_REAL_BROKER)

    assert result == draft == APPROVED_REPLY and len(offline['bodies']) == 2  # no repair, no regeneration
    generation = json.dumps(offline['bodies'][0])
    assert 'from the trusted account, is Real Broker LLC' in generation and "I'm with Real Broker LLC" in generation
    assert 'No current brokerage is available' not in generation
    rules = _review_payload(offline['bodies'][1])['evidence_rules']
    assert 'identifies Real Broker LLC as the resolved agent' in rules
    assert 'do not ask for wording such as "publicly listed" or "pending confirmation" about it' in rules
    assert build_initial_outreach('Maya Chen', '1 Elm St', 'It came off the market', LEGACY_AGENT_REAL_BROKER
                                  ).endswith('Bobbie Fisher – Real Broker LLC')


def test_retired_brokerage_setting_cannot_reach_any_prompt(offline, monkeypatch):
    # BOBBIE_APPROVED_BROKERAGE is no longer an identity source; only the trusted account is.
    monkeypatch.setitem(settings['sms'], 'approved_brokerage', 'Injected Realty')
    assert 'Injected Realty' not in build_agent_prompt(LIVE_CONVERSATION, LEGACY_AGENT)
    assert 'Injected Realty' not in bobbie.review_evidence_rules(LEGACY_AGENT)
    assert 'Injected Realty' not in build_initial_outreach('Maya', '1 Elm St', 'Off market', LEGACY_AGENT)
