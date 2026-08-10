"""S3-compatible object storage (Cloudflare R2) for catalog photos."""

from __future__ import annotations

import logging
import mimetypes
from functools import lru_cache
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def _clean_secret(raw: str | None) -> str:
    """Strip whitespace / wrapping quotes that break S3V4 signatures when pasted into Railway."""
    v = (raw or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    # Railway / dotenv sometimes keep a trailing newline or CR inside quotes.
    return v.replace("\r", "").replace("\n", "").strip()


def r2_configured() -> bool:
    return bool(
        _clean_secret(settings.r2_access_key_id)
        and _clean_secret(settings.r2_secret_access_key)
        and _clean_secret(settings.r2_bucket)
        and _clean_secret(settings.r2_endpoint)
    )


def _normalize_endpoint(raw: str) -> str:
    """
    Accept either ``https://<account>.r2.cloudflarestorage.com`` or the same with
    ``/<bucket>`` appended (Cloudflare bucket Settings UI).
    """
    endpoint = _clean_secret(raw).rstrip("/")
    bucket = _clean_secret(settings.r2_bucket).strip("/")
    if bucket and endpoint.endswith(f"/{bucket}"):
        endpoint = endpoint[: -(len(bucket) + 1)]
    return endpoint


def r2_client_error_hint(exc: BaseException) -> str | None:
    """Return a short operator-facing hint for common R2/boto auth failures."""
    name = type(exc).__name__
    msg = str(exc)
    code = None
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = resp.get("Error", {}).get("Code")
    blob = f"{name} {code or ''} {msg}".lower()
    if "signaturedoesnotmatch" in blob or "signature does not match" in blob:
        return (
            "R2 auth failed (SignatureDoesNotMatch): check Railway R2_ACCESS_KEY_ID + "
            "R2_SECRET_ACCESS_KEY are a matching pair from Cloudflare R2 → Manage R2 API Tokens "
            "(no extra quotes), and R2_ENDPOINT is https://<accountid>.r2.cloudflarestorage.com"
        )
    if "invalidaccesskeyid" in blob or ("access key" in blob and "invalid" in blob):
        return (
            "R2 access key rejected: recreate the R2 API token and update "
            "R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY on Railway"
        )
    if "nosuchbucket" in blob:
        return "R2 bucket not found: check R2_BUCKET matches the Cloudflare bucket name exactly"
    return None


@lru_cache(maxsize=1)
def _client() -> Any:
    if not r2_configured():
        raise RuntimeError("R2 is not configured")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("boto3 is required for R2 storage — pip install boto3") from exc

    # R2 needs path-style addressing. Newer boto3 default checksums break many S3-compatible APIs.
    cfg_kwargs: dict[str, Any] = {
        "signature_version": "s3v4",
        "s3": {"addressing_style": "path"},
    }
    try:
        cfg = Config(
            **cfg_kwargs,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
    except TypeError:
        cfg = Config(**cfg_kwargs)

    return boto3.client(
        "s3",
        endpoint_url=_normalize_endpoint(settings.r2_endpoint),
        aws_access_key_id=_clean_secret(settings.r2_access_key_id),
        aws_secret_access_key=_clean_secret(settings.r2_secret_access_key),
        region_name="auto",
        config=cfg,
    )


def _bucket() -> str:
    return _clean_secret(settings.r2_bucket)


def put_bytes(key: str, data: bytes, *, content_type: str | None = None) -> None:
    ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=ct)


def put_file(key: str, path: str, *, content_type: str | None = None) -> None:
    ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
    # put_object is more reliable with R2 than multipart upload_file for typical catalog JPEGs.
    with open(path, "rb") as f:
        _client().put_object(Bucket=_bucket(), Key=key, Body=f, ContentType=ct)


def presigned_get_url(key: str, *, expires_seconds: int = 3600) -> str:
    """Time-limited HTTPS URL so browsers can GET the object without proxying through Railway."""
    expires = max(60, min(int(expires_seconds), 24 * 3600))
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
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
        resp = getattr(exc, "response", None)
        code = None
        status = None
        if isinstance(resp, dict):
            code = resp.get("Error", {}).get("Code")
            status = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")
        msg = str(exc).lower()
        if (
            code in ("404", "NoSuchKey", "NotFound", "404 Not Found")
            or status == 404
            or "not found" in msg
            or "nosuchkey" in msg
        ):
            return False
        # Auth / config errors should not look like "missing"
        raise


def delete_object(key: str) -> None:
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("R2 delete_object failed for %s: %s", key, exc)


def list_keys(prefix: str, *, max_keys: int = 5000) -> list[str]:
    """List object keys under ``prefix`` (paged, capped)."""
    if not r2_configured():
        return []
    pref = (prefix or "").lstrip("/")
    out: list[str] = []
    token: str | None = None
    client = _client()
    while len(out) < max_keys:
        kwargs: dict[str, Any] = {
            "Bucket": _bucket(),
            "Prefix": pref,
            "MaxKeys": min(1000, max_keys - len(out)),
        }
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for item in resp.get("Contents") or []:
            key = item.get("Key")
            if isinstance(key, str) and key and not key.endswith("/"):
                out.append(key)
                if len(out) >= max_keys:
                    break
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return out


def ping() -> str:
    """Write and delete a tiny probe object. Returns 'ok' or raises with detail."""
    key = "_health/r2-ping.txt"
    put_bytes(key, b"ok", content_type="text/plain")
    delete_object(key)
    return "ok"


def put_bucket_cors(origins: list[str]) -> list[str]:
    """
    Allow browsers on ``origins`` to GET/HEAD objects (needed for fetch() of signed URLs).
    ``<img src>`` works without CORS; crop/rotate uses fetch and needs this.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in origins:
        o = (raw or "").strip().rstrip("/")
        if not o or "*" in o or o in seen:
            continue
        seen.add(o)
        cleaned.append(o)
    if not cleaned:
        raise ValueError("No AllowedOrigins to apply")
    _client().put_bucket_cors(
        Bucket=_bucket(),
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["GET", "HEAD"],
                    "AllowedOrigins": cleaned,
                    "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        },
    )
    return cleaned


def get_bucket_cors() -> list[dict[str, Any]]:
    """Return current CORS rules (empty list if none)."""
    try:
        resp = _client().get_bucket_cors(Bucket=_bucket())
    except Exception as exc:  # noqa: BLE001
        resp = getattr(exc, "response", None)
        code = None
        status = None
        if isinstance(resp, dict):
            code = resp.get("Error", {}).get("Code")
            status = resp.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("NoSuchCORSConfiguration", "NoSuchCORSConfig") or status == 404:
            return []
        raise
    rules = resp.get("CORSRules") or []
    return list(rules) if isinstance(rules, list) else []


def reset_client_cache() -> None:
    """Test helper — clear cached boto3 client."""
    _client.cache_clear()
