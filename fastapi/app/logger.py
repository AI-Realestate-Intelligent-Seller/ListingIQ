import logging
import os
import contextvars
from uuid import uuid4

# Context variables for per-request logging
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('user_id', default='-')
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar('session_id', default='-')

def setup_logging():
    level = os.getenv('LOG_LEVEL', 'INFO').upper()
    fmt = '%(asctime)s %(levelname)s [user=%(user_id)s session=%(session_id)s] %(message)s'
    logging.basicConfig(level=level, format=fmt)

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

def set_request_context(user_id: str | None = None, session_id: str | None = None):
    if session_id:
        session_id_var.set(session_id)
    else:
        session_id_var.set(uuid4().hex)
    if user_id:
        user_id_var.set(str(user_id))
    else:
        user_id_var.set('-')

