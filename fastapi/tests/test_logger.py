import logging
import re

from app.logger import (
    APP_LOG_FORMAT,
    ContextFilter,
    LOG_DATE_FORMAT,
    get_logger,
    log_event,
    session_id_var,
    set_request_context,
    user_id_var,
)


def test_log_event_is_searchable_and_single_line(caplog):
    logger = get_logger('tests.structured_logging')
    caplog.set_level(logging.INFO)

    log_event(
        logger,
        'sms.conversation.state_updated',
        conversation_id=42,
        transitions={'lead_status': {'from': 'processing', 'to': 'interested'}},
        note='first line\nsecond line',
    )

    message = caplog.records[-1].getMessage()
    assert message.startswith('event=sms.conversation.state_updated ')
    assert 'conversation_id=42' in message
    assert '"from":"processing"' in message
    assert '\\n' in message
    assert '\n' not in message


def test_adding_user_context_preserves_request_session_id():
    set_request_context(session_id='request-123')
    set_request_context(user_id='42')

    assert session_id_var.get() == 'request-123'
    assert user_id_var.get() == '42'


def test_formatter_includes_time_level_logger_and_context():
    record = logging.LogRecord(
        'tests.warning', logging.WARNING, __file__, 1, 'check warning', (), None
    )
    ContextFilter().filter(record)
    rendered = logging.Formatter(
        APP_LOG_FORMAT, datefmt=LOG_DATE_FORMAT
    ).format(record)

    assert re.match(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4} WARNING ',
        rendered,
    )
    assert '[tests.warning]' in rendered
    assert 'check warning' in rendered
