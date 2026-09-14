import httpx
import pytest

from testbench_client import TestBench


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_does_not_fetch(limit):
    def unexpected(request):
        pytest.fail("A nonpositive limit should not make a request")

    with TestBench(api_key="tb_test_secret", transport=httpx.MockTransport(unexpected)) as client:
        assert client.devices.list(limit=limit) == []
        assert client.software.list(limit=limit) == []
        assert client.tests.list(limit=limit) == []
