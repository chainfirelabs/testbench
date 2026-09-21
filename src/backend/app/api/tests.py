from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Device, Test, Software, SoftwareComponent, User
from ..models.test import TEST_OUTCOMES, TEST_TAGS
from ..schemas import (
    BulkIds,
    BulkPayload,
    ImportResult,
    Page,
    TestCreate,
    TestImportRow,
    TestOut,
    TestUpdate,
)
from ..services.audit import field_diff, log_action
from ..services.io import download_response, streaming_export_response, parse_import, strip_nulls, template_csv
from ..services.list_filters import exclude_clause, excluded_values, include_clause
from ..services.query import row_error, row_scope
from ..services.entity_fields import coerce_query_value, entity_field_expression, entity_order_by, get_entity_fields, merge_extra_columns, project_fields, validate_custom_values
from .deps import get_current_user, require_tests_edit

router = APIRouter(prefix="/tests", tags=["tests"])

EXPORT_COLUMNS = [
    "id", "software_id", "software_name", "software_version", "device_id", "device_unique_id",
    "component_id", "component_name", "component_version",
    "device_make", "device_model", "outcome", "tag", "misc_data", "notes",
    "run_at", "created_at", "created_by_username",
]
EDITABLE_FIELDS = ["component_id", "software_version", "outcome", "tag", "misc_data", "notes", "run_at"]

# The columns an import template offers, required first. A row identifies its
# software and device by name — a hand-filled file has no UUIDs to quote.
TEMPLATE_COLUMNS = [
    "device_unique_id", "software_name", "software_version", "component_name", "component_version",
    "outcome", "tag", "run_at", "notes",
]

# Server-owned or display-only columns; an export re-imported as-is carries them.
IMPORT_IGNORED = {"created_at", "updated_at", "created_by_username", "device_make", "device_model"}

# CSV columns outside the import schema and the display-only export columns are
# test-specific measurements. They travel in Test.misc_data so a sheet can append
# fields such as `ping` without first encoding them as JSON by hand.
IMPORT_COLUMNS = set(TestImportRow.model_fields) | IMPORT_IGNORED
IMPORT_RELATION_FIELDS = {"id", "software_id", "software_name", "device_id", "device_unique_id", "component_id", "component_name", "component_version", "misc_data"}


def _merge_csv_extra_columns(row: dict, known: set[str] | None = None) -> dict:
    """Move non-empty, non-standard CSV cells into the row's misc-data object."""
    return merge_extra_columns(row, known or IMPORT_COLUMNS, "misc_data")


def _test_dict(t: Test) -> dict:
    return TestOut.model_validate(t).model_dump(mode="json")


def _resolve_component(
    db: Session, software: Software, component_id: str | None,
    component_name: str | None, component_version: str | None,
) -> SoftwareComponent | None:
    component = db.get(SoftwareComponent, component_id) if component_id else None
    name = (component_name or "").strip()
    version = (component_version or "").strip()
    if component is not None and component.software_id != software.id:
        raise ValueError("component does not belong to the selected software suite")
    if component is None and name:
        component = db.scalar(select(SoftwareComponent).where(
            SoftwareComponent.software_id == software.id,
            func.lower(SoftwareComponent.name) == name.lower(),
            SoftwareComponent.version == version,
        ))
        if component is None:
            component = SoftwareComponent(
                software_id=software.id, name=name, version=version,
                position=db.scalar(select(func.count()).select_from(SoftwareComponent).where(
                    SoftwareComponent.software_id == software.id,
                )) or 0,
            )
            db.add(component)
            db.flush()
    elif component is None and component_id:
        raise ValueError("unknown component_id")
    return component


def _resolve_import_row(db: Session, row: TestImportRow) -> dict:
    """Turn one import row into the fields of a Test.

    Rows may name their software and device instead of quoting UUIDs, since that is
    what a template filled in by hand can realistically carry.
    """
    software = db.get(Software, row.software_id) if row.software_id else None
    if software is None and row.software_name:
        # Software names match case-insensitively, so a file may spell one however.
        q = select(Software).where(func.lower(Software.name) == row.software_name.lower())
        if row.software_version:
            # The file names a version, so bind the run to that exact one
            # rather than to whichever version happens to be current.
            software = db.scalar(q.where(Software.version == row.software_version))
            if software is None:
                raise ValueError(f"unknown version of {row.software_name}: {row.software_version}")
        else:
            software = db.scalar(q.order_by(Software.created_at.desc()).limit(1))
    if software is None:
        raise ValueError(f"unknown software: {row.software_name or row.software_id or '(none given)'}")

    component = _resolve_component(
        db, software, row.component_id, row.component_name, row.component_version,
    )

    device = db.get(Device, row.device_id) if row.device_id else None
    if device is None and row.device_unique_id:
        device = db.scalar(select(Device).where(Device.unique_id == row.device_unique_id))
    if device is None:
        raise ValueError(f"unknown device: {row.device_unique_id or row.device_id or '(none given)'}")

    if row.outcome not in TEST_OUTCOMES:
        raise ValueError(f"outcome must be one of {TEST_OUTCOMES}")
    if row.tag not in TEST_TAGS:
        raise ValueError(f"tag must be one of {TEST_TAGS}")

    return {
        "software_id": software.id,
        "component_id": component.id if component else None,
        "device_id": device.id,
        # Unstated version means "whatever the software is on now", same as the API.
        "software_version": row.software_version or software.version,
        "outcome": row.outcome,
        "tag": row.tag,
        "misc_data": row.misc_data,
        "notes": row.notes,
        "run_at": row.run_at,
    }


def _query_tests(
    db: Session,
    search: str | None,
    device_id: str | None,
    software_id: str | None,
    outcome: str | None,
    tag: str | None,
    filters: dict | None = None,
) -> select:
    q = select(Test)
    if device_id:
        q = q.where(Test.device_id == device_id)
    if software_id:
        q = q.where(Test.software_id == software_id)
    if outcome:
        q = q.where(Test.outcome == outcome)
    if tag:
        q = q.where(Test.tag == tag)
    catalog = {field.key: field for field in get_entity_fields(db, "tests")}
    # A test's device, software and author are rows of their own, so filtering
    # on them means joining to reach them. They used to be skipped outright,
    # which did not disable the filter — it ignored it: the grid narrowed to a
    # device and the server answered with every test there is.
    joined_component = joined_device = joined_software = joined_creator = False
    for key, value in (filters or {}).items():
        excluded = key.startswith("exclude__")
        included = key.startswith("include__")
        if excluded or included:
            key = key.removeprefix("exclude__" if excluded else "include__")
        field = catalog.get(key)
        # `created_at` is a timestamp, which no checklist offers and no text
        # comparison fits; it has no filter shape here yet.
        if not field or field.key == "created_at":
            continue
        if key == "device_unique_id":
            if not joined_device:
                q = q.join(Device, Test.device_id == Device.id)
                joined_device = True
            expression = Device.unique_id
        elif key == "software_name":
            if not joined_software:
                q = q.join(Software, Test.software_id == Software.id)
                joined_software = True
            expression = Software.name
        elif key == "created_by_username":
            # Outer: a test imported or written by a since-deleted account has
            # no author row, and "(Blanks)" has to be able to find it.
            if not joined_creator:
                q = q.outerjoin(User, Test.created_by == User.id)
                joined_creator = True
            expression = User.username
        elif key in {"component_name", "component_version"}:
            if not joined_component:
                q = q.outerjoin(SoftwareComponent, Test.component_id == SoftwareComponent.id)
                joined_component = True
            expression = getattr(SoftwareComponent, "name" if key == "component_name" else "version")
        else:
            expression = entity_field_expression(Test, field)
        if excluded or included:
            values = [coerce_query_value(field, item) for item in excluded_values(value)]
            clause = exclude_clause(expression, values) if excluded else include_clause(expression, values)
            if clause is not None:
                q = q.where(clause)
            continue
        value = coerce_query_value(field, value)
        q = q.where(expression == value if field.indexed else expression.ilike(f"%{value}%"))
    if search:
        like = f"%{search}%"
        configured = [
            entity_field_expression(Test, field).ilike(like)
            for field in get_entity_fields(db, "tests")
            if field.field_type in {"text", "textarea", "select"}
            and field.key not in {
                "device_unique_id", "software_name", "component_name",
                "component_version", "created_by_username",
            }
        ]
        # Only the joins a filter above has not already made: joining the same
        # table twice is a different query, and usually an empty one.
        if not joined_component:
            q = q.outerjoin(SoftwareComponent, Test.component_id == SoftwareComponent.id)
            joined_component = True
        if not joined_device:
            q = q.join(Device, Test.device_id == Device.id)
            joined_device = True
        if not joined_software:
            q = q.join(Software, Test.software_id == Software.id)
            joined_software = True
        q = q.where(
            or_(
                Device.unique_id.ilike(like), Software.name.ilike(like),
                SoftwareComponent.name.ilike(like), SoftwareComponent.version.ilike(like),
                *configured,
            )
        )
    return q


@router.get("", response_model=Page[TestOut])
def list_tests(
    request: Request,
    search: str | None = None,
    device_id: str | None = None,
    software_id: str | None = None,
    outcome: str | None = None,
    tag: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = {
        key: value for key, value in request.query_params.items()
        if key not in {"search", "device_id", "software_id", "outcome", "tag", "sort", "order", "page", "page_size"}
    }
    q = _query_tests(db, search, device_id, software_id, outcome, tag, filters)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.order_by(entity_order_by(db, "tests", Test, sort, order))
    items = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[TestOut.model_validate(t) for t in items], total=total, page=page, page_size=page_size)


@router.get("/export")
def export_tests(
    format: str = "json",
    search: str | None = None,
    device_id: str | None = None,
    software_id: str | None = None,
    outcome: str | None = None,
    tag: str | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = db.scalars(_query_tests(db, search, device_id, software_id, outcome, tag).order_by(Test.created_at)).all()
    fields = get_entity_fields(db, "tests")
    rows = [project_fields(_test_dict(t), fields, "misc_data") for t in items]
    log_action(db, user, "export.tests", "test", None, {"format": format, "count": len(rows)}, request)
    db.commit()
    return streaming_export_response(rows, [field.key for field in fields], format, "tests")


@router.get("/template")
def test_template(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A blank CSV with base test columns; extra headers become misc data."""
    configured = [
        field.key for field in get_entity_fields(db, "tests")
        if field.writable and field.key != "misc_data"
    ]
    # Keep the hand-filled columns in a stable, useful order. In particular,
    # component fields must be present even on an upgraded installation where
    # the entity-field catalog initially added them as hidden.
    columns = [*TEMPLATE_COLUMNS, *(key for key in configured if key not in TEMPLATE_COLUMNS)]
    return download_response(template_csv(columns), "tests-template.csv", "text/csv")


@router.get("/{test_id}", response_model=TestOut)
def get_test(test_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    test = db.get(Test, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")
    return TestOut.model_validate(test)


@router.post("", response_model=TestOut, status_code=201)
def create_test(
    body: TestCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tests_edit),
):
    if body.outcome not in TEST_OUTCOMES:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {TEST_OUTCOMES}")
    if body.tag not in TEST_TAGS:
        raise HTTPException(status_code=400, detail=f"tag must be one of {TEST_TAGS}")
    software = db.get(Software, body.software_id)
    if software is None:
        raise HTTPException(status_code=400, detail="Unknown software_id")
    if db.get(Device, body.device_id) is None:
        raise HTTPException(status_code=400, detail="Unknown device_id")
    try:
        component = _resolve_component(
            db, software, body.component_id, body.component_name, body.component_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        data = validate_custom_values(
            db, "tests", body.model_dump(exclude={"component_name", "component_version"}), "misc_data",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not data.get("software_version"):
        # Record what the software is on now: the run is evidence about that build,
        # and the software's own version will move on without it.
        data["software_version"] = software.version
    data["component_id"] = component.id if component else None
    test = Test(**data, created_by=user.id)
    db.add(test)
    detail = body.model_dump(mode="json")
    detail["software_version"] = data["software_version"]
    log_action(db, user, "test.create", "test", test.id, detail, request)
    db.commit()
    db.refresh(test)
    return TestOut.model_validate(test)


@router.patch("/{test_id}", response_model=TestOut)
def update_test(
    test_id: str,
    body: TestUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tests_edit),
):
    test = db.get(Test, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")
    if body.outcome is not None and body.outcome not in TEST_OUTCOMES:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {TEST_OUTCOMES}")
    if body.tag is not None and body.tag not in TEST_TAGS:
        raise HTTPException(status_code=400, detail=f"tag must be one of {TEST_TAGS}")
    if body.component_id is not None:
        component = db.get(SoftwareComponent, body.component_id)
        if component is None or component.software_id != test.software_id:
            raise HTTPException(status_code=400, detail="Component does not belong to this test's software")
    old = {f: getattr(test, f) for f in EDITABLE_FIELDS}
    try:
        updates = validate_custom_values(
            db, "tests", body.model_dump(exclude_unset=True), "misc_data", partial=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for field, value in updates.items():
        setattr(test, field, value)
    test.updated_at = utcnow()
    log_action(db, user, "test.update", "test", test.id,
               {"diff": field_diff(old, {f: getattr(test, f) for f in EDITABLE_FIELDS})}, request)
    db.commit()
    db.refresh(test)
    return TestOut.model_validate(test)


@router.delete("/{test_id}", status_code=204)
def delete_test(
    test_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tests_edit),
):
    test = db.get(Test, test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")
    log_action(db, user, "test.delete", "test", test.id, {"snapshot": _test_dict(test)}, request)
    db.delete(test)
    db.commit()


@router.post("/delete", status_code=204)
def delete_tests_bulk(
    body: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tests_edit),
):
    """Delete multiple tests by id (multi-select delete)."""
    for test_id in body.ids:
        test = db.get(Test, test_id)
        if test:
            log_action(db, user, "test.delete", "test", test.id, {"snapshot": _test_dict(test), "bulk": True}, request)
            db.delete(test)
    db.commit()
    return None


@router.post("/bulk")
def bulk_tests(
    body: BulkPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tests_edit),
):
    created = updated = deleted = 0
    errors: list[dict] = []
    for i, row in enumerate(body.upserts):
        try:
            with row_scope(db):
                parsed = TestCreate(**{k: v for k, v in row.items() if k != "id"})
                software = db.get(Software, parsed.software_id)
                if software is None:
                    raise ValueError("unknown software_id")
                component = _resolve_component(
                    db, software, parsed.component_id, parsed.component_name, parsed.component_version,
                )
                data = parsed.model_dump(exclude={"component_name", "component_version"})
                data["component_id"] = component.id if component else None
                data = validate_custom_values(db, "tests", data, "misc_data")
                if not data.get("software_version"):
                    software = db.get(Software, data["software_id"])
                    data["software_version"] = software.version if software else None
                test = db.get(Test, row.get("id")) if row.get("id") else None
                if test is None:
                    test = Test(**data, created_by=user.id)
                    db.add(test)
                    outcome = "created"
                else:
                    for field, value in data.items():
                        setattr(test, field, value)
                    test.updated_at = utcnow()
                    outcome = "updated"
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": i, "error": row_error(exc)})
            continue
        created += outcome == "created"
        updated += outcome == "updated"
    for test_id in body.delete_ids:
        test = db.get(Test, test_id)
        if test:
            db.delete(test)
            deleted += 1
        else:
            errors.append({"row": None, "error": f"test {test_id} not found"})
    log_action(db, user, "test.bulk", "test", None,
               {"created": created, "updated": updated, "deleted": deleted, "errors": errors}, request)
    db.commit()
    return {"created": created, "updated": updated, "deleted": deleted, "errors": errors}


@router.post("/import", response_model=ImportResult)
async def import_tests(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_tests_edit),
):
    content = await file.read()
    filename = file.filename or ""
    rows = parse_import(content, filename)
    is_csv = filename.lower().endswith(".csv")
    result = ImportResult(created=0, updated=0, errors=[])
    for i, row in enumerate(rows):
        try:
            with row_scope(db):
                if not isinstance(row, dict):
                    raise ValueError("row must be an object")
                if is_csv:
                    configured = {field.key for field in get_entity_fields(db, "tests")}
                    row = _merge_csv_extra_columns(
                        row, IMPORT_RELATION_FIELDS | configured | IMPORT_IGNORED
                    )
                parsed = TestImportRow(
                    **strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
                )
                data = _resolve_import_row(db, parsed)
                data = validate_custom_values(db, "tests", data, "misc_data")
                test = db.get(Test, parsed.id) if parsed.id else None
                if test is None:
                    test = Test(**data, created_by=user.id)
                    db.add(test)
                    outcome = "created"
                else:
                    for field, value in data.items():
                        setattr(test, field, value)
                    test.updated_at = utcnow()
                    outcome = "updated"
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": i, "error": row_error(exc)})
            continue
        result.created += outcome == "created"
        result.updated += outcome == "updated"
    log_action(db, user, "test.import", "test", None,
               {"file": file.filename, "created": result.created, "updated": result.updated, "errors": result.errors}, request)
    db.commit()
    return result
