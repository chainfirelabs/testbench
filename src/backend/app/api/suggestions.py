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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Device, Software, Test, User, VendorDevice
from ..schemas import SuggestionsOut
from ..services.device_schema import device_field_expression, effective_field_map
from ..services.entity_fields import entity_field_expression, entity_field_map
from .deps import get_current_user

router = APIRouter(prefix="/suggestions", tags=["suggestions"])

# entity -> field -> column. The keys are the API's own names for these
# collections, so they match the paths the rest of the API uses.
SUGGESTABLE = {
    "vendor-devices": {
        "vendor": VendorDevice.vendor,
        "make": VendorDevice.make,
        "model": VendorDevice.model,
        "firmware_version": VendorDevice.firmware_version,
        "hardware_version": VendorDevice.hardware_version,
        "source": VendorDevice.source,
    },
}


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
