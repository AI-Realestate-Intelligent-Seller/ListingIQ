"""First-person identity and SMS length, replayed from the three live Haiku runs.

The SMS strings below are the exact model outputs recorded on 27 Oct 2026.
Provider HTTP is faked, so these tests pin policy decisions and request
contents; they do not show how a live model will write or judge.
"""

import json

import pytest

from app.sms import bobbie
from app.sms.policy import find_reply_policy_violations, safe_grounded_fallback
from test_ai_review_evidence import (
    _generator_knowledge,
    LEGACY_AGENT,  # noqa: F401  (offline is a fixture)
    LIVE_CONVERSATION, LIVE_HISTORY, LIVE_KNOWLEDGE, PROVIDERS, VALID, _review_payload, offline, use,
)

RUN1_DRAFT = ('Bobbie Fisher is a Chicago-area real estate professional. Public records list her with Real Broker '
              'LLC, and she previously worked with RE/MAX Premier on brokerage growth and agent recruiting. Are you '
              'thinking about selling 100 Example Ave?')
RUN1_REGENERATED = ('Bobbie Fisher is a Chicago-area real estate professional, publicly listed with Real Broker LLC '
                    '(confirmable directly), with background in brokerage growth, agent recruiting, and leadership. '
                    'Would you be open to a short in-person visit to talk through your options for 100 Example Ave?')
RUN1_REPAIR = ("I'm Bobbie Fisher, a Chicago-area real estate professional publicly listed with Real Broker LLC, "
               'which you can confirm directly. My background includes brokerage growth, agent recruiting and '
               "leadership. Would you be open to discussing your home's options?")
RUN2_DRAFT = ("I'm Bobbie Fisher, a Chicago-area real estate professional. I'm currently listed with Real Broker LLC, "
              "and I previously worked with RE/MAX Premier's North Shore division on brokerage growth and agent "
              'recruiting.\n\nAre you thinking about selling 100 Example Ave at some point?')
RUN2_REPAIR = ("I'm Bobbie Fisher, a Chicago-area real estate professional currently listed with Real Broker LLC, "
               'with past brokerage growth and agent leadership work at RE/MAX Premier. Would you be open to selling '
               '100 Example Ave?')
RUN2_FINAL = ('Bobbie Fisher is a Chicago-area real estate professional publicly listed with Real Broker LLC, with '
              'earlier brokerage growth and agent leadership work at RE/MAX Premier. Would you be open to discussing '
              'options for selling 100 Example Ave?')
RUN3_DRAFT = ('Bobbie Fisher is a Chicago-area real estate professional currently listed with Real Broker LLC, with '
              "earlier brokerage growth and agent recruiting experience at RE/MAX Premier's North Shore division. Are "
              'you thinking about selling 100 Example Ave at some point?')
RUN3_REPAIR = ('Bobbie Fisher is a Chicago-area real estate professional, publicly listed with Real Broker LLC. She '
               'previously worked with RE/MAX Premier on brokerage growth and agent recruiting. Would you be open to '
               'discussing your plans for 100 Example Ave?')
RUN3_FINAL = ('Bobbie Fisher is a Chicago-area real estate professional publicly listed with Real Broker LLC, with a '
              'background in brokerage growth, agent recruiting, and leadership development. Are you open to talking '
              'about selling 100 Example Ave?')
FIRST_PERSON_SHORT = ("I'm a Chicago-area real estate professional publicly listed with Real Broker LLC, with earlier "
                      'brokerage growth and agent recruiting work at RE/MAX Premier. Are you thinking of selling?')
RUN3_REJECTION = json.dumps({
    'valid': False, 'issues': ['does_not_answer_owner_question', 'evasive_non_answer'],
    'rewrite_instruction': 'Answer directly in first person; keep it under 220 characters.',
    'reason': "Draft refuses to answer the owner's direct questions."})


def _violations(text):
    # Production judges third-person self-reference against the trusted agent name.
    return find_reply_policy_violations(text, phone_source_known=False, history=LIVE_HISTORY,
                                        agent_name=LEGACY_AGENT.agent_name)


def test_recorded_lengths_match_the_live_runs():
    lengths = [len(t) for t in (RUN1_DRAFT, RUN1_REGENERATED, RUN1_REPAIR, RUN2_DRAFT, RUN2_REPAIR,
                                RUN2_FINAL, RUN3_DRAFT, RUN3_REPAIR, RUN3_FINAL)]
    assert lengths == [239, 284, 255, 274, 215, 238, 261, 243, 234]


@pytest.mark.parametrize('text', [RUN1_DRAFT, RUN1_REGENERATED, RUN2_FINAL, RUN3_DRAFT, RUN3_REPAIR, RUN3_FINAL],
                         ids=['run1_draft', 'run1_regenerated', 'run2_final', 'run3_draft', 'run3_repair',
                              'run3_final'])
def test_recorded_third_person_outputs_are_identity_violations(text):
    assert 'identity_switch' in _violations(text)


@pytest.mark.parametrize('text', [RUN1_REPAIR, RUN2_DRAFT, RUN2_REPAIR], ids=['run1_repair', 'run2_draft',
                                                                           'run2_repair'])
def test_recorded_first_person_outputs_keep_their_identity(text):
    assert 'identity_switch' not in _violations(text)


@pytest.mark.parametrize('text', [
    'Bobbie Fisher has worked with sellers across the North Shore.',
    "Bobbie Fisher's background includes brokerage growth.",
    "Bobbie's team will follow up with you.",
    'Bobbie Fisher previously worked with RE/MAX Premier.',
    'Bobbie was with RE/MAX Premier earlier.',
    'Bobbie will reach out to you shortly.',
])
def test_bobbie_specific_third_person_statements_are_blocked(text):
    assert 'identity_switch' in find_reply_policy_violations(text, agent_name=LEGACY_AGENT.agent_name)


@pytest.mark.parametrize('text', [
    "I'm Bobbie Fisher currently listed with Real Broker LLC. Are you thinking of selling?",
    'This is Bobbie Fisher with Real Broker LLC. Is now a good time to talk?',
    "Hi, it's Bobbie Fisher. I'm publicly listed with Real Broker LLC.",
    'You asked "Is Bobbie Fisher still with RE/MAX?" I\'m publicly listed with Real Broker LLC now.',
    'You asked “who Bobbie Fisher is” — I’m a Chicago-area real estate professional.',
    'Your neighbor Bobbie Smith is selling too, so timing matters. What price are you hoping for?',
    FIRST_PERSON_SHORT,
])
def test_first_person_quotes_and_other_people_are_not_identity_violations(text):
    assert 'identity_switch' not in find_reply_policy_violations(text, agent_name=LEGACY_AGENT.agent_name)


def test_draft_prompt_requires_bobbie_first_person_voice_and_natural_length():
    prompt = bobbie.build_agent_prompt(LIVE_CONVERSATION, LEGACY_AGENT)
    assert ('Speak in first person as Bobbie Fisher (I, me, my); never refer to Bobbie Fisher in the third person'
            in prompt)
    assert 'Never call yourself an assistant' in prompt
    # No fixed character budget: concise by default, longer when the answer needs it.
    assert '220' not in prompt and 'Usually 1-3 concise sentences' in prompt
    assert "Answer the owner's actual question first" in prompt


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_run3_replay_repair_sees_blocked_draft_keeps_voice_and_fallback_is_unchanged(
        offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    offline['replies'] = [RUN3_DRAFT, RUN3_REPAIR]

    reply = bobbie.generate_ai_reply(LIVE_CONVERSATION, LIVE_HISTORY, agent_context=LEGACY_AGENT)

    # A repair that still fails policy falls back exactly as before. Length is
    # no longer a violation; the third-person voice still is.
    expected_fallback = safe_grounded_fallback(LIVE_HISTORY[-1]['text'], ['identity_switch'],
                                               LIVE_HISTORY, {}, LEGACY_AGENT.agent_name,
                                               LEGACY_AGENT.brokerage_name)
    assert reply == expected_fallback
    assert len(offline['bodies']) == 2
    repair = json.dumps(offline['bodies'][1])
    assert 'Fix these violations: identity_switch' in repair and 'over_length' not in repair
    assert "in Bobbie Fisher's own first-person voice (I, me, my)" in repair and '220' not in repair
    assert 'the blocked draft was:' in repair
    assert json.dumps(json.dumps(RUN3_DRAFT))[1:-1] in repair


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_repair_never_echoes_a_draft_blocked_for_unsafe_content(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    unsafe = "I'm Bobbie Fisher. Call me at 555 123 4567 and I'll explain my background."
    offline['replies'] = [unsafe, FIRST_PERSON_SHORT]

    reply = bobbie.generate_ai_reply(LIVE_CONVERSATION, LIVE_HISTORY, agent_context=LEGACY_AGENT)

    assert reply == FIRST_PERSON_SHORT
    repair = json.dumps(offline['bodies'][1])
    assert 'invented_phone' in repair and 'blocked draft' not in repair and '555 123 4567' not in repair


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_run3_replay_end_to_end_reviewer_input_and_identity_repair(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    # draft -> repair -> fallback -> reviewer rejects -> regenerated third person -> repaired first person
    # -> the repaired regeneration is itself reviewed before it can be used
    offline['replies'] = [RUN3_DRAFT, RUN3_REPAIR, RUN3_REJECTION, RUN3_FINAL, FIRST_PERSON_SHORT, VALID]
    evidence = []
    draft = bobbie.generate_ai_reply(LIVE_CONVERSATION, LIVE_HISTORY, evidence_out=evidence, agent_context=LEGACY_AGENT)

    result = bobbie.review_and_repair_ai_reply(
        LIVE_CONVERSATION, LIVE_HISTORY, draft, {'next_step': 'answer_and_qualify'}, knowledge_evidence=evidence, agent_context=LEGACY_AGENT)

    review = _review_payload(offline['bodies'][2])
    assert review['proposed_agent_reply'] == draft  # the reviewer sees the real fallback text
    assert review['approved_knowledge_evidence'] == evidence and evidence
    assert result == FIRST_PERSON_SHORT and _violations(result) == []
    assert len(offline['bodies']) == 6 and len(offline['searches']) == 2
    assert 'Fix these violations: identity_switch' in json.dumps(offline['bodies'][4])
    final_review = _review_payload(offline['bodies'][5])
    assert final_review['proposed_agent_reply'] == FIRST_PERSON_SHORT
    assert final_review['approved_knowledge_evidence'] == [_generator_knowledge(offline['bodies'][3])]


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_run2_replay_first_person_long_draft_goes_straight_to_review(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    offline['replies'] = [RUN2_DRAFT, VALID]
    evidence = []
    draft = bobbie.generate_ai_reply(LIVE_CONVERSATION, LIVE_HISTORY, evidence_out=evidence, agent_context=LEGACY_AGENT)

    result = bobbie.review_and_repair_ai_reply(
        LIVE_CONVERSATION, LIVE_HISTORY, draft, {'next_step': 'answer_and_qualify'}, knowledge_evidence=evidence, agent_context=LEGACY_AGENT)

    # The 273-character first-person draft was once repaired only for length; it is
    # now reviewed and sent as written (whitespace normalized), with no repair call.
    assert result == ' '.join(RUN2_DRAFT.split()) and len(offline['bodies']) == 2
    assert _review_payload(offline['bodies'][1])['proposed_agent_reply'] == result


# Product decision (27 Oct 2026): natural, direct first-person facts; no forced
# "publicly listed" style hedges; never invented experience, titles, or years.
RUN4_DRAFT = ("I'm a Chicago-area real estate professional currently listed with Real Broker LLC, with earlier "
              'brokerage growth and agent-leadership experience at RE/MAX Premier. Are you thinking about selling '
              '100 Example Ave?')
DIRECT_REPLY = ("I'm with Real Broker LLC, and earlier I worked on brokerage growth and agent recruiting at RE/MAX "
                'Premier. Are you thinking about selling 100 Example Ave?')


def test_draft_prompt_asks_for_direct_first_person_facts_without_forced_hedges(monkeypatch):
    from app.core.config import settings
    monkeypatch.setitem(settings['sms'], 'approved_brokerage', '')  # the brokerage-neutral default
    prompt = bobbie.build_agent_prompt(LIVE_CONVERSATION, LEGACY_AGENT)
    assert 'State supported facts in natural, concise first person without stock hedges such as' in prompt
    assert '"publicly listed", "public records show", or "pending confirmation"' in prompt
    assert 'never invent experience, titles, or years' in prompt
    assert 'Real Broker' not in prompt and 'RE/MAX' not in prompt  # brokerage still comes from knowledge


def test_rules_say_status_notes_do_not_forbid_stating_a_fact():
    rules = bobbie.REVIEW_EVIDENCE_RULES
    assert 'a status note such as "publicly supported" or "pending confirmation" describes the source' in rules
    assert 'does not by itself forbid stating the fact' in rules
    assert 'Requires confirmation: an excerpt explicitly says the claim must not be stated' in rules


@pytest.mark.parametrize('text', [RUN4_DRAFT, DIRECT_REPLY], ids=['run4_draft', 'direct_reply'])
def test_direct_first_person_brokerage_replies_pass_sms_policy(text):
    assert _violations(text) == []


@pytest.mark.parametrize('text,violation', [
    ("I've been with Real Broker LLC for 5 years. Are you thinking of selling?", 'unsupported_tenure'),
    ("I'm with Real Broker LLC since 2019. Are you thinking of selling?", 'unsupported_tenure'),
    ('Bobbie Fisher is with Real Broker LLC. Are you thinking of selling?', 'identity_switch'),
])
def test_direct_wording_still_blocks_invented_years_and_third_person(text, violation):
    assert violation in _violations(text)


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_run4_replay_accepted_direct_draft_is_sent_without_regeneration(offline, monkeypatch, provider, model):
    use(monkeypatch, provider, model)
    offline['knowledge'] = LIVE_KNOWLEDGE
    offline['replies'] = [RUN4_DRAFT, VALID]
    evidence = []
    draft = bobbie.generate_ai_reply(LIVE_CONVERSATION, LIVE_HISTORY, evidence_out=evidence, agent_context=LEGACY_AGENT)

    result = bobbie.review_and_repair_ai_reply(
        LIVE_CONVERSATION, LIVE_HISTORY, draft, {'next_step': 'answer_and_qualify'}, knowledge_evidence=evidence, agent_context=LEGACY_AGENT)

    assert result == draft == RUN4_DRAFT and len(offline['bodies']) == 2
    payload = _review_payload(offline['bodies'][1])
    assert payload['proposed_agent_reply'] == RUN4_DRAFT
    assert 'must not be rejected for lacking that wording' in payload['evidence_rules']
