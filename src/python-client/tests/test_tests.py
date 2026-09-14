"""Recording test results."""

from datetime import date, datetime

import pytest

from testbench_client import UnknownSoftware


def test_create_resolves_names_to_ids(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap", "7.94")
    result = tb.tests.create(device="dev-0042", software="nmap", outcome="pass")
    assert result.device_unique_id == "dev-0042"
    assert result.software_name == "nmap"
    assert result.outcome == "pass"


def test_create_snapshots_the_current_version(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap", "7.90", created_at="2026-01-01T00:00:00")
    api.add_software("nmap", "7.94", created_at="2026-02-01T00:00:00")
    assert tb.tests.create(device="dev-0042", software="nmap", outcome="pass").software_version == "7.94"


def test_create_can_pin_an_older_version(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap", "7.90", created_at="2026-01-01T00:00:00")
    api.add_software("nmap", "7.94", created_at="2026-02-01T00:00:00")
    result = tb.tests.create(device="dev-0042", software="nmap",
                             software_version="7.90", outcome="pass")
    assert result.software_version == "7.90"


def test_unknown_version_names_the_ones_that_exist(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap", "7.94")
    with pytest.raises(UnknownSoftware, match="Known versions: 7.94"):
        tb.tests.create(device="dev-0042", software="nmap",
                        software_version="0.1", outcome="pass")


def test_bad_outcome_is_rejected_before_the_request(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap")
    before = len(api.requests)
    with pytest.raises(ValueError, match="outcome must be one of"):
        tb.tests.create(device="dev-0042", software="nmap", outcome="passed")
    assert len(api.requests) == before


def test_record_maps_a_boolean(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap")
    assert tb.tests.record("dev-0042", "nmap", True).outcome == "pass"
    assert tb.tests.record("dev-0042", "nmap", False).outcome == "fail"


def test_run_at_is_a_date(tb, api):
    # `run_at` is a DATE column: a test is evidence dated to a day, and the
    # hour it started was never used. A datetime is accepted and truncated
    # rather than rejected, so existing callers keep working.
    api.add_device("dev-0042")
    api.add_software("nmap")
    result = tb.tests.create(device="dev-0042", software="nmap",
                             outcome="pass", run_at=date(2026, 3, 1))
    assert result.run_at == date(2026, 3, 1)

    from_datetime = tb.tests.create(device="dev-0042", software="nmap",
                                    outcome="pass",
                                    run_at=datetime(2026, 3, 2, 12, 0))
    assert from_datetime.run_at == date(2026, 3, 2)


def test_misc_data_payload_round_trips(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap")
    payload = {"duration_s": 12.4, "packets": 900}
    assert tb.tests.create(device="dev-0042", software="nmap",
                           outcome="pass", misc_data=payload).misc_data == payload


def test_resolution_is_cached(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap")
    tb.tests.create(device="dev-0042", software="nmap", outcome="pass")
    before = len(api.requests)
    tb.tests.create(device="dev-0042", software="nmap", outcome="fail")
    # One POST, no repeat lookups of the device or the software.
    assert len(api.requests) - before == 1


def test_clear_cache_forces_re_resolution(tb, api):
    api.add_device("dev-0042")
    api.add_software("nmap")
    tb.tests.create(device="dev-0042", software="nmap", outcome="pass")
    tb.clear_cache()
    before = len(api.requests)
    tb.tests.create(device="dev-0042", software="nmap", outcome="pass")
    assert len(api.requests) - before > 1


def test_list_filters_by_device(tb, api):
    api.add_device("dev-0042")
    api.add_device("dev-0043")
    api.add_software("nmap")
    tb.tests.create(device="dev-0042", software="nmap", outcome="pass")
    tb.tests.create(device="dev-0043", software="nmap", outcome="fail")
    assert [t.outcome for t in tb.tests.for_device("dev-0042")] == ["pass"]
