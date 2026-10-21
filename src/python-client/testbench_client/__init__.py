"""Python client for the TestBench API.

```python
from testbench_client import TestBench

with TestBench.from_env() as tb:
    with tb.devices.reserved("dev-0042") as device:
        result = run_my_suite(device.wan_ip)
        tb.tests.record("dev-0042", "curl-smoke", result.ok, misc_data=result.metrics)
```

Authenticates with a TestBench API key (`tb_<prefix>_<secret>`), minted
per user under Profile -> API keys. The key carries a role, and a role is a set
of permissions the installation defines — so what a key may do is
`tb.whoami().permissions`, not the role's name. Reading needs nothing;
`devices.edit` checks a device out and `tests.edit` records a result.
"""

# Released with TestBench and versioned with it: client 1.13.6 is the one
# that knows everything a 1.13.6 server offers.
__version__ = "1.13.6"

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
    AUDIT_VIEW,
    DEVICE_STATUSES,
    DEVICES_EDIT,
    FAIL,
    PASS,
    PLUGINS_MANAGE,
    SCHEMA_MANAGE,
    SETTINGS_MANAGE,
    SOFTWARE_EDIT,
    TEST_OUTCOMES,
    TEST_TAGS,
    TESTS_EDIT,
    USERS_MANAGE,
    VENDOR_SUPPORT_STATUSES,
    VIEWS_SAVE,
    WARN,
    CompatibleSoftware,
    Device,
    DeviceType,
    Identity,
    SearchResults,
    Software,
    SoftwareComponent,
    Test,
    TestArtifact,
    TestedDevice,
    TestFileStorage,
    TestOutput,
    VendorDevice,
)

__all__ = [
    "TestBench",
    "Device",
    "DeviceType",
    "Software",
    "SoftwareComponent",
    "Test",
    "TestArtifact",
    "TestedDevice",
    "TestFileStorage",
    "TestOutput",
    "VendorDevice",
    "CompatibleSoftware",
    "SearchResults",
    "Identity",
    "DEVICE_STATUSES",
    "TEST_OUTCOMES",
    "TEST_TAGS",
    "VENDOR_SUPPORT_STATUSES",
    "PASS",
    "FAIL",
    "WARN",
    "DEVICES_EDIT",
    "SOFTWARE_EDIT",
    "TESTS_EDIT",
    "VIEWS_SAVE",
    "AUDIT_VIEW",
    "USERS_MANAGE",
    "SCHEMA_MANAGE",
    "PLUGINS_MANAGE",
    "SETTINGS_MANAGE",
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
