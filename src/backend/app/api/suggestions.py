"""Existing values for a field, offered as you type.

A fleet's free-text columns are meant to repeat: a device is in "Rack 4 — Lab
B" or it is not, and typing that from memory produces "Rack 4 - Lab B" the
second time. Nothing in the schema can enforce agreement on a free-text column,
so the fix is to make the value that is already there the easiest one to pick.

The fields are whitelisted rather than resolved from the request. `getattr` on
a model from a path parameter would happily hand out `password_hash`, and a
distinct-values endpoint over a column like that is a disclosure, not a
convenience. Only the columns named below can be asked for.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Boolean, Numeric, String, cast, func, literal, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, Device, Software, SoftwareComponent, Test, User, VendorDevice
from ..schemas import SuggestionsOut
from ..services.device_schema import device_field_expression, effective_field_map, union_field_map
from ..services.entity_fields import entity_field_expression, entity_field_map
from .deps import get_current_user
from ..services.permissions import AUDIT_VIEW, USERS_MANAGE, caller_permissions

router = APIRouter(prefix="/suggestions", tags=["suggestions"])

# entity -> field -> column. The keys are the API's own names for these
# collections, so they match the paths the rest of the API uses.
SUGGESTABLE = {
    "vendor-devices": {
        "make": VendorDevice.make,
        "model": VendorDevice.model,
        "firmware_version": VendorDevice.firmware_version,
        "hardware_version": VendorDevice.hardware_version,
        "architecture": VendorDevice.architecture,
        "support_status": VendorDevice.support_status,
        "source": VendorDevice.source,
    },
    # The audit and user collections are listed so their grids' column filters
    # can offer the whole column rather than the page on screen. Both are admin
    # reading, and so is this: who has logged in and what they did is not a
    # list a readonly account gets to enumerate.
    "audit_logs": {
        "username": AuditLog.username,
        "action": AuditLog.action,
        "entity_type": AuditLog.entity_type,
        "ip_address": AuditLog.ip_address,
    },
    "users": {
        "username": User.username,
        "email": User.email,
        "role": User.role,
        "auth_provider": User.auth_provider,
    },
}

# Collections whose distinct values need the permission their own list endpoint
# needs: enumerating every username, or every action anyone has taken, is the
# same disclosure as reading the list it came from.
GATED = {"audit_logs": AUDIT_VIEW, "users": USERS_MANAGE}


@router.get("/{entity}/{field}", response_model=SuggestionsOut)
def field_suggestions(
    entity: str,
    field: str,
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fields = SUGGESTABLE.get(entity)
    if fields is None and entity not in {"devices", "software", "tests"}:
        raise HTTPException(404, f"No suggestions for {entity!r}")
    needed = GATED.get(entity)
    if needed and needed not in caller_permissions(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Your role ({user.role}) is not allowed to read {entity.replace('_', ' ')}.",
        )
    if entity == "devices":
        # Devices resolve through the published schema rather than the frozen
        # catalog, and a sensitive field is never suggestible: a dropdown of
        # every password in the fleet is a disclosure, not a convenience.
        configured = effective_field_map(db, None).get(field)
        column = (
            device_field_expression(configured)
            if configured
            and configured.field_type in {"text", "textarea", "select"}
            and configured.storage in {"data", "column"}
            and not configured.sensitive
            else None
        )
    elif entity in {"software", "tests"}:
        models = {"software": Software, "tests": Test}
        configured = entity_field_map(db, entity).get(field)
        column = (
            entity_field_expression(models[entity], configured)
            if configured and configured.field_type in {"text", "textarea", "select"}
            else None
        )
    else:
        column = fields.get(field) if fields else None
    if column is None:
        raise HTTPException(404, f"{entity} has no suggestible field {field!r}")

    # Commonest first, then alphabetical. A column with a long tail of one-off
    # values (someone's typo) should not push the value everything else uses
    # off the end of the list.
    stmt = (
        select(column, func.count().label("n"))
        .where(column.isnot(None), func.trim(column) != "")
        .group_by(column)
        .order_by(func.count().desc(), column.asc())
        .limit(limit)
    )
    if q:
        stmt = stmt.where(column.ilike(f"%{q}%"))

    return SuggestionsOut(values=[row[0] for row in db.execute(stmt)])


@router.get("/{entity}/{field}/filter-values")
def field_filter_values(
    entity: str,
    field: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Paginated, typed distinct values, including blanks, for column filters.

    Autocomplete's top 500 nonempty strings cannot describe a filter's value
    domain. Keep its existing contract and enumerate filter options separately.
    """
    needed = GATED.get(entity)
    if needed and needed not in caller_permissions(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed to read these filter values")
    column = None
    if entity == "devices":
        configured = union_field_map(db).get(field)
        if (configured and not configured.sensitive
                and configured.field_type not in {"json", "password"}
                and configured.storage in {"data", "column"}):
            column = device_field_expression(configured)
    elif entity in {"software", "tests", "vendor-devices"}:
        configured = entity_field_map(db, entity.replace("-", "_")).get(field)
        # These aliases are relationship columns displayed by the grids.
        related = {
            "tests": {"device_unique_id": Device.unique_id, "software_name": Software.name,
                      "component_name": SoftwareComponent.name, "component_version": SoftwareComponent.version,
                      "created_by_username": User.username},
            "vendor-devices": {"software_name": Software.name, "software_version": Software.version},
        }.get(entity, {})
        if field in related and (not configured or not configured.sensitive):
            column = related[field]
        elif (configured and not configured.sensitive
              and configured.field_type not in {"json", "password"}
              and field not in {"vendor_device_count", "version_count", "created_at", "updated_at"}):
            if entity == "vendor-devices":
                if configured.storage == "data":
                    column = VendorDevice.misc_data[field].astext
                    if configured.field_type in {"boolean", "number"}:
                        column = cast(func.nullif(column, ""), Boolean if configured.field_type == "boolean" else Numeric)
                else:
                    column = getattr(VendorDevice, field, None)
            else:
                column = entity_field_expression({"software": Software, "tests": Test}[entity], configured)
    else:
        columns = dict(SUGGESTABLE.get(entity, {}))
        if entity == "audit_logs":
            columns.update(timestamp=AuditLog.timestamp, entity_id=AuditLog.entity_id)
        elif entity == "users":
            columns.update(created_at=User.created_at, last_login_at=User.last_login_at)
        column = columns.get(field)
    if column is None:
        raise HTTPException(404, f"{entity} has no filter values for {field!r}")
    if isinstance(column.type, String):
        column = func.nullif(column, "")
    if entity in {"tests", "vendor-devices"} and field in related:
        # Enumerate only values reachable through this collection. In
        # particular, test authors must not expose unrelated user accounts.
        if entity == "tests":
            target, join = {
                "device_unique_id": (Device, Test.device_id == Device.id),
                "software_name": (Software, Test.software_id == Software.id),
                "component_name": (SoftwareComponent, Test.component_id == SoftwareComponent.id),
                "component_version": (SoftwareComponent, Test.component_id == SoftwareComponent.id),
                "created_by_username": (User, Test.created_by == User.id),
            }[field]
            related_values = select(column.label("value")).select_from(Test).outerjoin(target, join)
        else:
            related_values = select(column.label("value")).select_from(VendorDevice).join(Software)
        domain = related_values.union(select(literal(None).label("value"))).subquery()
        column = domain.c.value
    statement = select(column).distinct().order_by(column.asc().nullsfirst()).offset(offset).limit(limit + 1)
    values = list(db.scalars(statement))
    return {"values": values[:limit], "has_more": len(values) > limit}
