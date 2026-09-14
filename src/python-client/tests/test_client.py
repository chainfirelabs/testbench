"""Connection handling, retries and error mapping."""

import httpx
import pytest

from testbench_client import (
    AuthenticationError,
    TestBench,
    PermissionDenied,
    ServerError,
    TransportError,
    ValidationError,
)
from testbench_client.client import normalise_base_url


@pytest.mark.parametrize("given, expected", [
    ("http://tb.test", "http://tb.test/api/v1"),
    ("http://tb.test/", "http://tb.test/api/v1"),
    ("http://tb.test/api/v1", "http://tb.test/api/v1"),
    ("http://tb.test/api/v1/", "http://tb.test/api/v1"),
])
def test_base_url_accepts_either_form(given, expected):
    assert normalise_base_url(given) == expected


def test_api_key_is_required():
    with pytest.raises(ValueError, match="API key is required"):
        TestBench("http://tb.test", api_key="")


def test_from_env_reads_the_environment(monkeypatch, api):
    monkeypatch.setenv("TB_API_URL", "http://tb.test")
    monkeypatch.setenv("TB_API_KEY", "tb_prefix_secret")
    with TestBench.from_env(transport=httpx.MockTransport(api.handler)) as tb:
        assert tb.whoami().username == "tester1"


def test_from_env_says_which_variable_is_missing(monkeypatch):
    monkeypatch.delenv("TB_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TB_API_KEY is not set"):
        TestBench.from_env()


def test_whoami_reports_the_effective_role(tb, api):
    api.me["role"] = "readonly"
    identity = tb.whoami()
    assert identity.role == "readonly"
    assert identity.can_write is False


def test_401_is_not_retried(tb, api):
    api.failures = [(401, {"detail": "Invalid API key"})] * 3
    with pytest.raises(AuthenticationError, match="revoked or expired"):
        tb.devices.list()
    assert len(api.requests) == 1


def test_403_explains_the_role_cap(tb, api):
    api.failures = [(403, {"detail": "Insufficient role for write access"})]
    with pytest.raises(PermissionDenied, match="lower of the role"):
        tb.devices.list()


def test_422_becomes_a_validation_error(tb, api):
    api.failures = [(422, {"detail": [{"loc": ["body", "outcome"], "msg": "field required"}]})]
    with pytest.raises(ValidationError, match="body.outcome: field required"):
        tb.devices.list()


def test_a_get_retries_through_a_500(tb, api):
    api.add_device("dev-0001")
    api.failures = [(500, {"detail": "boom"})]
    assert len(tb.devices.list()) == 1


def test_a_get_gives_up_after_the_retries(tb, api):
    api.failures = [(500, {"detail": "boom"})] * 5
    with pytest.raises(ServerError):
        tb.devices.list()
    assert len(api.requests) == 3  # the first attempt plus two retries


def test_a_write_is_never_retried(tb, api):
    # A POST that may have landed must not be sent twice; a duplicate result is
    # worse than a failure the caller can see.
    api.add_device("dev-0042")
    api.add_software("nmap")
    tb.devices.resolve("dev-0042")
    tb.software.resolve("nmap")
    api.failures = [(500, {"detail": "boom"})] * 3
    before = len(api.requests)
    with pytest.raises(ServerError):
        tb.tests.create(device="dev-0042", software="nmap", outcome="pass")
    assert len(api.requests) - before == 1


def test_transport_failure_is_wrapped():
    def explode(request):
        raise httpx.ConnectError("connection refused")

    with TestBench("http://tb.test", api_key="tb_a_b", retries=0,
                       transport=httpx.MockTransport(explode)) as tb:
        with pytest.raises(TransportError, match="Could not reach"):
            tb.devices.list()


def test_pagination_does_not_yield_a_row_twice(tb, api):
    # OFFSET paging shifts rows down when one is inserted mid-listing.
    for i in range(4):
        api.add_device(f"dev-{i:04d}")
    tb.page_size = 2
    seen = []
    for device in tb.devices.iter():
        seen.append(device.unique_id)
        if len(seen) == 2:
            api.add_device("dev-aaaa")  # sorts to the front of the fake's list
    assert len(seen) == len(set(seen))
