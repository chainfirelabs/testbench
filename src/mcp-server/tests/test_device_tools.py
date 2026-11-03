"""Device history, vendor claims for a device, owned matches and the fleet summary."""
import asyncio

import pytest

pytest.importorskip("mcp")

from tb_mcp import tools  # noqa: E402
from tb_mcp.client import NotFound  # noqa: E402

DEVICE = {"id": "d1", "unique_id": "router-1", "make": "Cisco", "model": "ISR4331"}


class StubApi:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, path, params=None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        self.calls.append((path, params))
        if path not in self.routes:
            raise NotFound("Not found", 404)
        route = self.routes[path]
        return route(params) if callable(route) else route


@pytest.fixture
def stub(monkeypatch):
    def install(routes):
        api = StubApi(routes)
        monkeypatch.setattr(tools, "api", lambda: api)
        return api
    return install


def run(coro):
    return asyncio.run(coro)


def paged(rows):
    def page(params):
        start = (params["page"] - 1) * params["page_size"]
        return {"items": rows[start:start + params["page_size"]], "total": len(rows)}
    return page


def test_device_history_reads_as_who_when_what(stub):
    entries = [
        {"id": 2, "timestamp": "2026-10-30T14:05:09", "username": "joe", "action": "device.update",
         "summary": "Device updated", "changes": [
             {"field": "firmware_version", "label": "Firmware Version", "old": "1.2", "new": "1.3"}]},
        {"id": 1, "timestamp": "2026-10-01T09:00:00", "username": "admin", "action": "device.create",
         "summary": "Device created", "changes": []},
    ]
    stub({"/devices/router-1": DEVICE, "/devices/d1/changelog": paged(entries)})
    out = run(tools.get_device_history("router-1"))
    assert out["device"].startswith("router-1")
    assert out["items"][0] == {"when": "2026-10-30 14:05", "who": "joe", "what": "Device updated",
                               "action": "device.update",
                               "changes": [{"field": "Firmware Version", "old": "1.2", "new": "1.3"}]}
    assert out["items"][1]["what"] == "Device created" and "changes" not in out["items"][1]
    assert out["total"] == 2


def test_device_history_resumes_from_any_offset_and_reports_misses(stub):
    entries = [{"id": n, "timestamp": f"2026-10-{n + 1:02d}T00:00:00", "username": "u", "action": "a",
                "summary": f"entry {n}"} for n in range(5)]
    stub({"/devices/router-1": DEVICE, "/devices/d1/changelog": paged(entries),
          "/devices": {"items": [{"unique_id": "router-1"}], "total": 1}})
    out = run(tools.get_device_history("router-1", offset=3, limit=2))
    assert [e["what"] for e in out["items"]] == ["entry 3", "entry 4"] and "next_offset" not in out
    assert run(tools.get_device_history("routr-1"))["found"] is False


def test_vendor_claims_for_a_device_hide_secret_and_hidden_fields(stub):
    claim = {"id": "c1", "software_id": "s1", "make": "Cisco", "model": "ISR4331",
             "support_status": "supported", "inventory_match_count": 3,
             "inventory_devices": ["router-1", "router-7"],
             "misc_data": {"region": "EU", "api_key": "hunter2", "internal": "x"}}
    stub({"/devices/router-1": DEVICE, "/devices/d1/compatible-software": {"device_id": "d1", "items": [{
        "software": {"id": "s1", "name": "nmap", "version": "7.94", "is_latest": False},
        "support_status": "supported", "matched_on": ["make", "model"],
        "vendor_devices": [claim],
        "fields": [{"key": "region", "sensitive": False}, {"key": "api_key", "sensitive": True}],
    }]}})
    out = run(tools.list_vendor_claims_for_device("router-1"))
    row = out["items"][0]
    assert (row["software"], row["software_version"], row["software_id"]) == ("nmap", "7.94", "s1")
    assert row["software_is_latest"] is False and row["matched_on"] == ["make", "model"]
    shown = row["claims"][0]
    assert shown["custom_fields"] == {"region": "EU"}
    assert shown["id"] == "c1" and "software_id" not in shown
    assert shown["owned_matches"] == 3 and shown["owned_preview"] == ["router-1", "router-7"]
    assert "not evidence" in out["sense"]


def test_owned_devices_matching_a_claim(stub, monkeypatch):
    devices = [{"id": f"d{n}", "unique_id": f"router-{n}"} for n in range(150)]
    api = stub({"/software/s1/vendor-devices/c1/inventory-matches": paged(devices)})
    out = run(tools.list_owned_devices_matching_claim("c1", "s1", limit=500))
    # The API's own cap of 100 per page.
    assert out["returned"] == 100 and out["total"] == 150 and out["next_offset"] == 100
    assert out["items"][0] == {"unique_id": "router-0"}
    assert all(params["page_size"] == 100 for _, params in api.calls)
    rest = run(tools.list_owned_devices_matching_claim("c1", "s1", offset=100, limit=500))
    assert rest["returned"] == 50 and "next_offset" not in rest
    assert run(tools.list_owned_devices_matching_claim("nope", "s1"))["found"] is False


def test_claim_rows_carry_what_the_matches_tool_needs():
    row = tools.vendor_device_brief({"id": "c1", "software_id": "s1", "make": "Cisco",
                                     "inventory_match_count": 0, "inventory_devices": []})
    assert row == {"id": "c1", "software_id": "s1", "make": "Cisco"}


def test_fleet_summary_counts_scan_state_and_statuses(stub):
    counts = {"available": 7, "checked_out": 2, "inventory": 0, "missing": 1, "broken": 0}
    api = stub({
        "/dashboard/fleet-summary": {"total": 10, "online": 6, "offline": 3, "never_scanned": 1,
                                     "as_of": "2026-11-02T10:11:12Z"},
        "/devices": lambda p: {"items": [], "total": counts[p["status"]]},
    })
    out = run(tools.get_fleet_summary())
    assert out == {"devices": 10, "scan": {"online": 6, "offline": 3, "never_scanned": 1},
                   "status": counts, "as_of": "2026-11-02 10:11"}
    assert all(params["page_size"] == 1 for path, params in api.calls if path == "/devices")
