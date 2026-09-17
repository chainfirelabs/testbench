"""Which software a device is claimed to run, from the vendor device lists.

Every software version carries a list of `vendor_devices` — hardware the vendor
says that release works against. This module runs those claims backwards: given
one device from inventory, find the claims that describe it.

This is the vendor's word, not ours. What the software has actually been run
against on this device lives in `tests`.
"""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import Device, Software, VendorDevice

# The device columns a claim can pin down, in the order the UI lists them.
# `make` is special-cased below; the rest compare straight across.
MATCH_FIELDS = ("make", "model", "firmware_version", "hardware_version", "architecture")

# Spellings of one ISA. The devices table keeps `arm64` and `aarch64` apart
# because which one a device reports is worth recording, but a vendor claiming
# either has claimed the other.
ARCH_ALIASES = {
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("arm64", "aarch64"),
    "x86_64": ("x86_64", "amd64"),
    "amd64": ("x86_64", "amd64"),
}

# Best claim wins when several describe the same device.
SUPPORT_RANK = {"supported": 0, "partial": 1, "planned": 2, "unsupported": 3}

def _is_blank(col):
    return or_(col.is_(None), func.trim(col) == "")


def _field_matches(col, value: str | None):
    """A claim matches a field when it leaves it blank, or names the same value.

    Blank is a wildcard: a claim that does not mention firmware is a claim
    about every firmware. The comparison is case- and whitespace-insensitive,
    since these values arrive from vendor spreadsheets.

    A device with no value of its own is the mirror image: only a claim that
    also leaves the field open can describe it, because there is nothing to
    agree with.
    """
    value = (value or "").strip()
    if not value:
        return _is_blank(col)
    wanted = ARCH_ALIASES.get(value.lower(), (value.lower(),))
    return or_(_is_blank(col), func.lower(func.trim(col)).in_(wanted))


def _claim_pins(vd: VendorDevice, field: str) -> bool:
    """Whether this claim actually named the field (rather than wildcarding it)."""
    if field == "make":
        return bool((vd.make or "").strip())
    return bool((getattr(vd, field) or "").strip())


def compatible_software(db: Session, device: Device) -> list[dict]:
    """Software whose vendor devices describe `device`, best support first.

    Returns one entry per software *version* — vendor device lists belong to a
    version, not to a name, so v1 and v2 of the same software can disagree
    about a device. Each entry carries every claim that matched, so the UI can
    show what the vendor actually said.
    """
    conditions = [_field_matches(VendorDevice.make, device.make)]
    conditions += [
        _field_matches(getattr(VendorDevice, f), getattr(device, f))
        for f in MATCH_FIELDS
        if f != "make"
    ]
    rows = db.execute(
        select(VendorDevice, Software)
        .join(Software, VendorDevice.software_id == Software.id)
        .where(and_(*conditions))
        .order_by(Software.name, Software.created_at.desc())
    ).all()

    grouped: dict[str, dict] = {}
    for vd, software in rows:
        pinned = [f for f in MATCH_FIELDS if _claim_pins(vd, f)]
        # A claim that pins nothing down matches every device in the fleet, so
        # it says nothing about this one. Drop it rather than pad the list.
        if not pinned:
            continue
        entry = grouped.setdefault(
            software.id,
            {"software": software, "vendor_devices": [], "matched_on": set()},
        )
        entry["vendor_devices"].append(vd)
        entry["matched_on"].update(pinned)

    items = []
    for entry in grouped.values():
        claims = sorted(
            entry["vendor_devices"],
            key=lambda v: SUPPORT_RANK.get(v.support_status, 99),
        )
        items.append({
            "software": entry["software"],
            "support_status": claims[0].support_status,
            # Ordered like MATCH_FIELDS so the UI reads make → model → firmware.
            "matched_on": [f for f in MATCH_FIELDS if f in entry["matched_on"]],
            "vendor_devices": claims,
        })

    items.sort(
        key=lambda i: (
            SUPPORT_RANK.get(i["support_status"], 99),
            # More fields agreed on is a more specific claim.
            -len(i["matched_on"]),
            i["software"].name.lower(),
            i["software"].version,
        )
    )
    return items
