"""S3-compatible object storage (Cloudflare R2) for catalog photos."""

from __future__ import annotations

import logging
import mimetypes
from functools import lru_cache
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def r2_configured() -> bool:
    return bool(
        (settings.r2_access_key_id or "").strip()
        and (settings.r2_secret_access_key or "").strip()
        and (settings.r2_bucket or "").strip()
        and (settings.r2_endpoint or "").strip()
    )


def _normalize_endpoint(raw: str) -> str:
    """
    Accept either ``https://<account>.r2.cloudflarestorage.com`` or the same with
    ``/<bucket>`` appended (Cloudflare bucket Settings UI).
    """
    endpoint = (raw or "").strip().rstrip("/")
    bucket = (settings.r2_bucket or "").strip().strip("/")
    if bucket and endpoint.endswith(f"/{bucket}"):
        endpoint = endpoint[: -(len(bucket) + 1)]
    return endpoint


@lru_cache(maxsize=1)
def _client() -> Any:
    if not r2_configured():
        raise RuntimeError("R2 is not configured")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("boto3 is required for R2 storage — pip install boto3") from exc

    return boto3.client(
        "s3",
        endpoint_url=_normalize_endpoint(settings.r2_endpoint),
        aws_access_key_id=settings.r2_access_key_id.strip(),
        aws_secret_access_key=settings.r2_secret_access_key.strip(),
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def _bucket() -> str:
    return (settings.r2_bucket or "").strip()


def put_bytes(key: str, data: bytes, *, content_type: str | None = None) -> None:
    ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=ct)


def put_file(key: str, path: str, *, content_type: str | None = None) -> None:
    ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    _client().upload_file(
        path,
        _bucket(),
        key,
        ExtraArgs={"ContentType": ct},
    )


def get_bytes(key: str) -> bytes:
    resp = _client().get_object(Bucket=_bucket(), Key=key)
    body = resp["Body"].read()
    return body if isinstance(body, (bytes, bytearray)) else bytes(body)


def download_file(key: str, dest_path: str) -> None:
    _client().download_file(_bucket(), key, dest_path)


def object_exists(key: str) -> bool:
    try:
        _client().head_object(Bucket=_bucket(), Key=key)
        return True
    except Exception as exc:  # noqa: BLE001 — botocore ClientError and others
        code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
        # 404 / NotFound / NoSuchKey
        msg = str(exc).lower()
        if code in ("404", "NoSuchKey", "NotFound") or "not found" in msg or "404" in msg:
            return False
        logger.warning("R2 head_object failed for %s: %s", key, exc)
        return False


def delete_object(key: str) -> None:
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("R2 delete_object failed for %s: %s", key, exc)


def reset_client_cache() -> None:
    """Test helper — clear cached boto3 client."""
    _client.cache_clear()
