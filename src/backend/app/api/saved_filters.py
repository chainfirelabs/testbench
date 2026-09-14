from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SavedFilter, User
from ..models.saved_filter import FILTER_ENTITIES, FILTER_PLATFORMS, is_filter_entity
from ..schemas import Page, SavedFilterCreate, SavedFilterOut, SavedFilterUpdate
from ..services.audit import log_action
from .deps import get_current_user, require_write

router = APIRouter(prefix="/saved_filters", tags=["saved_filters"])


@router.get("", response_model=list[SavedFilterOut])
def list_saved_filters(
    entity: str | None = None,
    platform: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A user's saved views, narrowed to one entity and/or one platform.

    `platform` is omitted rather than defaulted so an unfiltered call still
    returns everything, which is what the export and any older client expect.
    """
    q = select(SavedFilter).where(SavedFilter.user_id == user.id).order_by(SavedFilter.name)
    if entity:
        q = q.where(SavedFilter.entity == entity)
    if platform:
        q = q.where(SavedFilter.platform == platform)
    return db.scalars(q).all()


@router.post("", response_model=SavedFilterOut, status_code=201)
def create_saved_filter(
    body: SavedFilterCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    if not is_filter_entity(body.entity):
        raise HTTPException(
            status_code=400,
            detail=f"entity must be one of {FILTER_ENTITIES}, or devices:<device type key>",
        )
    if body.platform not in FILTER_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"platform must be one of {FILTER_PLATFORMS}")
    if body.is_default:
        # One default per user, per entity, *per platform* — setting a phone's
        # default must leave the same user's desktop default alone.
        db.query(SavedFilter).filter(
            SavedFilter.user_id == user.id,
            SavedFilter.entity == body.entity,
            SavedFilter.platform == body.platform,
            SavedFilter.is_default.is_(True),
        ).update({SavedFilter.is_default: False})
    sf = SavedFilter(user_id=user.id, **body.model_dump())
    db.add(sf)
    log_action(db, user, "saved_filter.create", "saved_filter", sf.id, {"name": body.name, "entity": body.entity})
    db.commit()
    db.refresh(sf)
    return sf


@router.patch("/{sf_id}", response_model=SavedFilterOut)
def update_saved_filter(
    sf_id: str,
    body: SavedFilterUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    sf = db.get(SavedFilter, sf_id)
    if sf is None or sf.user_id != user.id:
        raise HTTPException(status_code=404, detail="Saved filter not found")
    updates = body.model_dump(exclude_unset=True)
    if "platform" in updates and updates["platform"] not in FILTER_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"platform must be one of {FILTER_PLATFORMS}")
    if updates.get("is_default"):
        # The platform being moved into, which is the incoming one when this
        # request also changes it.
        platform = updates.get("platform", sf.platform)
        db.query(SavedFilter).filter(
            SavedFilter.user_id == user.id,
            SavedFilter.entity == sf.entity,
            SavedFilter.platform == platform,
            SavedFilter.is_default.is_(True),
        ).update({SavedFilter.is_default: False})
    for field, value in updates.items():
        setattr(sf, field, value)
    log_action(db, user, "saved_filter.update", "saved_filter", sf.id, {"fields": list(updates)})
    db.commit()
    db.refresh(sf)
    return sf


@router.delete("/{sf_id}", status_code=204)
def delete_saved_filter(sf_id: str, db: Session = Depends(get_db), user: User = Depends(require_write)):
    sf = db.get(SavedFilter, sf_id)
    if sf is None or sf.user_id != user.id:
        raise HTTPException(status_code=404, detail="Saved filter not found")
    log_action(db, user, "saved_filter.delete", "saved_filter", sf.id, {"name": sf.name})
    db.delete(sf)
    db.commit()
