"""Thin Lofty Open API transport. The credential is never logged or returned.

Only reads are retried inside one request. A write that times out is reported
as ``ambiguous`` so the caller can check Lofty before trying it again.
"""

from __future__ import annotations

from typing import Any

import requests

from ..core.config import settings
from ..logger import get_logger, log_event
from .auth import LoftyCredentials

logger = get_logger(__name__)

READ_ATTEMPTS = 2
# Lofty's web firewall (OpsAny) rejects the default ``python-requests/x.y``
# User-Agent with an HTML 403, while the same request with any other agent is
# accepted. Identify this integration honestly instead of using the library default.
USER_AGENT = 'ListingIQ/1.0 (Lofty integration)'


class LoftyError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None,
                 ambiguous: bool = False):
        super().__init__(message)
        self.status_code = status_code
        # True when Lofty may have applied a write we did not get an answer for.
        self.ambiguous = ambiguous


def _lead_id(value: Any) -> str | None:
    if isinstance(value, bool) or value in (None, ''):
        return None
    text = str(value).strip()
    return text if text.isdigit() else None


def _unwrap(body: Any) -> Any:
    """Lofty sometimes wraps a payload in ``data``; accept both shapes."""
    if isinstance(body, dict) and isinstance(body.get('data'), (dict, list)):
        return body['data']
    return body


class LoftyClient:
    def __init__(self, credentials: LoftyCredentials, *, base_url: str | None = None,
                 timeout: float | None = None):
        self.credentials = credentials
        self.base_url = (base_url or settings['lofty']['base_url']).rstrip('/')
        self.timeout = timeout or settings['lofty']['timeout_seconds']

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: dict | None = None) -> Any:
        attempts = READ_ATTEMPTS if method == 'GET' else 1
        last_error: LoftyError | None = None
        for _ in range(attempts):
            try:
                response = requests.request(
                    method,
                    self.base_url + path,
                    params=params,
                    json=body,
                    headers={
                        'Authorization': self.credentials.authorization_header(),
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'User-Agent': USER_AGENT,
                    },
                    timeout=(5, self.timeout),
                    allow_redirects=False,
                )
            except requests.RequestException as error:
                last_error = LoftyError(f'Lofty could not be reached ({type(error).__name__}).',
                                        ambiguous=method != 'GET')
                continue
            log_event(logger, 'lofty.request', method=method, path=path.split('?')[0],
                      status=response.status_code)
            if response.status_code >= 500 and method == 'GET':
                last_error = LoftyError(f'Lofty returned HTTP {response.status_code}.',
                                        status_code=response.status_code)
                continue
            if not response.ok:
                self._log_failure(method, path, params, body, response)
                raise LoftyError(self._error_detail(response), status_code=response.status_code,
                                 ambiguous=response.status_code >= 500)
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                raise LoftyError('Lofty returned a response that is not JSON.',
                                 status_code=response.status_code)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _log_failure(method: str, path: str, params: dict | None, body: dict | None,
                     response) -> None:
        """TEMPORARY diagnostics for failed calls. Names and shapes only:
        no key, header values, phone numbers, emails or note text."""
        sent = response.request.headers if getattr(response, 'request', None) is not None else {}
        log_event(
            logger, 'lofty.request.failed', method=method, path=path,
            status=response.status_code,
            response_content_type=response.headers.get('content-type'),
            param_names=sorted((params or {}).keys()),
            body_fields=sorted((body or {}).keys()),
            header_names=sorted(sent.keys()),
            user_agent=sent.get('User-Agent'),
            authorization_scheme=str(sent.get('Authorization') or '').split(' ', 1)[0] or None,
        )

    @staticmethod
    def _error_detail(response) -> str:
        if 'html' in str(response.headers.get('content-type') or '').lower():
            return (f'Lofty\'s firewall rejected the request (HTTP {response.status_code}). '
                    'Try again; if it persists, contact Lofty support.')
        try:
            body = response.json()
        except ValueError:
            body = None
        message = None
        if isinstance(body, dict):
            message = body.get('message') or body.get('errorMessage') or body.get('error')
        message = str(message or response.text or '').strip()[:300]
        return f'Lofty returned HTTP {response.status_code}' + (f': {message}' if message else '.')

    # -- leads -----------------------------------------------------------

    def find_leads(self, *, phone: str | None = None, email: str | None = None) -> list[str]:
        """Lead IDs matching this exact phone or email (Lofty precise search)."""
        params: dict[str, Any] = {'limit': 10}
        if phone:
            params['phone'] = phone
        elif email:
            params['email'] = email
        else:
            return []
        body = _unwrap(self._request('GET', '/v1.0/leads', params=params))
        rows = body.get('leads') if isinstance(body, dict) else body
        ids = []
        for row in rows or []:
            lead_id = _lead_id(row.get('leadId') if isinstance(row, dict) else None)
            if lead_id and lead_id not in ids:
                ids.append(lead_id)
        return ids

    def create_lead(self, payload: dict) -> str:
        body = _unwrap(self._request('POST', '/v1.0/leads', body=payload))
        lead_id = _lead_id(body.get('leadId') if isinstance(body, dict) else None)
        if not lead_id:
            raise LoftyError('Lofty accepted the lead but returned no lead ID.', ambiguous=True)
        return lead_id

    def update_lead(self, lead_id: str, payload: dict) -> None:
        self._request('PUT', f'/v1.0/leads/{lead_id}', body=payload)

    # -- Sales Agent (Anna) ----------------------------------------------

    def mute_sales_agent(self, lead_id: str) -> None:
        # No body, but Lofty still expects the JSON content type header.
        self._request('PUT', f'/v2.0/sales-agents/working-lead/{lead_id}/mute')

    def sales_agent_muted(self, lead_id: str) -> bool:
        body = self._request('GET', '/v2.0/sales-agent/lead/mute-status',
                             params={'leadId': lead_id})
        data = _unwrap(body)
        value = data.get('mute') if isinstance(data, dict) else None
        if value is None and isinstance(body, dict):
            value = body.get('mute')
        return value is True or str(value).strip().lower() == 'true'

    # -- notes -------------------------------------------------------------

    def create_note(self, lead_id: str, content: str) -> str | None:
        body = _unwrap(self._request('POST', '/v1.0/notes', body={
            'leadId': int(lead_id), 'content': content, 'isPin': False,
        }))
        note_id = body.get('noteId') if isinstance(body, dict) else None
        return str(note_id) if note_id is not None else None

    def list_notes(self, lead_id: str) -> list[dict]:
        body = _unwrap(self._request('GET', '/v1.0/notes', params={'leadId': lead_id}))
        rows = body.get('notes') if isinstance(body, dict) else body
        return [row for row in rows or [] if isinstance(row, dict)]
