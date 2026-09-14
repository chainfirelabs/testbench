"""Python client for the TestBench API.

```python
from testbench_client import TestBench

with TestBench.from_env() as tb:
    with tb.devices.reserved("dev-0042") as device:
        result = run_my_suite(device.wan_ip)
        tb.tests.record("dev-0042", "curl-smoke", result.ok, misc_data=result.metrics)
```

Authenticates with a TestBench API key (`tb_<prefix>_<secret>`), minted
per user under Profile -> API keys. The key carries a role: a `readonly` one
can list and inspect, and needs `tester` or better to check devices out or
record results.
"""

__version__ = "0.1.0"

from .client import TestBench
from .errors import (
    AuthenticationError,
    Conflict,
    TestBenchError,
    DeviceUnavailable,
    NotFound,
    PermissionDenied,
    ServerError,
    TransportError,
    UnknownDevice,
    UnknownSoftware,
    ValidationError,
)
from .models import (
    DEVICE_STATUSES,
    FAIL,
    PASS,
    TEST_OUTCOMES,
    TEST_TAGS,
    WARN,
    Device,
    DeviceType,
    Identity,
    Software,
    Test,
)

__all__ = [
    "TestBench",
    "Device",
    "DeviceType",
    "Software",
    "Test",
    "Identity",
    "DEVICE_STATUSES",
    "TEST_OUTCOMES",
    "TEST_TAGS",
    "PASS",
    "FAIL",
    "WARN",
    "TestBenchError",
    "TransportError",
    "AuthenticationError",
    "PermissionDenied",
    "NotFound",
    "Conflict",
    "ValidationError",
    "ServerError",
    "UnknownDevice",
    "UnknownSoftware",
    "DeviceUnavailable",
]
