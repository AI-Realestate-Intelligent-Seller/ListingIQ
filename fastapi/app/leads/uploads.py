"""Staging for lead-list uploads.

Two things happen here. First, an upload is streamed to disk in chunks and the
size ceiling is enforced *while* streaming, so an oversized file is rejected
without ever being held in memory. Second, a previewed file is kept for a short
while under a token, so confirming the import does not mean uploading a large
spreadsheet a second time.
"""

from __future__ import annotations

import re
import secrets
import time
from pathlib import Path

from ..core.config import load_config

CHUNK_BYTES = 1024 * 1024


class UploadTooLarge(Exception):
    """The upload exceeded the configured ceiling."""


def _settings() -> dict:
    return load_config()['leads']


def max_bytes() -> int:
    return _settings()['upload_max_mb'] * 1024 * 1024


def staging_dir() -> Path:
    path = Path(_settings()['staging_dir'])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(filename: str) -> str:
    """A filename we are willing to put on disk, extension preserved."""
    stem = re.sub(r'[^a-z0-9._-]+', '_', (filename or 'upload').lower())[-80:]
    return stem or 'upload.csv'


def prune(now: float | None = None) -> int:
    """Delete staged files past their TTL. Cheap enough to run on every upload."""
    ttl = _settings()['staging_ttl_minutes'] * 60
    cutoff = (now or time.time()) - ttl
    removed = 0
    for path in staging_dir().glob('*'):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:  # pragma: no cover - a concurrent prune already won
            continue
    return removed


async def stage(upload, user_id: int) -> tuple[str, Path]:
    """Stream an UploadFile to disk. Returns (token, path).

    Raises UploadTooLarge once the ceiling is crossed, having written nothing
    further — the partial file is removed before the error propagates.
    """
    prune()
    token = f'{user_id}-{secrets.token_urlsafe(16)}'
    path = staging_dir() / f'{token}__{_safe_name(upload.filename or "")}'
    ceiling = max_bytes()
    written = 0

    try:
        with path.open('wb') as handle:
            while True:
                chunk = await upload.read(CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > ceiling:
                    raise UploadTooLarge()
                handle.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise

    if not written:
        path.unlink(missing_ok=True)
        raise ValueError('The file is empty.')
    return token, path


def resolve(token: str, user_id: int) -> Path | None:
    """The staged file for this token, if it exists and belongs to this user."""
    if not token or not token.startswith(f'{user_id}-'):
        return None
    # The token is the filename prefix, so no path traversal is possible.
    if not re.fullmatch(r'\d+-[A-Za-z0-9_-]{10,64}', token):
        return None
    for path in staging_dir().glob(f'{token}__*'):
        if path.is_file():
            return path
    return None


def discard(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)
