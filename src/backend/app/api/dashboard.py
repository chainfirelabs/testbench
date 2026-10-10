"""Shared dashboard layout and its read-only fleet aggregate."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import DashboardLayout, Device, User
from ..services.audit import log_action
from ..services.dashboard_data import widget_data
from .deps import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

WIDGET_TYPES = (
    "fleet_summary", "devices_by_make", "online_by_type", "inventory_status",
    "checkouts_due", "recent_devices", "test_activity", "most_tested_software",
    "most_tested_devices", "announcement", "untested_devices", "recent_problem_tests",
    "scan_freshness", "software_outcomes", "firmware_coverage",
)


class DashboardWidget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: Literal[
        "fleet_summary", "devices_by_make", "online_by_type", "inventory_status",
        "checkouts_due", "recent_devices", "test_activity", "most_tested_software",
        "most_tested_devices", "announcement", "untested_devices", "recent_problem_tests",
        "scan_freshness", "software_outcomes", "firmware_coverage",
    ]
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=160)
    width_percent: int = Field(default=100, ge=25, le=100)
    height_px: int | None = Field(default=None, ge=260, le=1200)
    show_breakdown: bool = True
    top_n: int = Field(default=8, ge=3, le=20)
    days: int | None = Field(default=None, ge=1, le=365)
    threshold_days: int = Field(default=7, ge=1, le=90)
    body: str = Field(default="", max_length=1000)
    device_type_key: str | None = Field(default=None, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def accept_saved_width(cls, value):
        # Layouts saved before free resizing contain a full/half width string.
        if isinstance(value, dict) and "width" in value:
            value = value.copy()
            legacy_width = value.pop("width")
            if legacy_width not in ("full", "half"):
                raise ValueError("width must be full or half")
            value.setdefault("width_percent", 50 if legacy_width == "half" else 100)
        return value

    @model_validator(mode="after")
    def valid_identity(self):
        if self.id != self.type.replace("_", "-"):
            raise ValueError("widget id must match its type")
        return self

    @field_validator("title", "description", "body")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value:
            raise ValueError("title must not be blank")
        return value


DEFAULT_WIDGET = DashboardWidget(
    id="fleet-summary", type="fleet_summary", title="Fleet summary",
    description="Latest recorded scan state across all devices.",
)


class DashboardLayoutOut(BaseModel):
    revision: int
    widgets: list[DashboardWidget]


class DashboardLayoutUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    widgets: list[DashboardWidget] = Field(max_length=len(WIDGET_TYPES))

    @field_validator("widgets")
    @classmethod
    def no_duplicate_widgets(cls, widgets: list[DashboardWidget]) -> list[DashboardWidget]:
        if len({widget.type for widget in widgets}) != len(widgets):
            raise ValueError("each widget type can appear only once")
        return widgets


def _layout_out(row: DashboardLayout | None) -> DashboardLayoutOut:
    if row is None:
        return DashboardLayoutOut(revision=0, widgets=[DEFAULT_WIDGET.model_copy()])
    return DashboardLayoutOut(
        revision=row.revision,
        widgets=[DashboardWidget.model_validate(item) for item in row.widgets],
    )


def require_dashboard_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can edit the dashboard.")
    return user


@router.get("/layout", response_model=DashboardLayoutOut)
def get_layout(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return _layout_out(db.get(DashboardLayout, 1))


@router.put("/layout", response_model=DashboardLayoutOut)
def save_layout(
    body: DashboardLayoutUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_dashboard_admin),
):
    # A fresh development database may have been created with create_all rather
    # than the migration. Inserting inside this transaction also serializes two
    # admins racing to make its first edit.
    db.execute(insert(DashboardLayout).values(
        id=1, revision=0, widgets=[DEFAULT_WIDGET.model_dump()],
    ).on_conflict_do_nothing(index_elements=[DashboardLayout.id]))
    row = db.scalar(select(DashboardLayout).where(DashboardLayout.id == 1).with_for_update())
    if row.revision != body.revision:
        db.rollback()
        raise HTTPException(status_code=409, detail="The dashboard changed while you were editing. Reload it and try again.")
    before = row.widgets
    row.widgets = [widget.model_dump() for widget in body.widgets]
    row.revision += 1
    log_action(db, user, "dashboard.layout.update", "dashboard_layout", "1", {
        "revision": row.revision, "before": before, "after": row.widgets,
    }, request)
    db.commit()
    return _layout_out(row)


class DashboardDataRequest(BaseModel):
    widgets: list[DashboardWidget] = Field(max_length=len(WIDGET_TYPES))

    @field_validator("widgets")
    @classmethod
    def no_duplicate_widgets(cls, widgets: list[DashboardWidget]) -> list[DashboardWidget]:
        if len({widget.type for widget in widgets}) != len(widgets):
            raise ValueError("each widget type can appear only once")
        return widgets


@router.post("/data")
def dashboard_data(
    body: DashboardDataRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Read-only aggregates for only the requested widget types and settings."""
    stamp = utcnow()
    return {"as_of": stamp, "widgets": {
        widget.id: widget_data(db, widget.type, widget.model_dump(), stamp)
        for widget in body.widgets
    }}


class FleetSummaryOut(BaseModel):
    total: int
    online: int
    offline: int
    never_scanned: int
    as_of: datetime


@router.get("/fleet-summary", response_model=FleetSummaryOut)
def fleet_summary(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Current scan result for the fleet, counted without loading device rows."""
    scanned = Device.last_scanned_at.is_not(None)
    total, online, offline, never_scanned = db.execute(select(
        func.count(Device.id),
        func.count(Device.id).filter(scanned, Device.online_status.is_(True)),
        func.count(Device.id).filter(scanned, Device.online_status.is_not(True)),
        func.count(Device.id).filter(~scanned),
    )).one()
    return FleetSummaryOut(
        total=total, online=online, offline=offline,
        never_scanned=never_scanned, as_of=utcnow(),
    )
