"""JSON Schemas for provider-enforced internal AI responses."""

def _suggestions_schema(max_length: int) -> dict:
    return {
        'type': 'object',
        'properties': {
            'suggestions': {
                'type': 'array',
                'items': {'type': 'string', 'minLength': 1, 'maxLength': max_length},
                'minItems': 0,
                'maxItems': 3,
            },
        },
        'required': ['suggestions'],
        'additionalProperties': False,
    }


REPLY_SUGGESTIONS_SCHEMA = _suggestions_schema(320)
REASON_SUGGESTIONS_SCHEMA = _suggestions_schema(300)

DISPOSITION_SCHEMA = {
    'type': 'object',
    'properties': {
        'action': {'type': 'string', 'enum': ['continue', 'end']},
        'intent': {'type': 'string', 'maxLength': 80},
        'outcome': {
            'type': 'string',
            'enum': ['positive', 'neutral', 'negative', 'disqualified'],
        },
        'lead_status': {
            'type': 'string',
            'enum': [
                # Mirrors bobbie.LEAD_STATUSES, which accepts every value here.
                'processing', 'want_more_info', 'interested', 'ready_to_sell',
                'location_discussion', 'not_interested',
            ],
        },
        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'conversation_stage': {
            'type': 'string',
            'enum': [
                'discovery', 'qualified_for_call', 'call_requested',
                'scheduling', 'complete',
            ],
        },
        'next_step': {
            'type': 'string',
            'enum': ['answer_and_qualify', 'answer_only', 'offer_call', 'schedule', 'close'],
        },
        'qualification_focus': {'type': 'string', 'maxLength': 160},
        'calendar': {
            'type': 'object',
            'properties': {
                'state': {
                    'type': 'string',
                    'enum': [
                        'none', 'call_declined', 'call_accepted',
                        'time_proposed', 'booking_confirmed',
                    ],
                },
                'should_fetch_availability': {'type': 'boolean'},
                'requested_time_text': {'type': 'string', 'maxLength': 160},
                'reason': {'type': 'string', 'maxLength': 200},
            },
            'required': [
                'state', 'should_fetch_availability', 'requested_time_text', 'reason',
            ],
            'additionalProperties': False,
        },
        'reason': {'type': 'string', 'maxLength': 240},
    },
    'required': [
        'action', 'intent', 'outcome', 'lead_status', 'confidence',
        'conversation_stage', 'next_step', 'qualification_focus', 'calendar', 'reason',
    ],
    'additionalProperties': False,
}

REVIEW_SCHEMA = {
    'type': 'object',
    'properties': {
        'valid': {'type': 'boolean'},
        'issues': {
            'type': 'array',
            'items': {'type': 'string', 'maxLength': 100},
            'maxItems': 6,
        },
        'rewrite_instruction': {'type': 'string', 'maxLength': 400},
        'reason': {'type': 'string', 'maxLength': 240},
    },
    'required': ['valid', 'issues', 'rewrite_instruction', 'reason'],
    'additionalProperties': False,
}

CALENDAR_ACTION_SCHEMA = {
    'type': 'object',
    'properties': {
        'action': {
            'type': 'string',
            'enum': ['none', 'offer_alternatives', 'ask_confirmation', 'book'],
        },
        'consent': {
            'type': 'string',
            'enum': ['none', 'owner_proposed', 'offered_slot_selected', 'confirmed_exact'],
        },
        'start_at': {'type': ['string', 'null']},
        'end_at': {'type': ['string', 'null']},
        'reason': {'type': 'string', 'maxLength': 200},
    },
    'required': ['action', 'consent', 'start_at', 'end_at', 'reason'],
    'additionalProperties': False,
}
