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

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy import Boolean, Numeric, String, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..models import AuditLog, Device, Software, SoftwareComponent, Test, User, VendorDevice
from ..schemas import SuggestionsOut
from ..services.device_schema import device_field_expression, effective_field_map, union_field_map
from ..services.entity_fields import entity_field_expression, entity_field_map
from ..services.list_filters import exclude_clause, excluded_values, include_clause
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
@router.post("/{entity}/{field}/filter-values/query")
def field_filter_values(
    entity: str,
    field: str,
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    filters_body: Annotated[dict[str, str] | None, Body()] = None,
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
        if field == "device_type_id":
            column = Device.device_type_id
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
            columns["is_online"] = case(
                (User.last_login_at >= utcnow() - timedelta(hours=settings.jwt_expires_hours), True),
                else_=False,
            )
        column = columns.get(field)
    if column is None:
        raise HTTPException(404, f"{entity} has no filter values for {field!r}")
    if isinstance(column.type, String):
        column = func.nullif(column, "")
    if filters_body and any(not key.startswith(("include__", "exclude__")) for key in filters_body):
        raise HTTPException(422, "The query body may contain only checklist filters")
    params = {key: value for key, value in {**request.query_params, **(filters_body or {})}.items()
              if key not in {"offset", "limit", field, f"include__{field}", f"exclude__{field}"}}
    # Facet values come from the same filtered row set as the table. Leave out
    # this column's predicate so users can change its selection without first
    # clearing it. With no context, retain the original full-domain response.
    matched_ids = None
    if params:
        if entity == "vendor-devices":
            from .vendor_devices import _catalog_query
            base = VendorDevice
            query = _catalog_query(db, params.get("search"), params)
        elif entity == "devices":
            from .devices import _query_devices
            base = Device
            controls = {"search", "sort", "order", "page", "page_size"}
            filters = {key: value for key, value in params.items() if key not in controls}
            software_id = filters.pop("software_id", None)
            component_filters = {key: filters.pop(key) for key in list(filters)
                                 if key.removeprefix("include__").removeprefix("exclude__")
                                 in {"component_name", "component_version"}}
            for flag in ("online", "overdue"):
                if flag in filters:
                    filters[flag] = str(filters[flag]).lower() == "true"
            query = _query_devices(db, filters, None if software_id else params.get("search"))
            if software_id:
                query = query.join(Test, Test.device_id == Device.id).outerjoin(
                    SoftwareComponent, Test.component_id == SoftwareComponent.id,
                ).where(Test.software_id == software_id)
                for key, value in component_filters.items():
                    keeping = key.startswith("include__")
                    excluded = key.startswith("exclude__")
                    name = key.removeprefix("include__" if keeping else "exclude__") if keeping or excluded else key
                    expression = getattr(SoftwareComponent, "name" if name == "component_name" else "version")
                    clause = ((include_clause if keeping else exclude_clause)(expression, excluded_values(value))
                              if keeping or excluded else expression.ilike(f"%{value}%"))
                    if clause is not None:
                        query = query.where(clause)
                if params.get("search"):
                    like = f"%{params['search']}%"
                    query = query.where(or_(Device.unique_id.ilike(like), Device.make.ilike(like),
                                            Device.model.ilike(like), SoftwareComponent.name.ilike(like),
                                            SoftwareComponent.version.ilike(like)))
        elif entity == "software":
            from .software import _query_software
            base = Software
            controls = {"search", "sort", "order", "page", "page_size", "latest_only"}
            filters = {key: value for key, value in params.items() if key not in controls}
            query = _query_software(db, params.get("search"),
                                    params.get("latest_only") == "true", filters)
        elif entity == "tests":
            from .tests import _query_tests
            base = Test
            controls = {"search", "sort", "order", "page", "page_size"}
            filters = {key: value for key, value in params.items() if key not in controls}
            query = _query_tests(db, params.get("search"), params.get("device_id"),
                                 params.get("software_id"), params.get("outcome"),
                                 params.get("tag"), filters)
        elif entity in {"users", "audit_logs"}:
            base = User if entity == "users" else AuditLog
            query = select(base)
            if params.get("search"):
                like = f"%{params['search']}%"
                searchable = ([User.username, User.email, User.role, User.auth_provider]
                              if entity == "users" else
                              [AuditLog.username, AuditLog.action, AuditLog.entity_type,
                               AuditLog.entity_id, AuditLog.ip_address])
                query = query.where(or_(*(item.ilike(like) for item in searchable)))
            columns = ({**SUGGESTABLE["users"], "created_at": User.created_at,
                        "last_login_at": User.last_login_at} if entity == "users" else
                       {**SUGGESTABLE["audit_logs"], "entity_id": AuditLog.entity_id,
                        "timestamp": AuditLog.timestamp})
            for key, value in params.items():
                keeping = key.startswith("include__")
                excluded = key.startswith("exclude__")
                if not (keeping or excluded):
                    if entity == "audit_logs" and key in columns and key != "search":
                        query = query.where(columns[key] == value if key in {"entity_type", "entity_id"}
                                            else columns[key].ilike(f"%{value}%"))
                    continue
                name = key.removeprefix("include__" if keeping else "exclude__")
                if entity == "users" and name == "is_online":
                    dropped = {str(item).lower() for item in excluded_values(value)}
                    if keeping:
                        dropped = {"true", "false"} - dropped
                    cutoff = utcnow() - timedelta(hours=settings.jwt_expires_hours)
                    if dropped == {"true", "false"}:
                        query = query.where(User.id.is_(None))
                    elif "true" in dropped:
                        query = query.where(or_(User.last_login_at.is_(None),
                                                User.last_login_at < cutoff))
                    elif "false" in dropped:
                        query = query.where(User.last_login_at >= cutoff)
                    continue
                expression = columns.get(name)
                if expression is not None:
                    clause = (include_clause if keeping else exclude_clause)(expression, excluded_values(value))
                    if clause is not None:
                        query = query.where(clause)
        else:
            base = None
        if base is not None:
            matched_ids = query.with_only_columns(base.id).subquery()
    if matched_ids is not None:
        statement = select(column).select_from(base).join(matched_ids, matched_ids.c.id == base.id)
        if entity == "vendor-devices" and field in related:
            statement = statement.join(Software, VendorDevice.software_id == Software.id)
        elif entity == "tests" and field in related:
            target, join = {
                "device_unique_id": (Device, Test.device_id == Device.id),
                "software_name": (Software, Test.software_id == Software.id),
                "component_name": (SoftwareComponent, Test.component_id == SoftwareComponent.id),
                "component_version": (SoftwareComponent, Test.component_id == SoftwareComponent.id),
                "created_by_username": (User, Test.created_by == User.id),
            }[field]
            statement = statement.outerjoin(target, join)
        statement = statement.distinct().order_by(column.asc().nullsfirst()).offset(offset).limit(limit + 1)
        values = list(db.scalars(statement))
        return {"values": values[:limit], "has_more": len(values) > limit}
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
