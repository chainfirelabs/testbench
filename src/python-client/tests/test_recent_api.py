"""What the API gained since the client was last brought up to date: suite
components, required custom test fields, file storage, compatible software,
tested devices and a claim's matching inventory."""

import json

import pytest

import testbench_client
from testbench_client import (
    CompatibleSoftware, SoftwareComponent, TestBenchError, TestedDevice, TestFileStorage,
    ValidationError,
)


@pytest.fixture
def suite(api):
    row = api.add_software("Microsoft 365", "2026.1")
    row["bundle_components"] = [
        {"id": "comp-outlook", "name": "Outlook", "version": "16.2", "required": True, "position": 0},
        {"id": "comp-teams", "name": "Teams", "version": "2.1", "required": False, "position": 1},
    ]
    row["bundle_parent_count"] = 0
    return row


def test_the_client_is_versioned_with_testbench(tb, api):
    assert testbench_client.__version__ == "1.14.1"
    tb.whoami()
    assert api.requests[-1].headers["user-agent"] == "testbench-client/1.14.1"


def test_a_suite_lists_its_components(tb, suite):
    software = tb.software.get("Microsoft 365")
    assert software.is_suite
    assert [str(c) for c in software.components] == ["Outlook 16.2", "Teams 2.1"]
    assert software.component("outlook").id == "comp-outlook"
    assert software.component("Outlook", "1.0") is None
    assert tb.software.components("Microsoft 365")[1].required is False
    assert not tb.software.get(tb.software.get("Microsoft 365").id).raw.get("missing")


def test_a_run_is_recorded_against_a_component(tb, api, suite):
    api.add_device("dev-1")
    outlook = tb.software.get("Microsoft 365").component("Outlook")
    by_record = tb.tests.create(device="dev-1", software="Microsoft 365", outcome="pass", component=outlook)
    assert api.tests[-1]["component_id"] == "comp-outlook"
    assert (by_record.component_name, by_record.component_version) == ("Outlook", "16.2")

    by_name = tb.tests.create(device="dev-1", software="Microsoft 365", outcome="fail",
                              component="Word", component_version="16.0")
    assert (by_name.component_name, by_name.component_version) == ("Word", "16.0")

    assert [t.id for t in tb.tests.list(component=outlook)] == [by_record.id]
    assert [t.id for t in tb.tests.list(component="Word", component_version="16.0")] == [by_name.id]


def test_a_component_version_needs_a_component(tb, api, suite):
    api.add_device("dev-1")
    with pytest.raises(ValueError, match="needs a component"):
        tb.tests.create(device="dev-1", software="Microsoft 365", outcome="pass", component_version="1")
    assert api.tests == []


def test_a_run_moves_to_another_component_by_id(tb, api, suite):
    api.add_device("dev-1")
    run = tb.tests.create(device="dev-1", software="Microsoft 365", outcome="pass")
    teams = tb.software.get("Microsoft 365").component("Teams")
    with pytest.raises(testbench_client.NotFound):  # the fake does not amend runs
        tb.tests.update(run, component=teams)
    patch = api.requests[-1]
    assert patch.method == "PATCH"
    assert json.loads(patch.content) == {"component_id": "comp-teams"}


def test_required_custom_test_fields_are_listed_and_enforced(tb, api):
    api.add_device("dev-1")
    api.add_software("nmap")
    api.entity_fields["tests"][1]["required"] = True
    lab = next(f for f in tb.entity_fields("tests") if f["key"] == "lab")
    assert lab["required"] and lab["storage"] == "data"

    with pytest.raises(ValidationError, match="Lab is required"):
        tb.tests.create(device="dev-1", software="nmap", outcome="pass")
    run = tb.tests.create(device="dev-1", software="nmap", outcome="pass", misc_data={"lab": "East"})
    assert run.misc_data == {"lab": "East"}


def test_file_storage_reports_whether_runs_can_carry_files(tb, api):
    storage = tb.tests.file_storage()
    assert isinstance(storage, TestFileStorage)
    assert (storage.enabled, storage.output_max_bytes, storage.artifact_max_bytes) == (True, 64, 1024)

    api.file_storage = None  # a server from before the endpoint existed
    assert tb.tests.file_storage().enabled is True


def test_a_run_with_files_is_not_recorded_when_storage_is_off(tb, api):
    api.add_device("dev-1")
    api.add_software("nmap")
    api.file_storage["storage_enabled"] = False
    with pytest.raises(TestBenchError, match="no object storage"):
        tb.tests.create(device="dev-1", software="nmap", outcome="pass", output="ok\n")
    assert api.tests == []
    # Without files nothing needs storage, and the question is not even asked.
    tb.tests.create(device="dev-1", software="nmap", outcome="pass")
    assert len(api.tests) == 1
    assert not any(r.url.path.endswith("/file-storage") for r in api.requests[-3:])


def test_files_still_upload_against_a_server_without_the_storage_endpoint(tb, api):
    api.add_device("dev-1")
    api.add_software("nmap")
    api.file_storage = None
    run = tb.tests.create(device="dev-1", software="nmap", outcome="pass", output="ok\n")
    assert run.output_filename == "output.txt"


def test_compatible_software_is_the_servers_match(tb, api):
    api.add_device("dev-1", make="MikroTik", model="RB4011i")
    routeros = api.add_software("RouterOS", "7.15")
    api.add_vendor_device(routeros, "MikroTik", None, support_status="partial")
    api.add_vendor_device(api.add_software("Other", "1"), "Cisco", "ISR")

    matches = tb.devices.compatible_software("dev-1")
    assert len(matches) == 1
    match = matches[0]
    assert isinstance(match, CompatibleSoftware)
    assert (str(match.software), match.support_status, match.matched_on) == ("RouterOS 7.15", "partial", ["make"])
    assert not match.is_supported
    claim = match.claims[0]
    assert (claim.software_name, claim.software_version, claim.make) == ("RouterOS", "7.15", "MikroTik")
    assert match.fields == [{"key": "make", "label": "Make"}]


def test_compatible_software_for_an_unknown_device_suggests(tb, api):
    api.add_device("dev-0042")
    with pytest.raises(testbench_client.UnknownDevice):
        tb.devices.compatible_software("dev-9999")


def test_tested_devices_come_from_runs(tb, api):
    api.add_device("dev-1")
    api.add_device("dev-2")
    api.add_software("nmap")
    tb.tests.record("dev-1", "nmap", True)
    tb.tests.record("dev-1", "nmap", False)
    tb.tests.record("dev-2", "nmap", True)

    tested = tb.software.tested_devices("nmap")
    assert all(isinstance(t, TestedDevice) for t in tested)
    assert [(t.device.unique_id, t.test_count, t.outcomes) for t in tested] == [
        ("dev-1", 2, {"pass": 1, "fail": 1}), ("dev-2", 1, {"pass": 1})]
    assert len(tb.software.tested_devices("nmap", limit=1)) == 1


def test_tested_devices_page_through(api):
    import httpx
    from testbench_client import TestBench
    api.add_software("nmap")
    for n in range(5):
        api.add_device(f"dev-{n}")
    client = TestBench("http://tb.test", api_key="tb_prefix_secret",
                       transport=httpx.MockTransport(api.handler), page_size=2)
    for n in range(5):
        client.tests.record(f"dev-{n}", "nmap", True)
    assert [t.device.unique_id for t in client.software.tested_devices("nmap")] == [
        f"dev-{n}" for n in range(5)]
    client.close()


def test_a_claim_names_its_inventory_and_pages_through_it(tb, api):
    software = api.add_software("RouterOS", "7.15")
    for n in range(3):
        api.add_device(f"dev-{n}", make="MikroTik", model="RB4011i")
    api.add_device("dev-other", make="Cisco", model="ISR")
    api.add_vendor_device(software, "MikroTik", "RB4011i",
                          inventory_match_count=3, inventory_devices=["dev-0", "dev-1"])

    claim = tb.vendor_devices.list()[0]
    assert (claim.inventory_match_count, claim.inventory_devices) == (3, ["dev-0", "dev-1"])
    assert tb.vendor_devices.inventory_matches(claim) == ["dev-0", "dev-1", "dev-2"]
    assert tb.vendor_devices.inventory_matches(claim, limit=2) == ["dev-0", "dev-1"]
    assert all(int(r.url.params.get("page_size", 0)) <= 100
               for r in api.requests if r.url.path.endswith("/inventory-matches"))


def test_a_device_keeps_its_link_overrides(tb, api):
    api.add_device("dev-1", link_overrides={"wan_ip": "https://10.0.0.1:8443"})
    assert tb.devices.get("dev-1").link_overrides == {"wan_ip": "https://10.0.0.1:8443"}


def test_search_finds_components_across_kinds(tb, api, suite):
    api.search_result = {
        "devices": [{**api.add_device("dev-1"), "tested_components": [
            {"test_id": "t1", "name": "Outlook", "version": "16.2", "software_name": "Microsoft 365",
             "software_version": "2026.1", "outcome": "pass"}]}],
        "software": [{**suite, "matching_components": [suite["bundle_components"][0]]}],
        "tests": [], "vendor_devices": [],
        "devices_total": 1, "software_total": 1, "tests_total": 0, "vendor_devices_total": 0,
    }
    found = tb.search("Outlook", limit=500)
    assert api.requests[-1].url.params["q"] == "Outlook"
    assert api.requests[-1].url.params["limit"] == "200"
    assert [c.name for c in found.software[0].matching_components] == ["Outlook"]
    assert isinstance(found.software[0].components[0], SoftwareComponent)
    assert found.devices[0].tested_components[0]["outcome"] == "pass"
    assert (found.devices_total, found.tests_total) == (1, 0)
    with pytest.raises(ValueError):
        tb.search("  ")
