"""Vendor claims: the catalogue, one software's list, and the distinction."""

import pytest

from testbench_client import VendorDevice


@pytest.fixture
def catalogue(api):
    """Two software, three versions between them, five claims."""
    backup = api.add_software("Backup-Restore", "1.0", created_at="2026-01-01T00:00:00")
    backup["is_latest"] = False
    backup_2 = api.add_software("Backup-Restore", "2.0", created_at="2026-02-01T00:00:00")
    guard = api.add_software("NetGuard", "3.1")
    api.add_vendor_device(backup, "Cisco", "ISR 4331")
    api.add_vendor_device(backup, "Juniper", "SRX300", "partial")
    api.add_vendor_device(backup_2, "Cisco", "ISR 4331")
    api.add_vendor_device(guard, "Cisco", "Catalyst 9300", "unsupported")
    api.add_vendor_device(guard, "MikroTik", "RB4011i")
    return api


def test_the_catalogue_spans_every_software(tb, catalogue):
    rows = tb.vendor_devices.list()
    assert len(rows) == 5
    assert {(r.software_name, r.make) for r in rows} == {
        ("Backup-Restore", "Cisco"), ("Backup-Restore", "Juniper"),
        ("NetGuard", "Cisco"), ("NetGuard", "MikroTik"),
    }


def test_each_claim_names_the_software_version_that_makes_it(tb, catalogue):
    by_version = {
        r.software_version: r.software_is_latest
        for r in tb.vendor_devices.list(software="Backup-Restore", make="Cisco")
    }
    # A claim on a superseded version is still a claim, and says so.
    assert by_version == {"1.0": False, "2.0": True}


def test_search_matches_hardware_and_software_alike(tb, catalogue):
    assert len(tb.vendor_devices.list(search="ISR")) == 2
    assert len(tb.vendor_devices.list(search="netguard")) == 2


def test_named_filters_narrow_the_catalogue(tb, catalogue):
    rows = tb.vendor_devices.list(make="Cisco", support_status="supported")
    assert len(rows) == 2
    assert all(r.model == "ISR 4331" for r in rows)


def test_a_software_name_covers_every_version_of_it(tb, catalogue):
    assert len(tb.vendor_devices.list(software="Backup-Restore")) == 3


def test_an_unknown_support_status_fails_before_the_request(tb, catalogue):
    before = len(catalogue.requests)
    with pytest.raises(ValueError, match="support_status must be one of"):
        tb.vendor_devices.list(support_status="maybe")
    assert len(catalogue.requests) == before


def test_one_versions_own_list_is_that_version_and_not_its_ancestor(tb, catalogue):
    # A bare name gives the current version, and versions do not inherit: 2.0
    # copied 1.0's list at creation and the two have diverged since.
    rows = tb.vendor_devices.for_software("Backup-Restore")
    assert len(rows) == 1
    assert (rows[0].software_name, rows[0].software_version) == ("Backup-Restore", "2.0")
    assert rows[0].software_is_latest is True


def test_one_versions_list_still_takes_the_catalogue_filters(tb, catalogue):
    # The filters compose rather than being dropped by the narrowing — which is
    # why this asks the catalogue by id rather than the per-software endpoint.
    assert tb.vendor_devices.for_software("Backup-Restore", "1.0", search="Juniper") != []
    assert tb.vendor_devices.for_software("Backup-Restore", "1.0", support_status="partial") != []
    assert tb.vendor_devices.for_software("Backup-Restore", "2.0", search="Juniper") == []


def test_an_older_version_can_be_read_on_its_own(tb, catalogue):
    rows = tb.vendor_devices.for_software("Backup-Restore", "1.0")
    assert {r.make for r in rows} == {"Cisco", "Juniper"}
    assert all(r.software_is_latest is False for r in rows)


def test_claims_about_a_device_are_found_by_its_make_and_model(tb, api, catalogue):
    api.add_device("dev-1", make="Cisco", model="ISR 4331")
    rows = tb.vendor_devices.for_device("dev-1")
    assert len(rows) == 2
    assert {r.software_name for r in rows} == {"Backup-Restore"}


def test_supported_only_drops_the_vendors_qualified_answers(tb, api, catalogue):
    api.add_device("dev-2", make="Juniper", model="SRX300")
    assert len(tb.vendor_devices.for_device("dev-2")) == 1
    # "partial" is a claim, but it is not "supported".
    assert tb.vendor_devices.for_device("dev-2", supported_only=True) == []


def test_a_device_with_no_make_or_model_matches_nothing(tb, api, catalogue):
    api.add_device("dev-3", make=None, model=None)
    before = len(catalogue.requests)
    assert tb.vendor_devices.for_device("dev-3") == []
    # One request to read the device, and no catalogue search: every claim in
    # the catalogue is not the answer to "what supports this?".
    assert len(catalogue.requests) == before + 1


def test_a_claim_is_not_evidence(tb, api, catalogue):
    """The distinction the module exists to protect, stated as a test."""
    api.add_device("dev-4", make="MikroTik", model="RB4011i")
    claims = tb.vendor_devices.for_device("dev-4")
    assert [c.software_name for c in claims] == ["NetGuard"]
    # NetGuard claims support for it; nobody has ever run it.
    assert tb.tests.for_device("dev-4") == []


def test_a_record_reads_as_a_sentence(tb, catalogue):
    row = next(r for r in tb.vendor_devices.list(make="MikroTik"))
    assert str(row) == "MikroTik RB4011i: supported (NetGuard 3.1)"
    assert row.is_supported is True


def test_unknown_response_fields_are_kept_on_raw(tb, api, catalogue):
    api.vendor_devices[0]["future_column"] = "kept"
    row = next(r for r in tb.vendor_devices.list(search="ISR 4331"))
    assert row.raw["future_column"] == "kept"
    assert isinstance(row, VendorDevice)
