"""Authenticated exchange API and administrator controls for peer replication."""

import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuditLog, User
from ..services.audit import log_action
from ..services.plugin_host import registry
from ..services.replication import MODELS, apply_items, snapshot_page
from .deps import require_settings_manage

router = APIRouter(prefix="/replication", tags=["replication"])


def exchange_auth(x_testbench_replication_token: str | None = Header(default=None)) -> None:
    if (not settings.replication_enabled or not settings.replication_token
            or not x_testbench_replication_token
            or not hmac.compare_digest(x_testbench_replication_token, settings.replication_token)):
        raise HTTPException(status_code=401, detail="Invalid replication credential")


class ApplyIn(BaseModel):
    items: list[dict] = Field(max_length=250)
    force: bool = False


class RunResultIn(BaseModel):
    run_id: str = Field(max_length=64)
    trigger: Literal["manual", "scheduled"]
    state: Literal["succeeded", "failed"]
    mode: Literal["push", "push_pull"]
    peer_url: str = Field(max_length=2048)
    finished_at: str
    error: str | None = Field(default=None, max_length=500)
    entities: dict[str, dict] | None = None


def plugin_auth(
    x_testbench_plugin: str | None = Header(default=None),
    x_testbench_plugin_secret: str | None = Header(default=None),
) -> None:
    if (not settings.replication_enabled or x_testbench_plugin != "replication"
            or not settings.plugin_shared_secret or not x_testbench_plugin_secret
            or not hmac.compare_digest(x_testbench_plugin_secret, settings.plugin_shared_secret)):
        raise HTTPException(status_code=401, detail="Invalid plugin credential")


@router.post("/run-result", status_code=204)
def record_run_result(
    body: RunResultIn, db: Session = Depends(get_db), _: None = Depends(plugin_auth),
):
    action = f"replication.sync.{body.state}"
    if db.scalar(select(AuditLog.id).where(
        AuditLog.action == action, AuditLog.entity_type == "replication",
        AuditLog.entity_id == body.run_id,
    )) is not None:
        return
    detail = body.model_dump(exclude_none=True, exclude={"run_id"})
    log_action(db, None, action, "replication", body.run_id, detail)
    db.commit()


@router.get("/snapshot")
def get_snapshot(
    entity: str, after: str = "", tombstones: bool = False,
    db: Session = Depends(get_db), _: None = Depends(exchange_auth),
):
    if entity not in MODELS:
        raise HTTPException(status_code=422, detail="Unknown replication entity")
    return snapshot_page(db, entity, after, tombstones)


@router.post("/apply")
def apply_snapshot(
    body: ApplyIn, db: Session = Depends(get_db), _: None = Depends(exchange_auth),
):
    try:
        return apply_items(db, body.items, force=body.force)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/status")
def status(user: User = Depends(require_settings_manage)):
    if not settings.replication_enabled:
        return {"enabled": False}
    if settings.replication_mode == "receive":
        return {"enabled": True, "mode": "receive", "interval_minutes": 0, "running": False}
    try:
        result = registry.request("replication", "GET", "/plugin/v1/status")
        return {"enabled": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Replication controller is unavailable: {exc}") from exc


@router.post("/run", status_code=202)
def run(
    request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_settings_manage),
):
    if not settings.replication_enabled:
        raise HTTPException(status_code=404, detail="Replication is disabled")
    if settings.replication_mode == "receive":
        raise HTTPException(status_code=409, detail="This instance only receives replication")
    try:
        result = registry.request("replication", "POST", "/plugin/v1/run")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Replication controller is unavailable: {exc}") from exc
    log_action(db, user, "replication.sync.request", "replication", result["run_id"],
               {"peer_url": result["peer_url"], "mode": result["mode"]}, request)
    db.commit()
    return result
