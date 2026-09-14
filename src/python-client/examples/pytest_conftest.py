"""pytest integration — copy the contents into your own `conftest.py`.

Gives you a reserved device for the session and records a TestBench result
for every test that used it, with no bookkeeping in the tests themselves:

    def test_throughput(device, record):
        result = measure(device.wan_ip)
        record.data["mbps"] = result.mbps
        assert result.mbps > 100
"""

from datetime import date, timedelta

import os

import pytest

from testbench_client import TestBench, DeviceUnavailable

SOFTWARE = os.environ.get("TB_SOFTWARE", "curl-smoke")


@pytest.fixture(scope="session")
def tb():
    """One client per session: it holds a connection pool and a resolution cache."""
    with TestBench.from_env() as client:
        if not client.identity.can_write:
            pytest.skip(f"TB_API_KEY is {client.identity.role}; recording needs tester")
        yield client


@pytest.fixture(scope="session")
def device(tb):
    """Reserve one available device for the whole session, and always give it back.

    Pin a specific one with TB_DEVICE=dev-0042 — otherwise the first free one wins.
    """
    wanted = os.environ.get("TB_DEVICE")
    candidates = [wanted] if wanted else [d.unique_id for d in tb.devices.list(status="available")]
    for candidate in candidates:
        try:
            with tb.devices.reserved(candidate, purpose="pytest session",
                                     due=date.today() + timedelta(days=1)) as held:
                yield held
                return
        except DeviceUnavailable:
            continue
    pytest.skip("no device available to reserve")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Stash each phase's report so the fixture below can see the outcome.

    A fixture's teardown runs before pytest has finalised the report, so the
    outcome has to be captured here on the way past.
    """
    outcome = yield
    setattr(item, f"tb_report_{call.when}", outcome.get_result())


@pytest.fixture
def record(request, tb, device):
    """Record one TestBench result per test, from its actual outcome.

    Yields a mutable holder — put your measurements on `.data` and they end up
    in the result's JSON payload, which is what makes these rows worth querying
    later.
    """

    class Recorder:
        def __init__(self):
            self.data = {}
            self.software = SOFTWARE
            self.tag = "acceptance"
            self.skip = False

    recorder = Recorder()
    yield recorder

    if recorder.skip:
        return
    report = getattr(request.node, "tb_report_call", None)
    setup = getattr(request.node, "tb_report_setup", None)
    if report is None or (setup is not None and setup.failed):
        # Never ran, so there is no evidence to record. A test that errored in
        # setup says nothing about the device.
        return
    outcome = "pass" if report.passed else "fail"
    tb.tests.create(
        device=device,
        software=recorder.software,
        outcome=outcome,
        tag=recorder.tag,
        misc_data={"test": request.node.nodeid, "duration_s": round(report.duration, 3), **recorder.data},
        notes=None if report.passed else str(report.longrepr)[:500],
    )
