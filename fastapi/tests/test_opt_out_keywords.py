"""The opt-out keyword check must not mistake a visit invitation for STOP."""

import pytest

from app.sms.classifier import classify_lead_message, is_opt_out


@pytest.mark.parametrize('text', [
    '\nThere is a key box out side the home, feel free to stop by.',
    'Stop by anytime this week',
    'You can stop in after 5',
    'Feel free to stop over Saturday',
    'Just stop past the house',
])
def test_visit_invitations_are_not_opt_outs(text):
    assert not is_opt_out(text)
    assert (classify_lead_message(text) or {}).get('lead_status') != 'dnc'


@pytest.mark.parametrize('text', [
    'STOP', 'stop', 'Stop.', 'STOP!!', 'Please stop texting me', 'stop messaging me',
    'Stop contacting me', 'stop it', 'STOPALL', 'unsubscribe', 'Remove me',
    'do not contact me', "don't contact me again", 'leave me alone', 'take me off your list',
    'Stop. By the way I am not selling', 'stop bye',
])
def test_real_opt_outs_still_opt_out(text):
    assert is_opt_out(text)
    assert classify_lead_message(text)['lead_status'] == 'dnc'
