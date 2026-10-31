"""Private, append-only storage for original Persona source documents."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from ..core.config import BASE_DIR

logger = logging.getLogger(__name__)


class PersonaStorageError(RuntimeError):
    pass


def _safe_key(value: str) -> str:
    path = PurePosixPath(str(value or '').replace('\\', '/'))
    if path.is_absolute() or not path.parts or any(part in {'', '.', '..'} for part in path.parts):
        raise PersonaStorageError('Persona object key must be a safe relative path')
    return path.as_posix()


def _segment(value: object) -> str:
    clean = re.sub(r'[^A-Za-z0-9._=-]+', '-', str(value or 'unknown')).strip('-.')
    return clean[:180] or 'unknown'


def import_object_key(brokerage_id: str, user_id: int, job_id: str, suffix: str) -> str:
    extension = str(suffix or '').lower()
    if not re.fullmatch(r'\.(?:pdf|docx|txt|md|markdown)', extension):
        raise PersonaStorageError('Unsupported Persona source extension')
    return _safe_key(
        f'personas/brokerage-{_segment(brokerage_id)}/user-{_segment(user_id)}/'
        f'imports/{_segment(job_id)}/original{extension}'
    )


class LocalPersonaStorage:
    backend = 'local'

    def __init__(self, root: str | Path | None = None):
        configured = root or os.getenv('PERSONA_STORAGE_ROOT') or (BASE_DIR / 'data/personas')
        self.root = Path(configured).expanduser().resolve()

    def _path(self, key: str) -> Path:
        target = (self.root / _safe_key(key)).resolve()
        if not target.is_relative_to(self.root):
            raise PersonaStorageError('Persona object key escapes the local root')
        return target

    def write(self, key: str, content: bytes, media_type: str = '') -> str:
        del media_type
        safe = _safe_key(key)
        target = self._path(safe)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('xb') as output:
                output.write(content)
        except FileExistsError as error:
            raise PersonaStorageError(f'Persona source already exists: {safe}') from error
        return safe

    def read(self, key: str) -> bytes:
        target = self._path(key)
        if not target.is_file():
            raise FileNotFoundError(_safe_key(key))
        return target.read_bytes()

    def delete(self, key: str) -> None:
        """Remove an object whose database row was never committed."""
        self._path(key).unlink(missing_ok=True)


def _setting(persona_name: str, shared_name: str) -> str:
    """A Persona-only value, else the shared CLOUDFLARE_R2_* value."""
    return (os.getenv(persona_name, '').strip() or os.getenv(shared_name, '').strip())


def _r2_credentials() -> tuple[str, str]:
    """Persona R2 key pair.

    PERSONA_R2_CREDENTIALS_FILE names a private env file (chmod 600) holding
    CLOUDFLARE_R2_ACCESS_KEY_ID / CLOUDFLARE_R2_SECRET_ACCESS_KEY. Its values
    are read here only and never exported, so the BatchData R2 pair in the
    shared CLOUDFLARE_R2_* variables is left untouched. When the file is set it
    is the only source: a missing or unsafe file is an error, not a fallback.
    """
    path_value = os.getenv('PERSONA_R2_CREDENTIALS_FILE', '').strip()
    if not path_value:
        return (_setting('PERSONA_R2_ACCESS_KEY_ID', 'CLOUDFLARE_R2_ACCESS_KEY_ID'),
                _setting('PERSONA_R2_SECRET_ACCESS_KEY', 'CLOUDFLARE_R2_SECRET_ACCESS_KEY'))
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise PersonaStorageError('PERSONA_R2_CREDENTIALS_FILE does not exist')
    if path.stat().st_mode & 0o077:
        raise PersonaStorageError('PERSONA_R2_CREDENTIALS_FILE must not be readable by group or others')
    from dotenv import dotenv_values
    values = dotenv_values(path)
    return (str(values.get('CLOUDFLARE_R2_ACCESS_KEY_ID') or '').strip(),
            str(values.get('CLOUDFLARE_R2_SECRET_ACCESS_KEY') or '').strip())


class R2PersonaStorage:
    backend = 'r2'

    def __init__(self, client=None):
        # PERSONA_R2_* lets Persona originals use their own bucket and key pair
        # without changing the shared CLOUDFLARE_R2_* values BatchData uses.
        self.bucket = _setting('PERSONA_R2_BUCKET', 'CLOUDFLARE_R2_BUCKET')
        self.prefix = os.getenv('PERSONA_R2_PREFIX', '').strip().strip('/')
        endpoint = _setting('PERSONA_R2_ENDPOINT', 'CLOUDFLARE_R2_ENDPOINT')
        access_key, secret_key = _r2_credentials() if client is None else ('', '')
        if not self.bucket:
            raise PersonaStorageError('CLOUDFLARE_R2_BUCKET is required for Persona R2 storage')
        if client is None:
            if not endpoint or not access_key or not secret_key:
                raise PersonaStorageError('R2 endpoint, access key ID and secret access key are required')
            try:
                import boto3
            except ImportError as error:
                raise PersonaStorageError('boto3 is required for Persona R2 storage') from error
            parsed = urlsplit(endpoint)
            if parsed.path.strip('/') == self.bucket:
                endpoint = urlunsplit((parsed.scheme, parsed.netloc, '', parsed.query, parsed.fragment))
            client = boto3.client(
                's3', endpoint_url=endpoint, aws_access_key_id=access_key,
                aws_secret_access_key=secret_key, region_name='auto',
            )
        self.client = client

    def _object_key(self, key: str) -> str:
        safe = _safe_key(key)
        return f'{self.prefix}/{safe}' if self.prefix else safe

    def write(self, key: str, content: bytes, media_type: str = '') -> str:
        safe = _safe_key(key)
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=self._object_key(safe), Body=content,
                ContentType=media_type or mimetypes.guess_type(safe)[0] or 'application/octet-stream',
            )
        except Exception as error:
            raise PersonaStorageError('Unable to store the private Persona source document') from error
        return safe

    def read(self, key: str) -> bytes:
        safe = _safe_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._object_key(safe))
            return response['Body'].read()
        except Exception as error:
            details = getattr(error, 'response', {}).get('Error', {})
            if str(details.get('Code')) in {'404', 'NoSuchKey', 'NotFound'}:
                raise FileNotFoundError(safe) from error
            raise PersonaStorageError('Unable to read the private Persona source document') from error

    def delete(self, key: str) -> None:
        """Remove an object whose database row was never committed."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._object_key(_safe_key(key)))
        except Exception as error:
            raise PersonaStorageError('Unable to remove the private Persona source document') from error


def storage_for(backend: str):
    """The storage that holds objects written with ``backend``, whatever is configured now."""
    backend = str(backend or '').strip().lower()
    if backend == 'local':
        return LocalPersonaStorage()
    if backend == 'r2':
        return R2PersonaStorage()
    raise PersonaStorageError("PERSONA_STORAGE_BACKEND must be 'local' or 'r2'")


def configured_storage():
    return storage_for(os.getenv('PERSONA_STORAGE_BACKEND', 'local'))


def keep_database_copy(backend: str) -> bool:
    """Whether the upload is also kept in persona_import_jobs.document_bytes.

    Local disk is not durable on hosts such as Render, so a local-backend upload
    always keeps its database copy and the file on disk is only a working copy.
    R2 is the durable store; a database copy is optional
    (``PERSONA_STORAGE_DB_COPY=true``).
    """
    if backend != 'r2':
        return True
    return os.getenv('PERSONA_STORAGE_DB_COPY', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def expected_key_prefix(job) -> str:
    return (f'personas/brokerage-{_segment(job.brokerage_id)}/user-{_segment(job.owner_user_id)}/'
            f'imports/{_segment(job.id)}/')


def _matches(job, content: bytes) -> bool:
    expected = getattr(job, 'file_hash', None)
    return not expected or hashlib.sha256(content).hexdigest() == expected


def read_original(job) -> bytes:
    """Return the job's original upload, verified against its recorded SHA-256.

    Object storage is read through the backend the job was written with. The
    key must sit under this job's own owner/brokerage/job prefix, so a row can
    never be pointed at another user's object. Database bytes (all imports
    before object storage, and local-backend uploads) remain a fallback.
    """
    key = getattr(job, 'storage_key', None)
    backend = getattr(job, 'storage_backend', None) or 'database'
    if key and backend != 'database':
        if not _safe_key(key).startswith(expected_key_prefix(job)):
            raise PersonaStorageError('Persona source object does not belong to this import')
        try:
            content = storage_for(backend).read(key)
            if _matches(job, content):
                return content
            logger.error('[persona-storage] checksum mismatch job_id=%s backend=%s', job.id, backend)
        except (FileNotFoundError, PersonaStorageError) as error:
            logger.warning('[persona-storage] object read failed job_id=%s backend=%s error=%s',
                           job.id, backend, type(error).__name__)
    content = getattr(job, 'document_bytes', None)
    if content is None or not _matches(job, bytes(content)):
        raise PersonaStorageError('Persona source document is unavailable')
    return bytes(content)
