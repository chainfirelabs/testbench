"""Test runs: one pipeline or suite execution, the tests it recorded, and the
files it shares with all of them (a pytest report, say), stored once.

A run's shared files live in its storage folder under `artifacts/`; see
services/test_artifact_storage for the whole layout.
"""
import logging
import re
from typing import BinaryIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..config import settings
from ..db import get_db, new_uuid, utcnow
from ..models import Test, TestRun, TestRunArtifact, User
from ..schemas import Page, TestRunArtifactOut, TestRunCreate, TestRunOut, TestRunUpdate
from ..services.audit import field_diff, log_action
from ..services.object_storage import OutputError
from ..services import test_artifact_storage as storage
from .deps import get_current_user, require_tests_edit

router = APIRouter(prefix='/test-runs', tags=['test-runs'])
logger = logging.getLogger(__name__)

FIELDS = ['name', 'external_id', 'url', 'started_at', 'finished_at', 'notes']
SORTS = {
    'name': lambda: TestRun.name, 'external_id': lambda: TestRun.external_id,
    'started_at': lambda: TestRun.started_at, 'finished_at': lambda: TestRun.finished_at,
    'created_at': lambda: TestRun.created_at, 'test_count': lambda: TestRun.test_count,
    'pass_count': lambda: TestRun.pass_count, 'fail_count': lambda: TestRun.fail_count,
    'warn_count': lambda: TestRun.warn_count, 'artifact_count': lambda: TestRun.artifact_count,
}


def get_run(db: Session, run_id: str, *, lock: bool = False) -> TestRun:
    query = select(TestRun).where(TestRun.id == run_id)
    if lock:
        query = query.with_for_update(of=TestRun)
    run = db.scalar(query)
    if run is None:
        raise HTTPException(404, 'Test run not found')
    return run


def _blank_to_none(value: str | None) -> str | None:
    return value.strip() or None if isinstance(value, str) else value


@router.get('', response_model=Page[TestRunOut])
def list_runs(
    search: str | None = None,
    sort: str = 'created_at',
    order: str = 'desc',
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(TestRun)
    if search:
        like = f'%{search}%'
        query = query.where(or_(TestRun.name.ilike(like), TestRun.external_id.ilike(like)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    expression = SORTS.get(sort, SORTS['created_at'])()
    query = query.order_by(expression.desc().nullslast() if order == 'desc' else expression.asc().nullsfirst(),
                           TestRun.id)
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[TestRunOut.model_validate(item) for item in items],
                total=total, page=page, page_size=page_size)


@router.post('', response_model=TestRunOut, status_code=201)
def create_run(body: TestRunCreate, request: Request, response: Response,
               db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    """Start a run, or join the one already started under the same external_id.

    Every job of a CI pipeline can call this with the pipeline's ID and get the
    same run back, whichever of them gets there first.
    """
    external_id = _blank_to_none(body.external_id)
    if external_id:
        existing = db.scalar(select(TestRun).where(TestRun.external_id == external_id))
        if existing is not None:
            response.status_code = 200
            return TestRunOut.model_validate(existing)
    run_id = new_uuid()
    run = TestRun(
        id=run_id, name=body.name.strip(), external_id=external_id, url=body.url,
        started_at=body.started_at or utcnow(), finished_at=body.finished_at,
        notes=body.notes, storage_folder=storage.run_folder(run_id, body.name), created_by=user.id,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        # Another job created it between the lookup and this insert.
        db.rollback()
        existing = db.scalar(select(TestRun).where(TestRun.external_id == external_id)) if external_id else None
        if existing is None:
            raise
        response.status_code = 200
        return TestRunOut.model_validate(existing)
    log_action(db, user, 'test_run.create', 'test_run', run.id, body.model_dump(mode='json'), request)
    db.commit()
    db.refresh(run)
    return TestRunOut.model_validate(run)


@router.get('/{run_id}', response_model=TestRunOut)
def read_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return TestRunOut.model_validate(get_run(db, run_id))


@router.patch('/{run_id}', response_model=TestRunOut)
def update_run(run_id: str, body: TestRunUpdate, request: Request,
               db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    run = get_run(db, run_id, lock=True)
    old = {field: getattr(run, field) for field in FIELDS}
    changes = body.model_dump(exclude_unset=True)
    if 'name' in changes:
        if not (changes['name'] or '').strip():
            raise HTTPException(422, 'name cannot be blank')
        changes['name'] = changes['name'].strip()
    if 'external_id' in changes:
        changes['external_id'] = _blank_to_none(changes['external_id'])
        if changes['external_id'] and db.scalar(select(TestRun.id).where(
                TestRun.external_id == changes['external_id'], TestRun.id != run.id)):
            raise HTTPException(409, 'Another test run already has that external_id')
    for field, value in changes.items():
        setattr(run, field, value)
    run.updated_at = utcnow()
    log_action(db, user, 'test_run.update', 'test_run', run.id,
               {'diff': field_diff(old, {field: getattr(run, field) for field in FIELDS})}, request)
    db.commit()
    db.refresh(run)
    return TestRunOut.model_validate(run)


@router.delete('/{run_id}', status_code=204)
def delete_run(run_id: str, request: Request,
               db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    """Delete the run. Its tests stay, no longer in a run.

    Like deleting a test, this removes the record of the run's shared files
    but leaves the files themselves in the bucket, for the operator's
    retention policy to deal with.
    """
    run = get_run(db, run_id, lock=True)
    # Explicitly rather than by the foreign key's SET NULL, so each test is
    # marked as changed and replication carries the change.
    unlinked = db.execute(update(Test).where(Test.test_run_id == run.id)
                          .values(test_run_id=None, updated_at=utcnow())).rowcount
    log_action(db, user, 'test_run.delete', 'test_run', run.id,
               {'snapshot': TestRunOut.model_validate(run).model_dump(mode='json'),
                'tests_unlinked': unlinked}, request)
    db.delete(run)
    db.commit()


# ---------- shared files ----------

def get_run_artifact(db: Session, run_id: str, artifact_id: str) -> TestRunArtifact:
    artifact = db.scalar(select(TestRunArtifact).where(
        TestRunArtifact.id == artifact_id, TestRunArtifact.test_run_id == run_id))
    if artifact is None:
        raise HTTPException(404, 'Artifact not found')
    return artifact


@router.get('/{run_id}/artifacts')
def list_run_artifacts(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_run(db, run_id)
    items = db.scalars(select(TestRunArtifact).where(TestRunArtifact.test_run_id == run_id)
                       .order_by(TestRunArtifact.created_at, TestRunArtifact.id)).all()
    return {'items': [TestRunArtifactOut.model_validate(item) for item in items],
            'storage_enabled': settings.object_storage_enabled,
            'max_bytes': settings.artifact_max_bytes}


@router.post('/{run_id}/artifacts', response_model=TestRunArtifactOut, status_code=201)
def upload_run_artifact(run_id: str, file: UploadFile, request: Request,
                        db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    run = get_run(db, run_id, lock=True)
    return add_run_file(db, run, file.filename, file.file, file.content_type, user, request)


def add_run_file(db: Session, run: TestRun, filename: str | None, stream: BinaryIO,
                 content_type: str | None, user: User | None, request: Request | None = None,
                 audit: dict | None = None) -> TestRunArtifact:
    """Store one shared file on a run and record it, as an upload does.

    Also how the AI Test Summary plugin saves its reports, so a report is a
    shared file like any other: listed, downloadable and deletable the same way.
    """
    artifact_id = new_uuid()
    filename = storage.artifact_filename(filename)
    key = storage.run_artifact_key(run.storage_folder, artifact_id, filename)
    content_type = content_type or 'application/octet-stream'
    if len(content_type) > 255 or not re.fullmatch(r'[\w.+-]+/[\w.+-]+', content_type, flags=re.ASCII):
        content_type = 'application/octet-stream'
    bucket = settings.object_storage_bucket
    try:
        size = storage.upload_artifact(bucket, key, stream, content_type)
    except OutputError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    artifact = TestRunArtifact(id=artifact_id, test_run_id=run.id, filename=filename,
                               content_type=content_type, size_bytes=size, bucket=bucket,
                               object_key=key, created_by=user.id if user else None)
    try:
        db.add(artifact)
        log_action(db, user, 'test_run.artifact.upload', 'test_run', run.id,
                   {'artifact_id': artifact_id, 'filename': filename, 'size_bytes': size, **(audit or {})},
                   request)
        db.commit()
    except Exception:
        db.rollback()
        try:
            storage.delete_artifact(bucket, key)
        except OutputError:
            logger.exception('Could not remove run artifact after failed metadata save: %s', artifact_id)
        raise
    return artifact


@router.get('/{run_id}/artifacts/{artifact_id}/download')
def download_run_artifact(run_id: str, artifact_id: str, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    artifact = get_run_artifact(db, run_id, artifact_id)
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


@router.delete('/{run_id}/artifacts/{artifact_id}', status_code=204)
def delete_run_artifact(run_id: str, artifact_id: str, request: Request,
                        db: Session = Depends(get_db), user: User = Depends(require_tests_edit)):
    get_run(db, run_id, lock=True)
    artifact = get_run_artifact(db, run_id, artifact_id)
    try:
        storage.delete_artifact(artifact.bucket, artifact.object_key)
    except OutputError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    log_action(db, user, 'test_run.artifact.delete', 'test_run', run_id,
               {'artifact_id': artifact.id, 'filename': artifact.filename}, request)
    db.delete(artifact)
    db.commit()
