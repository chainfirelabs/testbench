"""Upload, list and download files associated with one recorded test."""
import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..config import settings
from ..db import get_db, new_uuid
from ..models import Test, TestArtifact, TestRunArtifact, User
from ..schemas import TestArtifactOut, TestRunArtifactOut
from ..services.audit import log_action
from ..services.object_storage import OutputError
from ..services import test_artifact_storage as storage
from .deps import get_current_user, require_tests_edit

router = APIRouter(prefix='/tests/{test_id}/artifacts', tags=['test-artifacts'])
logger = logging.getLogger(__name__)


def get_test(db: Session, test_id: str, *, lock: bool = False) -> Test:
    query = select(Test).where(Test.id == test_id)
    if lock:
        query = query.with_for_update(of=Test)
    test = db.scalar(query)
    if test is None:
        raise HTTPException(404, 'Test not found')
    return test


def run_folder(test: Test) -> str | None:
    """A test in a run keeps its files inside the run's folder."""
    return test.test_run.storage_folder if test.test_run else None


def get_artifact(db: Session, test_id: str, artifact_id: str) -> TestArtifact:
    artifact = db.scalar(select(TestArtifact).where(
        TestArtifact.id == artifact_id, TestArtifact.test_id == test_id))
    if artifact is None:
        raise HTTPException(404, 'Artifact not found')
    return artifact


@router.get('')
def list_artifacts(test_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    test = get_test(db, test_id)
    items = db.scalars(select(TestArtifact).where(TestArtifact.test_id == test_id)
                       .order_by(TestArtifact.created_at, TestArtifact.id)).all()
    # The files the test's run shares with all of its tests. They are listed
    # here so the test shows them, but they are managed on the run.
    shared = db.scalars(select(TestRunArtifact).where(TestRunArtifact.test_run_id == test.test_run_id)
                        .order_by(TestRunArtifact.created_at, TestRunArtifact.id)).all() if test.test_run_id else []
    return {'items': [TestArtifactOut.model_validate(item) for item in items],
            'test_run': {'id': test.test_run.id, 'name': test.test_run.name} if test.test_run else None,
            'test_run_items': [TestRunArtifactOut.model_validate(item) for item in shared],
            'storage_enabled': settings.object_storage_enabled,
            'max_bytes': settings.artifact_max_bytes}


@router.post('', response_model=TestArtifactOut, status_code=201)
def upload_artifact(test_id: str, file: UploadFile, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    test = get_test(db, test_id, lock=True)
    artifact_id = new_uuid()
    filename = storage.artifact_filename(file.filename)
    key = storage.artifact_key(test_id, artifact_id, filename, run_folder(test))
    content_type = file.content_type or 'application/octet-stream'
    if len(content_type) > 255 or not re.fullmatch(r'[\w.+-]+/[\w.+-]+', content_type, flags=re.ASCII):
        content_type = 'application/octet-stream'
    bucket = settings.object_storage_bucket
    try:
        size = storage.upload_artifact(bucket, key, file.file, content_type)
    except OutputError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    artifact = TestArtifact(id=artifact_id, test_id=test_id, filename=filename,
                            content_type=content_type, size_bytes=size, bucket=bucket,
                            object_key=key, created_by=user.id)
    try:
        db.add(artifact)
        log_action(db, user, 'test.artifact.upload', 'test', test_id,
                   {'artifact_id': artifact_id, 'filename': filename, 'size_bytes': size}, request)
        db.commit()
    except Exception:
        db.rollback()
        try:
            storage.delete_artifact(bucket, key)
        except OutputError:
            logger.exception('Could not remove artifact after failed metadata save: %s', artifact_id)
        raise
    return artifact


@router.get('/{artifact_id}/download')
def download_artifact(test_id: str, artifact_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    artifact = get_artifact(db, test_id, artifact_id)
    try:
        result = storage.download_artifact(artifact.bucket, artifact.object_key)
    except OutputError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    body = result['Body']

    def chunks():
        try:
            while chunk := body.read(64 * 1024):
                yield chunk
        finally:
            body.close()

    return StreamingResponse(chunks(), media_type='application/octet-stream',
                             background=BackgroundTask(body.close), headers={
        'Content-Disposition': f"attachment; filename*=UTF-8''{quote(artifact.filename, safe='')}",
        'Content-Length': str(result['ContentLength']),
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'private, no-store',
    })


@router.delete('/{artifact_id}', status_code=204)
def delete_artifact(test_id: str, artifact_id: str, request: Request,
                    db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    get_test(db, test_id, lock=True)
    artifact = get_artifact(db, test_id, artifact_id)
    try:
        storage.delete_artifact(artifact.bucket, artifact.object_key)
    except OutputError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    log_action(db, user, 'test.artifact.delete', 'test', test_id,
               {'artifact_id': artifact.id, 'filename': artifact.filename}, request)
    db.delete(artifact)
    db.commit()
