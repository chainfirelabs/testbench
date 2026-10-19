"""One recorded test's text output: uploaded once, read in a viewer, never downloaded.

The file lives beside the test's artifacts, at `tests/<test-id>/output/<filename>`.
A test has at most one; uploading again replaces it.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import TestOutput, User
from ..services.audit import log_action
from ..services.object_storage import OutputError
from ..services import test_artifact_storage as storage
from .deps import get_current_user, require_tests_edit
from .test_artifacts import get_test

router = APIRouter(prefix='/tests/{test_id}/output', tags=['test-output'])
logger = logging.getLogger(__name__)


def _meta(output: TestOutput | None) -> dict | None:
    if output is None:
        return None
    return {'filename': output.filename, 'size_bytes': output.size_bytes,
            'created_at': output.created_at, 'updated_at': output.updated_at}


def _output(db: Session, test_id: str) -> TestOutput | None:
    return db.scalar(select(TestOutput).where(TestOutput.test_id == test_id))


@router.get('')
def read_output(test_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The output and its text. A read failure is reported in `error`, not raised,
    so an editor can still replace or remove an output that no longer opens."""
    get_test(db, test_id)
    output = _output(db, test_id)
    text = error = None
    if output is not None:
        try:
            text = storage.read_output(output.bucket, output.object_key)
        except OutputError as exc:
            error = str(exc)
    return {'output': _meta(output), 'text': text, 'error': error,
            'storage_enabled': settings.object_storage_enabled,
            'max_bytes': settings.object_storage_max_bytes}


@router.put('')
def upload_output(test_id: str, file: UploadFile, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    get_test(db, test_id, lock=True)
    filename = storage.artifact_filename(file.filename or 'output.txt')
    key = storage.output_key(test_id, filename)
    bucket = settings.object_storage_bucket
    previous = _output(db, test_id)
    stale = (previous.bucket, previous.object_key) if previous is not None else None
    try:
        size = storage.upload_output(bucket, key, file.file)
    except OutputError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    output = previous or TestOutput(test_id=test_id)
    output.filename, output.size_bytes, output.bucket, output.object_key = filename, size, bucket, key
    output.created_by = user.id
    try:
        db.add(output)
        log_action(db, user, 'test.output.upload', 'test', test_id,
                   {'filename': filename, 'size_bytes': size}, request)
        db.commit()
    except Exception:
        db.rollback()
        if stale != (bucket, key):
            try:
                storage.delete_artifact(bucket, key)
            except OutputError:
                logger.exception('Could not remove output after failed metadata save: %s', test_id)
        raise
    # A replacement under another name leaves the old object behind otherwise.
    if stale is not None and stale != (bucket, key):
        try:
            storage.delete_artifact(*stale)
        except OutputError:
            logger.exception('Could not remove replaced output for test %s', test_id)
    db.refresh(output)
    return _meta(output)


@router.delete('', status_code=204)
def delete_output(test_id: str, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    get_test(db, test_id, lock=True)
    output = _output(db, test_id)
    if output is None:
        raise HTTPException(404, 'This test has no output')
    try:
        storage.delete_artifact(output.bucket, output.object_key)
    except OutputError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    log_action(db, user, 'test.output.delete', 'test', test_id, {'filename': output.filename}, request)
    db.delete(output)
    db.commit()
