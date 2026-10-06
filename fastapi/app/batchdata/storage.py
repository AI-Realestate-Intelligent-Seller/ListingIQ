"""Append-only local or R2 archives without database schema changes."""

import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit


class StorageConfigurationError(RuntimeError):
    pass


class ArchiveCollisionError(RuntimeError):
    pass


def _safe_key(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise StorageConfigurationError(
            "BatchData archive key must be a safe relative path"
        )
    return path.as_posix()


def _segment(value: object) -> str:
    clean = re.sub(r"[^A-Za-z0-9._=-]+", "-", str(value or "unknown")).strip("-.")
    return clean[:180] or "unknown"


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _dataset_parts(value):
    parts = [
        _segment(part)
        for part in str(value or "unknown").replace("\\", "/").split("/")
        if part
    ]
    return [f"dataset={parts[0]}", *parts[1:]]


def run_archive_key(mode, run_id, product, name, extension="json", occurred_at=None):
    current = _utc(occurred_at)
    return _safe_key(
        "/".join(
            (
                f"mode={_segment(mode)}",
                "fetches",
                f"fetch-date={current.date().isoformat()}",
                f"run={_segment(run_id)}",
                *_dataset_parts(product),
                f"{_segment(name)}.{_segment(extension)}",
            )
        )
    )


def property_archive_key(
    user_id,
    session_id,
    category,
    property_id,
    name,
    extension="json",
    occurred_at=None,
):
    """Return the canonical per-property archive key.

    R2 has no real directories; these segments are deliberately ordered for
    date/user/session browsing while keeping every artifact for one property
    together.
    """
    current = _utc(occurred_at)
    return _safe_key(
        "/".join(
            (
                f"fetch-date={current.date().isoformat()}",
                f"user={_segment(user_id)}",
                f"time={current.strftime('%H-%M-%S')}",
                f"session={_segment(session_id)}",
                "properties",
                f"category={_segment(category)}",
                f"property={_segment(property_id)}",
                f"{_segment(name)}.{_segment(extension)}",
            )
        )
    )


def webhook_archive_key(
    mode, classification, event_key, delivery_id, name, occurred_at=None
):
    current = _utc(occurred_at)
    return _safe_key(
        "/".join(
            (
                f"mode={_segment(mode)}",
                "webhooks",
                f"received-date={current.date().isoformat()}",
                f"classification={_segment(classification)}",
                f"event={_segment(event_key)}",
                f"delivery={_segment(delivery_id)}",
                f"{_segment(name)}.json",
            )
        )
    )


class LocalStorage:
    backend = "local"

    def __init__(self, root=None):
        self.root = Path(
            root
            or os.getenv("BATCHDATA_STORAGE_ROOT", "storage/integrations/batchdata")
        ).resolve()

    def write(self, key, content):
        safe_key = _safe_key(key)
        target = (self.root / safe_key).resolve()
        if not target.is_relative_to(self.root):
            raise StorageConfigurationError(
                "BatchData archive key escapes the local root"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as output:
                output.write(content)
        except FileExistsError as error:
            raise ArchiveCollisionError(
                f"BatchData archive already exists: {safe_key}"
            ) from error
        return safe_key

    def read(self, key):
        safe_key = _safe_key(key)
        target = (self.root / safe_key).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise FileNotFoundError(safe_key)
        return target.read_bytes()


class R2Storage:
    backend = "r2"

    def __init__(self, client=None):
        self.bucket = os.getenv("CLOUDFLARE_R2_BUCKET", "").strip()
        self.prefix = os.getenv("BATCHDATA_R2_PREFIX", "").strip().strip("/")
        self.legacy_prefix = os.getenv(
            "BATCHDATA_R2_LEGACY_PREFIX", "batchdata"
        ).strip().strip("/")
        endpoint = os.getenv("CLOUDFLARE_R2_ENDPOINT", "").strip()
        access_key = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "").strip()
        secret_key = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "").strip()
        if not self.bucket:
            raise StorageConfigurationError(
                "CLOUDFLARE_R2_BUCKET is required for R2 storage"
            )
        if client is None:
            if not endpoint or not access_key or not secret_key:
                raise StorageConfigurationError(
                    "R2 endpoint, access key ID and secret access key are required"
                )
            try:
                import boto3
            except ImportError as error:
                raise StorageConfigurationError(
                    "boto3 is required for BatchData R2 storage"
                ) from error
            # Cloudflare's S3 endpoint is account-level. Older local config
            # included /<bucket>, which made boto3 encode the bucket a second
            # time as the first object-key segment.
            parsed = urlsplit(endpoint)
            if parsed.path.strip("/") == self.bucket:
                endpoint = urlunsplit(
                    (parsed.scheme, parsed.netloc, "", parsed.query, parsed.fragment)
                )
            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name="auto",
            )
        self.client = client

    def _object_key(self, key):
        safe_key = _safe_key(key)
        return f"{self.prefix}/{safe_key}" if self.prefix else safe_key

    def write(self, key, content):
        safe_key = _safe_key(key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._object_key(safe_key),
                Body=content,
                ContentType=mimetypes.guess_type(safe_key)[0]
                or "application/octet-stream",
                IfNoneMatch="*",
            )
        except Exception as error:
            response = getattr(error, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"PreconditionFailed", "412"} or status == 412:
                raise ArchiveCollisionError(
                    f"BatchData archive already exists: {safe_key}"
                ) from error
            raise
        return safe_key

    def read(self, key):
        safe_key = _safe_key(key)
        canonical = self._object_key(safe_key)
        legacy_prefixed = (
            f"{self.legacy_prefix}/{safe_key}" if self.legacy_prefix else safe_key
        )
        candidates = list(
            dict.fromkeys(
                (
                    canonical,
                    legacy_prefixed,
                    f"{self.bucket}/{legacy_prefixed}",
                    f"{self.bucket}/{canonical}",
                )
            )
        )
        last_missing = None
        for object_key in candidates:
            try:
                response = self.client.get_object(
                    Bucket=self.bucket, Key=object_key
                )
                return response["Body"].read()
            except Exception as error:
                details = getattr(error, "response", {}).get("Error", {})
                if str(details.get("Code")) in {"404", "NoSuchKey", "NotFound"}:
                    last_missing = error
                    continue
                raise
        raise FileNotFoundError(safe_key) from last_missing


def configured_storage():
    backend = os.getenv("BATCHDATA_STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        return LocalStorage()
    if backend == "r2":
        return R2Storage()
    raise StorageConfigurationError(
        "BATCHDATA_STORAGE_BACKEND must be 'local' or 'r2'"
    )


def read_with_fallback(key):
    primary = configured_storage()
    try:
        return primary.read(key)
    except FileNotFoundError:
        if primary.backend == "r2":
            return LocalStorage().read(key)
        raise


def json_bytes(value):
    return json.dumps(value, indent=2, sort_keys=True, default=str).encode("utf-8")
