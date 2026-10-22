"""Test artifact and output storage. Keys are owned by TestBench, never supplied by clients.

Everything a test stores lives under `tests/<test-id>/`: artifacts beside each
other under `artifacts/`, and the test's one output file under `output/`.

A test that belongs to a test run stores its files inside the run's folder
instead, beside the files the run shares with all of its tests:

    test-runs/<run-name>_<run-id>/artifacts/<artifact-id>/<file>    shared
    test-runs/<run-name>_<run-id>/tests/<test-id>/artifacts/…       the test's own
    test-runs/<run-name>_<run-id>/tests/<test-id>/output/<file>

The run's folder is named when the run is created (`TestRun.storage_folder`),
so renaming the run later does not split its files across two folders.

The folder is chosen when a file is uploaded. The database records each
file's full key, so a test that later moves to another run keeps reading its
earlier files from where they are; nothing is ever moved.
"""
import re
from typing import BinaryIO

from botocore.exceptions import BotoCoreError, ClientError

from ..config import settings
from .object_storage import OutputError, ascii_text, object_location, object_storage_client


def artifact_filename(value: str | None) -> str:
    name = (value or 'artifact').replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip()[:200]
    return name if name and name not in {'.', '..'} else 'artifact'


def _basename(filename: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', '_', filename)[:200]


def run_folder(run_id: str, run_name: str) -> str:
    """A new run's folder, named so a person browsing the bucket can find it.

    The id keeps it unique; the name is only there to be read.
    """
    label = re.sub(r'[^A-Za-z0-9._-]+', '-', run_name or '').strip('-._')[:60] or 'run'
    return f'test-runs/{label}_{run_id}'


def _test_folder(test_id: str, run_folder: str | None) -> str:
    return f'{run_folder}/tests/{test_id}' if run_folder else f'tests/{test_id}'


def artifact_key(test_id: str, artifact_id: str, filename: str, run_folder: str | None = None) -> str:
    """`run_folder` is the storage folder of the test's run, when it has one."""
    return f'{_test_folder(test_id, run_folder)}/artifacts/{artifact_id}/{_basename(filename)}'


def output_key(test_id: str, filename: str, run_folder: str | None = None) -> str:
    return f'{_test_folder(test_id, run_folder)}/output/{_basename(filename)}'


def run_artifact_key(run_folder: str, artifact_id: str, filename: str) -> str:
    return f'{run_folder}/artifacts/{artifact_id}/{_basename(filename)}'


def storage_client(bucket: str):
    if not settings.object_storage_enabled:
        raise OutputError('Object storage is disabled', 503)
    if not settings.object_storage_bucket:
        raise OutputError('Object storage bucket is not configured', 503)
    if bucket != settings.object_storage_bucket:
        raise OutputError('Stored file does not use the configured bucket', 503)
    object_location(f's3://{bucket}/artifact')
    return object_storage_client()


def storage_error(exc: Exception, what: str = 'Artifact') -> OutputError:
    if isinstance(exc, ClientError):
        code = exc.response.get('Error', {}).get('Code', '')
        if code in {'NoSuchKey', 'NoSuchBucket', '404', 'NotFound'}:
            return OutputError(f'{what} file was not found', 404)
    return OutputError(f'Could not access {what.lower()} storage', 502)


def upload_artifact(bucket: str, key: str, file: BinaryIO, content_type: str) -> int:
    limit = settings.artifact_max_bytes
    if limit < 1:
        raise OutputError('Artifact size limit is invalid', 503)
    # UploadFile is already spooled to disk for large files; count without copying
    # the file into memory. Check actual bytes rather than a client-supplied size.
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > limit:
        raise OutputError(f'Artifact exceeds the {limit} byte limit', 413)
    client = storage_client(bucket)
    try:
        client.put_object(Bucket=bucket, Key=key, Body=file,
                          ContentLength=size, ContentType=content_type)
    except (ClientError, BotoCoreError) as exc:
        raise storage_error(exc) from exc
    return size


def download_artifact(bucket: str, key: str):
    client = storage_client(bucket)
    try:
        return client.get_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError) as exc:
        raise storage_error(exc) from exc


def delete_artifact(bucket: str, key: str) -> None:
    client = storage_client(bucket)
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError) as exc:
        raise storage_error(exc) from exc


def output_limit() -> int:
    limit = settings.object_storage_max_bytes
    if limit < 1:
        raise OutputError('Output size limit is invalid', 503)
    return limit


def upload_output(bucket: str, key: str, file: BinaryIO) -> int:
    """Store a test's output, refusing anything the viewer could not show.

    Checked on the way in rather than when read, so an output that was accepted
    always opens.
    """
    limit = output_limit()
    file.seek(0)
    data = file.read(limit + 1)
    if len(data) > limit:
        raise OutputError(f'Output exceeds the {limit} byte limit', 413)
    ascii_text(data)
    client = storage_client(bucket)
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentLength=len(data),
                          ContentType='text/plain; charset=us-ascii')
    except (ClientError, BotoCoreError) as exc:
        raise storage_error(exc, 'Output') from exc
    return len(data)


def read_output(bucket: str, key: str) -> str:
    limit = output_limit()
    client = storage_client(bucket)
    try:
        body = client.get_object(Bucket=bucket, Key=key)['Body']
        try:
            # The object can change after upload. Never consume more than limit + 1.
            data = body.read(limit + 1)
        finally:
            body.close()
    except (ClientError, BotoCoreError) as exc:
        raise storage_error(exc, 'Output') from exc
    if len(data) > limit:
        raise OutputError(f'Output exceeds the {limit} byte limit', 413)
    return ascii_text(data)
