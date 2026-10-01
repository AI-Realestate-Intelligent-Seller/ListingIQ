import json
import logging
import os
import contextvars
from uuid import uuid4

# Context variables for per-request logging
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('user_id', default='-')
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('session_id', default='-')

LOG_DATE_FORMAT = '%Y-%m-%dT%H:%M:%S%z'
APP_LOG_FORMAT = (
    '%(asctime)s %(levelname)s [%(name)s] '
    '[user=%(user_id)s session=%(session_id)s] %(message)s'
)

def setup_logging():
    level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    formatter = logging.Formatter(APP_LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    for handler in root.handlers:
        if not any(isinstance(existing, ContextFilter) for existing in handler.filters):
            handler.addFilter(ContextFilter())
        handler.setFormatter(formatter)

    # Uvicorn owns separate handlers for server and access logs. Preserve their
    # specialized fields while adding the same timestamp and visible level.
    from uvicorn.logging import AccessFormatter, DefaultFormatter

    uvicorn_formatters = {
        'uvicorn': DefaultFormatter(
            '%(asctime)s %(levelprefix)s %(message)s', datefmt=LOG_DATE_FORMAT
        ),
        'uvicorn.access': AccessFormatter(
            '%(asctime)s %(levelprefix)s %(client_addr)s - '
            '"%(request_line)s" %(status_code)s',
            datefmt=LOG_DATE_FORMAT,
        ),
    }
    for logger_name, uvicorn_formatter in uvicorn_formatters.items():
        target = logging.getLogger(logger_name)
        target.disabled = False
        target.setLevel(level)
        for handler in target.handlers:
            handler.setFormatter(uvicorn_formatter)

class ContextFilter(logging.Filter):
    def filter(self, record):
        try:
            record.user_id = user_id_var.get()
        except Exception:
            record.user_id = '-'
        try:
            record.session_id = session_id_var.get()
        except Exception:
            record.session_id = '-'
        return True

logging.getLogger().addFilter(ContextFilter())

def get_logger(name: str) -> logging.Logger:
    """Module logger that always carries the request context fields."""
    logger = logging.getLogger(name)
    if not any(isinstance(existing, ContextFilter) for existing in logger.filters):
        logger.addFilter(ContextFilter())
    return logger


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    """Write one searchable lifecycle record without multiline log fragments.

    Callers choose the fields deliberately, which keeps secrets and message
    bodies out of logs. JSON encoding preserves spaces and nested transition
    values while the stable ``event=`` prefix makes filtering straightforward.
    """
    parts = [f'event={event}']
    for key, value in fields.items():
        if value is None:
            continue
        rendered = json.dumps(value, default=str, ensure_ascii=False, separators=(',', ':'))
        rendered = rendered.replace(chr(10), '\\n').replace(chr(13), '\\r')
        parts.append(f'{key}={rendered}')
    logger.log(level, ' '.join(parts))


def set_request_context(user_id: str | None = None, session_id: str | None = None):
    if session_id:
        session_id_var.set(session_id)
    elif user_id is None:
        # Starting a request creates a correlation ID; adding its user later preserves it.
        session_id_var.set(uuid4().hex)
    if user_id:
        user_id_var.set(str(user_id))
    else:
        user_id_var.set('-')

