"""Bounded, non-sensitive aggregates for the shared dashboard."""

from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, select, true
from sqlalchemy.orm import Session

from ..models import Device, DeviceType, Software, Test
from .checkout import today


def _scan_counts():
    scanned = Device.last_scanned_at.is_not(None)
    return (
        func.count(Device.id).filter(scanned, Device.online_status.is_(True)),
        func.count(Device.id).filter(scanned, Device.online_status.is_not(True)),
        func.count(Device.id).filter(~scanned),
    )


def _period(days: int | None):
    return true() if days is None else Test.run_at >= today() - timedelta(days=days - 1)


def widget_data(db: Session, kind: str, settings: dict, stamp: datetime) -> dict:
    top_n = settings["top_n"]
    days = settings["days"]

    if kind == "fleet_summary":
        total, online, offline, never = db.execute(select(
            func.count(Device.id), *_scan_counts(),
        )).one()
        return {"total": total, "online": online, "offline": offline, "never_scanned": never}

    if kind == "devices_by_make":
        make = func.coalesce(func.nullif(func.btrim(Device.make), ""), "Unknown")
        total = db.scalar(select(func.count(Device.id))) or 0
        visible = db.execute(select(make, func.count(Device.id))
            .group_by(make).order_by(func.count(Device.id).desc(), make.asc())
            .limit(top_n)).all()
        other = total - sum(count for _, count in visible)
        items = [{"label": label, "count": count} for label, count in visible]
        if other:
            items.append({"label": "Other", "count": other})
        return {"items": items, "total": total}

    if kind == "online_by_type":
        online, offline, never = _scan_counts()
        label = func.coalesce(DeviceType.label, "Uncategorized")
        key = func.coalesce(DeviceType.key, "uncategorized")
        rows = db.execute(select(key, label, online, offline, never)
            .select_from(Device).outerjoin(DeviceType, Device.device_type_id == DeviceType.id)
            .group_by(DeviceType.key, DeviceType.label, DeviceType.position)
            .order_by(DeviceType.position.asc().nulls_last(), label.asc())).all()
        return {"items": [{"key": key, "label": label, "online": on,
                            "offline": off, "never_scanned": unseen}
                           for key, label, on, off, unseen in rows]}

    if kind == "inventory_status":
        status = func.coalesce(func.nullif(Device.status, ""), "available")
        rows = db.execute(select(status, func.count(Device.id))
            .group_by(status).order_by(func.count(Device.id).desc(), status.asc())).all()
        return {"items": [{"label": label, "count": count} for label, count in rows]}

    if kind == "checkouts_due":
        current_day = today()
        due = and_(Device.status == "checked_out", Device.checkout_due.is_not(None))
        overdue = db.scalar(select(func.count(Device.id)).where(due, Device.checkout_due < current_day)) or 0
        soon = db.scalar(select(func.count(Device.id)).where(
            due, Device.checkout_due >= current_day, Device.checkout_due <= current_day + timedelta(days=7))) or 0
        rows = db.execute(select(Device.id, Device.unique_id, Device.checkout_due)
            .where(due, Device.checkout_due <= current_day + timedelta(days=7))
            .order_by(Device.checkout_due.asc(), Device.unique_id.asc()).limit(top_n)).all()
        return {"overdue": overdue, "soon_due": soon, "items": [
            {"id": identifier, "label": unique_id, "due": due_date.isoformat(),
             "days_remaining": (due_date - current_day).days}
            for identifier, unique_id, due_date in rows]}

    if kind == "recent_devices":
        changed = func.coalesce(Device.updated_at, Device.created_at)
        rows = db.execute(select(Device.id, Device.unique_id, DeviceType.label, changed)
            .select_from(Device).outerjoin(DeviceType, Device.device_type_id == DeviceType.id)
            .order_by(changed.desc(), Device.id.asc()).limit(top_n)).all()
        return {"items": [{"id": identifier, "label": unique_id,
                            "type_label": type_label or "Uncategorized", "changed_at": changed_at}
                           for identifier, unique_id, type_label, changed_at in rows]}

    if kind == "test_activity":
        days = days or 30
        counts = db.execute(select(Test.outcome, func.count(Test.id))
            .where(_period(days)).group_by(Test.outcome)).all()
        outcomes = {outcome or "Unknown": count for outcome, count in counts}
        return {"total": sum(outcomes.values()), "outcomes": outcomes, "days": days}

    if kind == "most_tested_software":
        count = func.count(Test.id)
        rows = db.execute(select(Software.id, Software.name, Software.version, count)
            .join(Test, Test.software_id == Software.id).where(_period(days))
            .group_by(Software.id).order_by(count.desc(), Software.id.asc()).limit(top_n)).all()
        return {"items": [{"id": identifier, "label": name or "Unnamed software",
                            "version": version or "", "count": amount}
                           for identifier, name, version, amount in rows], "days": days}

    if kind == "most_tested_devices":
        count = func.count(Test.id)
        rows = db.execute(select(Device.id, Device.unique_id, DeviceType.label, count)
            .join(Test, Test.device_id == Device.id)
            .outerjoin(DeviceType, Device.device_type_id == DeviceType.id)
            .where(_period(days)).group_by(Device.id, DeviceType.label)
            .order_by(count.desc(), Device.id.asc()).limit(top_n)).all()
        return {"items": [{"id": identifier, "label": unique_id,
                            "type_label": type_label or "Uncategorized", "count": amount}
                           for identifier, unique_id, type_label, amount in rows], "days": days}

    if kind == "announcement":
        return {"body": settings["body"]}

    if kind == "untested_devices":
        untested = ~exists(select(Test.id).where(Test.device_id == Device.id))
        total = db.scalar(select(func.count(Device.id)).where(untested)) or 0
        rows = db.execute(select(Device.id, Device.unique_id, DeviceType.label)
            .outerjoin(DeviceType, Device.device_type_id == DeviceType.id)
            .where(untested).order_by(Device.unique_id.asc()).limit(top_n)).all()
        return {"total": total, "items": [{"id": identifier, "label": unique_id,
                 "type_label": type_label or "Uncategorized"}
                for identifier, unique_id, type_label in rows]}

    if kind == "recent_problem_tests":
        days = days or 30
        rows = db.execute(select(Test.id, Test.outcome, Test.run_at, Device.id,
                                 Device.unique_id, Software.id, Software.name)
            .join(Device, Test.device_id == Device.id)
            .join(Software, Test.software_id == Software.id)
            .where(Test.outcome.in_(("fail", "warn")), _period(days))
            .order_by(Test.run_at.desc(), Test.id.asc()).limit(top_n)).all()
        return {"items": [{"id": test_id, "outcome": outcome,
                            "run_at": run_at.isoformat(), "device_id": device_id,
                            "device_label": unique_id, "software_id": software_id,
                            "software_label": name or "Unnamed software"}
                           for test_id, outcome, run_at, device_id, unique_id, software_id, name in rows],
                "days": days}

    if kind == "scan_freshness":
        cutoff = stamp.replace(tzinfo=None) - timedelta(days=settings["threshold_days"])
        scanned = Device.last_scanned_at.is_not(None)
        recent, stale, never = db.execute(select(
            func.count(Device.id).filter(scanned, Device.last_scanned_at >= cutoff),
            func.count(Device.id).filter(scanned, Device.last_scanned_at < cutoff),
            func.count(Device.id).filter(~scanned),
        )).one()
        return {"recent": recent, "stale": stale, "never_scanned": never,
                "threshold_days": settings["threshold_days"]}

    if kind == "software_outcomes":
        count = func.count(Test.id)
        passed = func.count(Test.id).filter(Test.outcome == "pass")
        failed = func.count(Test.id).filter(Test.outcome == "fail")
        warned = func.count(Test.id).filter(Test.outcome == "warn")
        rows = db.execute(select(Software.id, Software.name, Software.version,
                                 count, passed, failed, warned)
            .join(Test, Test.software_id == Software.id).where(_period(days))
            .group_by(Software.id).having(count >= 3)
            .order_by(failed.desc(), count.desc(), Software.id.asc()).limit(top_n)).all()
        return {"items": [{"id": identifier, "label": name or "Unnamed software",
                            "version": version or "", "total": total, "pass": passed,
                            "fail": failed, "warn": warned}
                           for identifier, name, version, total, passed, failed, warned in rows],
                "days": days, "minimum_tests": 3}

    if kind == "firmware_coverage":
        firmware = func.coalesce(func.nullif(func.btrim(Device.firmware_version), ""), "Unknown")
        query = select(firmware, func.count(Device.id)).select_from(Device)
        total_query = select(func.count(Device.id)).select_from(Device)
        if settings["device_type_key"]:
            query = query.join(DeviceType, Device.device_type_id == DeviceType.id).where(
                DeviceType.key == settings["device_type_key"])
            total_query = total_query.join(DeviceType, Device.device_type_id == DeviceType.id).where(
                DeviceType.key == settings["device_type_key"])
        total = db.scalar(total_query) or 0
        visible = db.execute(query.group_by(firmware)
            .order_by(func.count(Device.id).desc(), firmware.asc()).limit(top_n)).all()
        other = total - sum(count for _, count in visible)
        items = [{"label": label, "count": count} for label, count in visible]
        if other:
            items.append({"label": "Other", "count": other})
        return {"items": items, "total": total}

    raise ValueError(f"Unsupported dashboard widget: {kind}")
