"""A fake TestBench, good enough to test the client against.

The point of testing against a fake rather than a live stack is the failure
modes: a device someone else grabs mid-checkout, a 500 that succeeds on retry,
a page boundary. Those are hard to stage against a real server and trivial here.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from testbench_client import TestBench


class _Rejected(Exception):
    """A 422 the fake server would return, raised from where it is decided."""


class FakeAPI:
    """Routes the handful of endpoints this client touches."""

    def __init__(self) -> None:
        self.devices: dict[str, dict] = {}
        self.software: dict[str, dict] = {}
        self.tests: list[dict] = []
        self.vendor_devices: list[dict] = []
        self.me = {
            "id": "u1", "username": "tester1", "role": "tester", "email": None,
            "permissions": ["devices.edit", "software.edit", "tests.edit", "views.save"],
        }
        self.requests: list[httpx.Request] = []
        # Queue of (status, body) to serve before behaving normally, for
        # exercising retries.
        self.failures: list[tuple[int, dict]] = []
        # Called just before a PATCH is applied, to simulate a concurrent actor.
        self.on_patch = None
        # The dynamic device schema this installation publishes. Routers carry
        # a WAN address; phones carry an IMEI and neither has the other's.
        self.device_types = [
            {"id": "type-router", "key": "router", "label": "Routers", "enabled": True,
             "position": 0, "device_count": 0, "configuration_source": "gui",
             "field_count": 4, "required_field_keys": ["wan_ip"], "plugin_ids": ["network-scan"]},
            {"id": "type-mobile", "key": "mobile", "label": "Mobile Phones", "enabled": True,
             "position": 10, "device_count": 0, "configuration_source": "yaml",
             "field_count": 4, "required_field_keys": ["imei"], "plugin_ids": []},
            {"id": "type-retired", "key": "retired", "label": "Retired", "enabled": False,
             "position": 20, "device_count": 0, "configuration_source": "gui",
             "field_count": 2, "required_field_keys": [], "plugin_ids": []},
        ]
        base = [
            {"key": "unique_id", "label": "Unique ID", "type": "text", "required": True,
             "visible": True, "storage": "column", "options": [], "scope": "global"},
            {"key": "status", "label": "Status", "type": "select", "required": True,
             "visible": True, "storage": "data", "options": ["available"], "scope": "global"},
        ]
        self.schemas = {
            "global": {"revision": 7, "device_type": None, "fields": base, "plugins": []},
            "router": {"revision": 7, "device_type": {"id": "type-router", "key": "router",
                                                      "label": "Routers"},
                       "fields": base + [{"key": "wan_ip", "label": "WAN IP", "type": "text",
                                          "required": True, "visible": True, "storage": "data",
                                          "options": [], "scope": "type",
                                          "role": "scan_address_wan"}],
                       "plugins": ["network-scan"]},
            "mobile": {"revision": 7, "device_type": {"id": "type-mobile", "key": "mobile",
                                                      "label": "Mobile Phones"},
                       "fields": base + [{"key": "imei", "label": "IMEI", "type": "text",
                                          "required": True, "visible": True, "storage": "data",
                                          "options": [], "scope": "type"}],
                       "plugins": []},
        }
        self.actions = [
            {"plugin_id": "network-scan", "id": "network-scan.scan-device", "label": "Scan",
             "risk": "read_only", "available": True, "unavailable_reason": None},
            {"plugin_id": "device-reboot", "id": "device-reboot.reboot", "label": "Reboot",
             "risk": "disruptive", "available": False,
             "unavailable_reason": "Device Reboot is not enabled for the Routers device type"},
        ]

    def as_role(self, role: str, permissions: list[str] | None = None) -> None:
        """Sign the fake key in as a role granting `permissions`.

        Both are set together on purpose: an installation defines its own
        roles, so a name and a permission set that disagree describe nothing
        real. Pass `permissions=None` for a role that grants nothing.
        """
        self.me["role"] = role
        self.me["permissions"] = list(permissions or [])

    def add_device(self, unique_id: str, **fields) -> dict:
        row = {
            "id": f"uuid-{unique_id}", "unique_id": unique_id, "status": "available",
            "location": "Lab A", "make": "MikroTik", "model": "RB4011i",
            "online_status": False, "misc_data": {}, "data": {"status": "available"},
            "device_type_id": None, "device_type_key": None, "device_type_label": None,
            "checked_out_by": None, "checked_out_by_username": None,
            "checked_out_at": None, "checkout_purpose": None, "checkout_due": None,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": None,
        }
        row.update(fields)
        self.devices[unique_id] = row
        return row

    def add_software(self, name: str, version: str = "1.0", created_at: str = "2026-01-01T00:00:00") -> dict:
        row = {
            "id": f"uuid-{name}-{version}", "name": name, "version": version,
            "misc_data": {}, "version_count": 1, "is_latest": True,
            "vendor_device_count": 0, "created_at": created_at, "updated_at": None,
        }
        self.software[row["id"]] = row
        return row

    def add_vendor_device(self, software: dict, make: str, model: str,
                          support_status: str = "supported", **fields) -> dict:
        """A vendor's claim about hardware, attached to one software version."""
        row = {
            "id": f"vd-{len(self.vendor_devices) + 1}",
            "software_id": software["id"],
            "make": make, "model": model,
            "firmware_version": None, "hardware_version": None, "architecture": None,
            "support_status": support_status, "source": None, "notes": None,
            "misc_data": {}, "created_at": "2026-01-01T00:00:00", "updated_at": None,
        }
        row.update(fields)
        self.vendor_devices.append(row)
        return row

    def _catalog_row(self, row: dict) -> dict:
        """A claim as the cross-software catalogue returns it: with its software.

        The per-software endpoint does not repeat this, which is the difference
        the client has to paper over — see `VendorDevices.for_software`.
        """
        software = self.software.get(row["software_id"], {})
        return {
            **row,
            "software_name": software.get("name", ""),
            "software_version": software.get("version", ""),
            "software_is_latest": bool(software.get("is_latest", True)),
        }

    # -- routing ----------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures:
            status, body = self.failures.pop(0)
            return httpx.Response(status, json=body)
        path = request.url.path.replace("/api/v1", "", 1)
        params = dict(request.url.params)
        body = json.loads(request.content) if request.content else {}
        method = request.method

        if path == "/auth/me":
            return httpx.Response(200, json=self.me)

        # Above /devices/{key}, exactly as the server declares it — otherwise
        # "overdue" is looked up as a device unique_id and 404s.
        if path == "/devices/overdue" and method == "GET":
            rows = sorted(
                (r for r in self.devices.values() if self._is_overdue(r)),
                key=lambda r: r["checkout_due"],
            )
            min_days = int(params.get("min_days", 1))
            rows = [r for r in rows if self._days_overdue(r) >= min_days]
            return httpx.Response(200, json=self._page(rows, params))

        if path == "/devices" and method == "GET":
            rows = list(self.devices.values())
            if params.get("overdue") is not None:
                want = str(params["overdue"]).lower() in ("true", "1")
                rows = [r for r in rows if self._is_overdue(r) == want]
            if params.get("status"):
                rows = [r for r in rows if r["status"] == params["status"]]
            if params.get("device_type"):
                rows = [r for r in rows if r.get("device_type_key") == params["device_type"]]
            if params.get("search"):
                rows = [r for r in rows if params["search"].lower() in r["unique_id"].lower()]
            return httpx.Response(200, json=self._page(rows, params))

        if path == "/device-types" and method == "GET":
            rows = self.device_types
            if not params.get("include_disabled"):
                rows = [r for r in rows if r.get("enabled", True)]
            return httpx.Response(200, json=rows)

        if path.startswith("/device-schema/") or path == "/device-schema/global":
            key = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self.schemas.get(key, self.schemas["global"]))

        # Above the /devices/{key} branch: otherwise "dev-1/actions" is looked
        # up as a device unique_id and 404s.
        if path.startswith("/devices/") and path.endswith("/actions") and method == "GET":
            key = path.split("/")[2]
            row = self.devices.get(key) or next((d for d in self.devices.values() if d["id"] == key), None)
            if row is None:
                return httpx.Response(404, json={"detail": "Device not found"})
            return httpx.Response(200, json={"device_id": row["id"], "actions": self.actions})

        if path.startswith("/devices/"):
            key = path.split("/", 2)[2]
            row = self.devices.get(key) or next(
                (d for d in self.devices.values() if d["id"] == key), None
            )
            if row is None:
                return httpx.Response(404, json={"detail": "Device not found"})
            if method == "GET":
                return httpx.Response(200, json={**row, "all_tests": [], "verified_tests": []})
            if method == "PATCH":
                if self.on_patch:
                    self.on_patch(row)
                # Fields first, then the status — the order the server uses
                # (api/devices.py), and the reason a checkout can supply its
                # purpose and return date in the same request as the status.
                for k, v in body.items():
                    if k != "status":
                        row[k] = v
                try:
                    self._apply_status(row, body.get("status"))
                except _Rejected as exc:
                    return httpx.Response(422, json={"detail": str(exc)})
                return httpx.Response(200, json=row)

        if path == "/vendor-devices" and method == "GET":
            rows = [self._catalog_row(r) for r in self.vendor_devices]
            if params.get("search"):
                needle = params["search"].lower()
                rows = [
                    r for r in rows
                    if any(needle in str(r.get(k) or "").lower()
                           for k in ("make", "model", "software_name", "software_version"))
                ]
            # A software *name* covers every version of it, which is the whole
            # reason the API takes a name here rather than an id.
            if params.get("software"):
                rows = [r for r in rows
                        if r["software_name"].lower() == params["software"].lower()]
            # One version exactly, as opposed to every version of a name.
            if params.get("software_id"):
                rows = [r for r in rows if r["software_id"] == params["software_id"]]
            for key in ("make", "model", "firmware_version", "hardware_version",
                        "architecture", "support_status"):
                if params.get(key):
                    rows = [r for r in rows
                            if str(r.get(key) or "").lower() == params[key].lower()]
            rows.sort(key=lambda r: (str(r.get(params.get("sort", "make")) or ""), r["id"]))
            return httpx.Response(200, json=self._page(rows, params))

        if path == "/software" and method == "GET":
            rows = list(self.software.values())
            if params.get("search"):
                rows = [r for r in rows if params["search"].lower() in r["name"].lower()]
            return httpx.Response(200, json=self._page(rows, params))

        if path.endswith("/versions") and method == "GET":
            key = path.split("/")[2]
            name = self._software_name(key)
            rows = sorted(
                (r for r in self.software.values() if r["name"] == name),
                key=lambda r: r["created_at"], reverse=True,
            )
            return httpx.Response(200, json=rows)

        if path.startswith("/software/") and method == "GET":
            key = path.split("/", 2)[2]
            row = self.software.get(key)
            if row is None:
                # A bare name addresses the highest numeric version.
                matches = sorted(
                    (r for r in self.software.values() if r["name"].lower() == key.lower()),
                    key=lambda r: r["created_at"], reverse=True,
                )
                row = matches[0] if matches else None
            if row is None:
                return httpx.Response(404, json={"detail": "Software not found"})
            return httpx.Response(200, json=row)

        if path == "/tests" and method == "POST":
            software = self.software.get(body["software_id"])
            device = next((d for d in self.devices.values() if d["id"] == body["device_id"]), None)
            if software is None:
                return httpx.Response(400, json={"detail": "Unknown software_id"})
            if device is None:
                return httpx.Response(400, json={"detail": "Unknown device_id"})
            row = {
                "id": f"test-{len(self.tests) + 1}",
                "software_id": software["id"], "software_name": software["name"],
                "software_version": body.get("software_version") or software["version"],
                "device_id": device["id"], "device_unique_id": device["unique_id"],
                "device_make": device["make"], "device_model": device["model"],
                "outcome": body["outcome"], "tag": body.get("tag", "adhoc"),
                "misc_data": body.get("misc_data") or {}, "notes": body.get("notes"),
                "run_at": body.get("run_at"), "created_at": "2026-01-01T00:00:00",
                "updated_at": None, "created_by_username": self.me["username"],
            }
            self.tests.append(row)
            return httpx.Response(201, json=row)

        if path == "/tests" and method == "GET":
            rows = list(self.tests)
            if params.get("device_id"):
                rows = [r for r in rows if r["device_id"] == params["device_id"]]
            if params.get("outcome"):
                rows = [r for r in rows if r["outcome"] == params["outcome"]]
            return httpx.Response(200, json=self._page(rows, params))

        return httpx.Response(404, json={"detail": f"no route for {method} {path}"})

    # -- helpers ----------------------------------------------------------

    def _software_name(self, key: str) -> str:
        row = self.software.get(key)
        return row["name"] if row else key

    def _apply_status(self, row: dict, status: str | None) -> None:
        """Mirror the server's transition rules, including the one that bites:
        setting checked_out on an already-checked-out device does nothing.

        Also mirrors the requirement: entering checked_out without a purpose
        and a return date is a 422, and leaving it clears both. Raises rather
        than returning a response because it is called from inside the PATCH
        handler; the caller turns it into the status code.
        """
        if not status or status == row["status"]:
            return
        if status == "checked_out":
            if not row.get("checkout_purpose") or not row.get("checkout_due"):
                raise _Rejected("Checking out a device needs a purpose and a return date.")
            row["checked_out_by"] = self.me["id"]
            row["checked_out_by_username"] = self.me["username"]
            row["checked_out_at"] = "2026-01-01"
        elif row["status"] == "checked_out":
            row["checked_out_by"] = None
            row["checked_out_by_username"] = None
            row["checked_out_at"] = None
            row["checkout_purpose"] = None
            row["checkout_due"] = None
        row["status"] = status

    @staticmethod
    def _is_overdue(row: dict) -> bool:
        """The server's rule: checked out, has a due date, date has passed."""
        return (
            row["status"] == "checked_out"
            and bool(row.get("checkout_due"))
            and date.fromisoformat(row["checkout_due"]) < date.today()
        )

    @classmethod
    def _days_overdue(cls, row: dict) -> int:
        if not cls._is_overdue(row):
            return 0
        return (date.today() - date.fromisoformat(row["checkout_due"])).days

    @staticmethod
    def _page(rows: list[dict], params: dict) -> dict:
        page = int(params.get("page", 1))
        size = int(params.get("page_size", 50))
        start = (page - 1) * size
        return {"items": rows[start:start + size], "total": len(rows),
                "page": page, "page_size": size}


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def tb(api: FakeAPI) -> TestBench:
    client = TestBench(
        "http://tb.test", api_key="tb_prefix_secret",
        transport=httpx.MockTransport(api.handler),
        retries=2,
    )
    yield client
    client.close()
