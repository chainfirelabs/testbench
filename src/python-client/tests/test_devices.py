"""Devices: listing, lookup and the checkout workflow."""

from datetime import date, timedelta

import httpx
import pytest

from testbench_client import DeviceUnavailable, UnknownDevice


def test_list_returns_devices(tb, api):
    api.add_device("dev-0001")
    api.add_device("dev-0002", status="broken")
    devices = tb.devices.list()
    assert [d.unique_id for d in devices] == ["dev-0001", "dev-0002"]


def test_list_filters_by_status(tb, api):
    api.add_device("dev-0001")
    api.add_device("dev-0002", status="broken")
    assert [d.unique_id for d in tb.devices.list(status="broken")] == ["dev-0002"]


def test_list_filters_and_maps_device_type(tb, api):
    api.add_device("router-1", device_type_id="type-1", device_type_key="router", device_type_label="Routers")
    api.add_device("phone-1", device_type_id="type-2", device_type_key="mobile", device_type_label="Mobile Phones")
    devices = tb.devices.list(device_type="mobile")
    assert [device.unique_id for device in devices] == ["phone-1"]
    assert devices[0].device_type_id == "type-2"
    assert devices[0].device_type_label == "Mobile Phones"


def test_list_rejects_a_status_that_is_not_a_status(tb):
    # The API would return an empty page, which reads as "none exist".
    with pytest.raises(ValueError, match="status must be one of"):
        tb.devices.list(status="braken")


def test_list_pages_through_everything(tb, api):
    for i in range(25):
        api.add_device(f"dev-{i:04d}")
    tb.page_size = 10
    assert len(tb.devices.list()) == 25


def test_limit_stops_paging_early(tb, api):
    for i in range(25):
        api.add_device(f"dev-{i:04d}")
    tb.page_size = 10
    before = len(api.requests)
    assert len(tb.devices.list(limit=3)) == 3
    assert len(api.requests) - before == 1


def test_unknown_device_suggests_near_misses(tb, api):
    api.add_device("dev-0042")
    with pytest.raises(UnknownDevice) as exc:
        tb.devices.get("dev-004")
    assert "dev-0042" in exc.value.suggestions
    assert "Did you mean" in str(exc.value)


def test_online_is_read_from_online_status(tb, api):
    api.add_device("dev-0001", online_status=True)
    assert tb.devices.get("dev-0001").online is True


def test_timestamps_are_timezone_aware(tb, api):
    # A naive datetime here would blow up the first time a caller compared it
    # against datetime.now(timezone.utc).
    api.add_device("dev-0001")
    assert tb.devices.get("dev-0001").created_at.tzinfo is not None


# Every checkout needs a purpose and a return date; the API refuses the
# transition without them. Shared here so each test can be about the
# behaviour it is actually testing.
CHECKOUT = {"purpose": "Regression suite", "due": "2099-01-01"}


def test_check_out_claims_the_device(tb, api):
    api.add_device("dev-0001")
    device = tb.devices.check_out("dev-0001", **CHECKOUT)
    assert device.is_checked_out
    assert device.checked_out_by_username == "tester1"


def test_check_out_is_idempotent_when_already_ours(tb, api):
    api.add_device("dev-0001")
    tb.devices.check_out("dev-0001", **CHECKOUT)
    assert tb.devices.check_out("dev-0001", **CHECKOUT).is_checked_out


def test_check_out_refuses_someone_elses_device(tb, api):
    api.add_device("dev-0001", status="checked_out", checked_out_by_username="tester2")
    with pytest.raises(DeviceUnavailable, match="checked out by tester2"):
        tb.devices.check_out("dev-0001", **CHECKOUT)


def test_check_out_refuses_a_broken_device(tb, api):
    api.add_device("dev-0001", status="broken")
    with pytest.raises(DeviceUnavailable, match="is 'broken', not available"):
        tb.devices.check_out("dev-0001", **CHECKOUT)


def test_force_takes_over_a_held_device(tb, api):
    api.add_device("dev-0001", status="checked_out", checked_out_by_username="tester2")
    device = tb.devices.check_out("dev-0001", force=True, **CHECKOUT)
    assert device.checked_out_by_username == "tester1"


def test_check_out_detects_a_device_claimed_mid_flight(tb, api):
    # The API accepts a redundant status change silently, so losing this race
    # would otherwise look like a successful checkout of someone else's device.
    api.add_device("dev-0001")

    def steal(row):
        row.update(status="checked_out", checked_out_by_username="tester2")
        api.on_patch = None

    api.on_patch = steal
    with pytest.raises(DeviceUnavailable, match="claimed by tester2"):
        tb.devices.check_out("dev-0001", **CHECKOUT)


def test_check_in_returns_the_device(tb, api):
    api.add_device("dev-0001")
    tb.devices.check_out("dev-0001", **CHECKOUT)
    device = tb.devices.check_in("dev-0001")
    assert device.is_available
    assert device.checked_out_by_username is None


def test_check_in_leaves_someone_elses_device_alone(tb, api):
    api.add_device("dev-0001", status="checked_out", checked_out_by_username="tester2")
    with pytest.raises(DeviceUnavailable):
        tb.devices.check_in("dev-0001")
    assert api.devices["dev-0001"]["status"] == "checked_out"


def test_check_in_is_a_no_op_on_a_free_device(tb, api):
    api.add_device("dev-0001")
    assert tb.devices.check_in("dev-0001").is_available


def test_reserved_releases_on_success(tb, api):
    api.add_device("dev-0001")
    with tb.devices.reserved("dev-0001", **CHECKOUT) as device:
        assert device.is_checked_out
    assert api.devices["dev-0001"]["status"] == "available"


def test_reserved_releases_when_the_block_raises(tb, api):
    api.add_device("dev-0001")
    with pytest.raises(RuntimeError):
        with tb.devices.reserved("dev-0001", **CHECKOUT):
            raise RuntimeError("the suite exploded")
    assert api.devices["dev-0001"]["status"] == "available"


def test_reserved_leaves_a_status_the_block_set(tb, api):
    # A run that discovered the device is broken should not have that undone.
    api.add_device("dev-0001")
    with tb.devices.reserved("dev-0001", **CHECKOUT):
        tb.devices.set_status("dev-0001", "broken")
    assert api.devices["dev-0001"]["status"] == "broken"


def test_release_failure_does_not_mask_the_real_error(tb, api):
    api.add_device("dev-0001")
    with pytest.raises(RuntimeError, match="the real problem"):
        with tb.devices.reserved("dev-0001", **CHECKOUT):
            api.devices.clear()  # check-in will now 404
            raise RuntimeError("the real problem")


# ---------- checkout deadlines ----------


def test_check_out_requires_a_purpose_and_a_date(tb, api):
    # Refused here rather than at the API: a 422 from the server would be the
    # same answer three round trips later.
    api.add_device("dev-0001")
    with pytest.raises(ValueError, match="purpose"):
        tb.devices.check_out("dev-0001", purpose="  ", due="2099-01-01")
    with pytest.raises(ValueError, match="return date"):
        tb.devices.check_out("dev-0001", purpose="Regression suite", due=None)


def test_check_out_records_the_purpose_and_the_date(tb, api):
    api.add_device("dev-0001")
    device = tb.devices.check_out("dev-0001", purpose="Firmware soak",
                                  due=date(2099, 1, 1))
    assert device.checkout_purpose == "Firmware soak"
    assert device.checkout_due == date(2099, 1, 1)


def test_checking_out_again_extends_the_deadline(tb, api):
    api.add_device("dev-0001")
    tb.devices.check_out("dev-0001", purpose="Firmware soak", due="2099-01-01")
    again = tb.devices.check_out("dev-0001", purpose="Firmware soak, week two",
                                 due="2099-02-01")
    assert again.checkout_due == date(2099, 2, 1)
    assert again.checkout_purpose == "Firmware soak, week two"


def test_check_in_clears_the_deadline(tb, api):
    api.add_device("dev-0001")
    tb.devices.check_out("dev-0001", **CHECKOUT)
    device = tb.devices.check_in("dev-0001")
    assert device.checkout_purpose is None
    assert device.checkout_due is None


def test_overdue_is_derived_from_the_date(tb, api):
    api.add_device("dev-0001", status="checked_out",
                   checkout_due=str(date.today() - timedelta(days=3)),
                   checkout_purpose="Left on a bench")
    device = tb.devices.get("dev-0001")
    assert device.is_overdue
    assert device.days_overdue == 3

    # An available device is never overdue, whatever date it carries.
    api.add_device("dev-0002", checkout_due=str(date.today() - timedelta(days=9)))
    assert not tb.devices.get("dev-0002").is_overdue


def test_overdue_lists_the_latest_first(tb, api):
    api.add_device("dev-0001", status="checked_out",
                   checkout_due=str(date.today() - timedelta(days=2)))
    api.add_device("dev-0002", status="checked_out",
                   checkout_due=str(date.today() - timedelta(days=11)))
    api.add_device("dev-0003", status="checked_out",
                   checkout_due=str(date.today() + timedelta(days=5)))

    overdue = tb.devices.overdue()
    assert [d.unique_id for d in overdue] == ["dev-0002", "dev-0001"]
    assert [d.unique_id for d in tb.devices.overdue(min_days=5)] == ["dev-0002"]
    assert [d.unique_id for d in tb.devices.list(overdue=True)] == ["dev-0001", "dev-0002"]


# ---- the dynamic device schema, as the client sees it ----------------------


def test_device_types_are_discoverable_by_their_stable_key(tb, api):
    types = {item.key: item for item in tb.device_types()}
    assert set(types) == {"router", "mobile"}
    assert types["router"].label == "Routers"
    assert types["router"].required_field_keys == ["wan_ip"]
    assert types["router"].plugin_ids == ["network-scan"]
    # Which side owns a type matters to anything that would edit it.
    assert types["mobile"].configuration_source == "yaml"


def test_disabled_types_are_available_on_request(tb, api):
    assert "retired" in {item.key for item in tb.device_types(include_disabled=True)}


def test_the_schema_differs_per_device_type(tb, api):
    router = {f["key"] for f in tb.device_schema("router")["fields"]}
    mobile = {f["key"] for f in tb.device_schema("mobile")["fields"]}
    assert "wan_ip" in router and "wan_ip" not in mobile
    assert "imei" in mobile
    # Both inherit the global fields.
    assert {"unique_id", "status"} <= router & mobile


def test_the_global_schema_is_what_every_type_inherits(tb, api):
    schema = tb.device_schema()
    assert schema["device_type"] is None
    assert {f["key"] for f in schema["fields"]} == {"unique_id", "status"}
    assert tb.device_schema_revision() == 7


def test_installation_defined_values_travel_in_the_document(tb, api):
    api.add_device("dev-dyn", data={"status": "available", "serial_number": "SN-9"})
    device = tb.devices.get("dev-dyn")
    assert device.data["serial_number"] == "SN-9"
    # Addressed by stable key, never by label: a label can be renamed at will.
    assert device.field("serial_number") == "SN-9"
    assert device.field("not_a_field") is None


def test_devices_can_be_filtered_by_a_type_key(tb, api):
    api.add_device("dev-r", device_type_key="router")
    api.add_device("dev-m", device_type_key="mobile")
    assert [d.unique_id for d in tb.devices.list(device_type="router")] == ["dev-r"]


def test_available_actions_come_with_reasons(tb, api):
    api.add_device("dev-act", device_type_key="router")
    actions = {a["plugin_id"]: a for a in tb.devices.actions("dev-act")}
    assert actions["network-scan"]["available"]
    # Not every installed action applies to every device, and the reason is the
    # installation's policy rather than anything about the row.
    assert not actions["device-reboot"]["available"]
    assert "not enabled" in actions["device-reboot"]["unavailable_reason"]


def test_actions_for_an_unknown_device_raise_unknown_device(tb, api):
    with pytest.raises(UnknownDevice):
        tb.devices.actions("nope")


def test_a_devices_schema_is_reachable_from_the_device(tb, api):
    api.add_device("dev-sch", device_type_key="mobile")
    assert "imei" in {f["key"] for f in tb.devices.schema("dev-sch")["fields"]}
