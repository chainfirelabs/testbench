"""Access to an operator-owned S3-compatible object store."""

import re
from urllib.parse import unquote, urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ..config import settings


class OutputError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def object_location(value: str) -> tuple[str, str]:
    """Accept only canonical s3://bucket/key references, never arbitrary URLs."""
    try:
        url = urlsplit(value)
    except ValueError as exc:
        raise OutputError("Output must be an s3://bucket/key URL") from exc
    if url.scheme != "s3" or not url.netloc or not url.path.startswith("/"):
        raise OutputError("Output must be an s3://bucket/key URL")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{1,61}[A-Za-z0-9]", url.netloc)
            or url.query or url.fragment):
        raise OutputError("Output must be an s3://bucket/key URL without credentials or query parameters")
    key = unquote(url.path[1:])
    if not key or any(ord(char) < 32 for char in key):
        raise OutputError("Output object key is invalid")
    return url.netloc, key


def ascii_text(data: bytes) -> str:
    """Decode stored output, accepting only ASCII text with ordinary whitespace."""
    try:
        result = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise OutputError("Output file must contain only ASCII text") from exc
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in result) or "\x7f" in result:
        raise OutputError("Output file must contain only ASCII text")
    return result


def object_storage_client():
    """Build a client for the configured private S3-compatible service."""
    try:
        endpoint = urlsplit(settings.object_storage_url)
    except ValueError as exc:
        raise OutputError("Object storage endpoint is not configured", 503) from exc
    if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise OutputError("Object storage endpoint is not configured", 503)
    if not settings.object_storage_access_key or not settings.object_storage_secret_key:
        raise OutputError("Object storage credentials are not configured", 503)
    return boto3.client(
        "s3", endpoint_url=settings.object_storage_url,
        aws_access_key_id=settings.object_storage_access_key,
        aws_secret_access_key=settings.object_storage_secret_key,
        region_name=settings.object_storage_region,
        config=Config(s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=15, retries={"max_attempts": 1}),
    )
