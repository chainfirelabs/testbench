"""Global search across devices, software, tests and vendor claims."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Device, Test, Software, User, VendorDevice
from ..schemas import DeviceOut, SearchResults, TestOut, SoftwareOut
from ..services.device_schema import device_field_expression, union_field_map
from ..services.entity_fields import entity_field_expression, get_entity_fields
from .devices import device_out
from .deps import get_current_user
from .vendor_devices import CATALOG_TEXT_FILTERS, _catalog_rows

router = APIRouter(prefix="/search", tags=["search"])


def _count(db: Session, stmt) -> int:
    """Total rows the (unlimited) statement would return, for "showing N of M"."""
    return db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0


@router.get("", response_model=SearchResults)
def global_search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(5, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    like = f"%{q}%"
    device_terms = [
        device_field_expression(field).ilike(like)
        for field in union_field_map(db).values()
        if field.field_type in {"text", "textarea", "select"}
        and field.storage in {"data", "column"}
        # Never match on a secret: a hit would confirm a guessed password.
        and not field.sensitive
    ]
    software_terms = [
        entity_field_expression(Software, field).ilike(like)
        for field in get_entity_fields(db, "software")
        if field.field_type in {"text", "textarea", "select"}
        and field.key not in {"vendor_device_count", "version_count"}
    ]
    test_terms = [
        entity_field_expression(Test, field).ilike(like)
        for field in get_entity_fields(db, "tests")
        if field.field_type in {"text", "textarea", "select"}
        and field.key not in {"device_unique_id", "software_name", "created_by_username"}
    ]
    device_q = (
        select(Device)
        .where(or_(*device_terms) if device_terms else Device.id.ilike(like))
        .order_by(Device.unique_id)
    )
    software_q = (
        select(Software)
        .where(or_(*software_terms) if software_terms else Software.id.ilike(like))
        .order_by(Software.name)
    )
    tests_q = (
        select(Test)
        .join(Device, Test.device_id == Device.id)
        .join(Software, Test.software_id == Software.id)
        .where(
            or_(
                Device.unique_id.ilike(like),
                Software.name.ilike(like),
                *test_terms,
            )
        )
        .order_by(Test.created_at.desc())
    )

    # Vendor claims are the fourth thing a search term can be about, and the
    # one that cannot be found any other way: a claim lives under one software
    # version, so "who says they support this box?" has no page to start from.
    # The software's name and version match too — a claim is identified by the
    # pair, not by the hardware alone.
    vendor_q = (
        select(VendorDevice, Software)
        .join(Software, VendorDevice.software_id == Software.id)
        .where(
            or_(
                *[column.ilike(like) for column in CATALOG_TEXT_FILTERS.values()],
                Software.name.ilike(like),
            )
        )
        .order_by(Software.name, VendorDevice.match_key)
    )

    devices = db.scalars(device_q.limit(limit)).all()
    software = db.scalars(software_q.limit(limit)).all()
    tests = db.scalars(tests_q.limit(limit)).all()
    vendor_pairs = [tuple(row) for row in db.execute(vendor_q.limit(limit)).all()]

    return SearchResults(
        devices=[device_out(db, d, {}) for d in devices],
        software=[SoftwareOut.model_validate(t) for t in software],
        tests=[TestOut.model_validate(t) for t in tests],
        vendor_devices=_catalog_rows(db, vendor_pairs),
        devices_total=_count(db, device_q) if len(devices) == limit else len(devices),
        software_total=_count(db, software_q) if len(software) == limit else len(software),
        tests_total=_count(db, tests_q) if len(tests) == limit else len(tests),
        vendor_devices_total=(
            _count(db, vendor_q) if len(vendor_pairs) == limit else len(vendor_pairs)
        ),
    )
