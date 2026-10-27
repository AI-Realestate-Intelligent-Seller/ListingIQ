"""Manual test failure (27 Oct 2026, conversation 4, messages 21 -> 22).

"Who is Bobbie, and how long has she been with RE/MAX?" was answered with the
calendar-safety line "I haven't scheduled anything, and I won't invent a time."
The reviewed, regenerated reply said its RE/MAX tenure "isn't confirmed", and
the booking guard treated any "confirmed" as an unbooked appointment claim.
Provider HTTP is faked; this pins the pipeline, not live model wording.
"""

import json
import logging
import re

import pytest

from app.core.config import settings
from app.sms import service
from app.sms.scheduling import contains_unbooked_confirmation
from test_ai_review_evidence import (  # noqa: F401  (offline is a fixture)
    LIVE_KNOWLEDGE, PROVIDERS, QUESTION, _capture_sent, _disposition, _review_payload, _thread, offline, use,
)

CALENDAR_SAFETY_LINE = 'I haven’t scheduled anything, and I won’t invent a time.'
OLD_UNBOOKED_CONFIRMATION = re.compile(r"\b(?:you(?:['’]re| are) all set|confirmed|booked)\b", re.I)

BIO_DISPOSITION = _disposition(intent='asks_about_agent_background', lead_status='want_more_info')
OVERLONG_DRAFT = ("I'm Bobbie Fisher, a Chicago-area real estate professional, and I'm with Real Broker LLC now. "
                  "Before that I worked with RE/MAX Premier on brokerage growth, agent recruiting and leadership "
                  'development across the North Shore. Are you thinking about selling 1 Test St?')
REPAIRED_DRAFT = "I'm Bobbie Fisher, with Real Broker LLC now. Are you thinking about selling 1 Test St?"
REVIEW_REJECTION = json.dumps({
    'valid': False, 'issues': ['does_not_answer_tenure_question'],
    'rewrite_instruction': 'Also answer how long she was with RE/MAX without inventing a tenure.',
    'reason': 'The owner asked how long she was with RE/MAX.'})
REGENERATED = ("I'm with Real Broker LLC now. My exact time at RE/MAX Premier isn't confirmed, so I won't guess. "
               'Are you thinking about selling 1 Test St?')


def _run(offline, monkeypatch, make_user, session, regenerated, caplog):
    monkeypatch.setitem(settings['sms'], 'approved_brokerage', 'Real Broker LLC')
    offline['knowledge'] = LIVE_KNOWLEDGE
    conversation = _thread(session, make_user)
    offline['replies'] = [BIO_DISPOSITION, OVERLONG_DRAFT, REPAIRED_DRAFT, REVIEW_REJECTION, regenerated]
    sent = _capture_sent(monkeypatch)
    caplog.set_level(logging.INFO)
    service.process_ai_reply(conversation.id, QUESTION)
    return sent


def test_the_exact_question_is_the_one_from_the_failed_manual_test():
    assert QUESTION == 'Who is Bobbie, and how long has she been with RE/MAX?'


def test_the_old_guard_matched_the_tenure_answer_and_the_new_one_does_not():
    assert OLD_UNBOOKED_CONFIRMATION.search(REGENERATED)  # reproduces the bug
    assert not contains_unbooked_confirmation(REGENERATED)


@pytest.mark.parametrize('provider,model', PROVIDERS)
def test_tenure_answer_survives_review_regeneration_and_calendar_guard(
        offline, monkeypatch, make_user, session, provider, model, caplog):
    use(monkeypatch, provider, model)

    sent = _run(offline, monkeypatch, make_user, session, REGENERATED, caplog)

    assert sent == [(REGENERATED, 'ai.reply')]
    assert CALENDAR_SAFETY_LINE not in [text for text, _event in sent]
    assert 'calendar_safety' not in caplog.text
    # disposition, draft, policy repair, review, regeneration
    assert len(offline['bodies']) == 5 and len(offline['searches']) == 2
    repair = json.dumps(offline['bodies'][2])
    assert 'Fix these violations: over_length' in repair
    review = _review_payload(offline['bodies'][3])
    assert review['proposed_bobbie_reply'] == REPAIRED_DRAFT and review['approved_knowledge_evidence']
    assert 'Also answer how long she was with RE/MAX' in json.dumps(offline['bodies'][4])


@pytest.mark.parametrize('regenerated', [
    "You're confirmed for Tuesday at 3 pm. Are you thinking about selling?",
    'Your visit is confirmed for tomorrow. See you then!',
    'I booked you for Monday morning.',
])
def test_real_unbooked_confirmations_still_get_the_calendar_safety_line(
        offline, monkeypatch, make_user, session, regenerated, caplog):
    use(monkeypatch, 'anthropic', 'claude-haiku-5-5')

    sent = _run(offline, monkeypatch, make_user, session, regenerated, caplog)

    assert sent == [(CALENDAR_SAFETY_LINE, 'ai.reply')]
    assert 'calendar_safety' in caplog.text


@pytest.mark.parametrize('text', [
    "My exact time at RE/MAX Premier isn't confirmed, so I won't guess.",
    "My RE/MAX Premier years haven't been confirmed.",
    'That figure is not confirmed.',
    "I'm with Real Broker LLC; earlier I was with RE/MAX Premier.",
])
def test_biography_wording_is_not_a_booking_claim(text):
    assert not contains_unbooked_confirmation(text)


@pytest.mark.parametrize('text', [
    "You're confirmed for Tuesday at 3 pm.", 'Your visit is confirmed.', "It's confirmed, see you then.",
    'Appointment confirmed for tomorrow.', "I've confirmed the time.", 'Confirmed for 10 am Friday.',
    "You're all set!", 'I booked you for Monday.', "I'll schedule that now.", 'Everything is confirmed.',
    'Your meeting has been confirmed.',
])
def test_booking_claims_are_still_detected(text):
    assert contains_unbooked_confirmation(text)
