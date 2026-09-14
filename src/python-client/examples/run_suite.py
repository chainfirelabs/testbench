"""Reserve a device, run something against it, record the result.

The shape most automated frameworks want:

    TB_API_URL=http://localhost:8001 TB_API_KEY=tb_... \
        python examples/run_suite.py curl-smoke

Needs a key with the `tester` role or better.
"""

from datetime import date, timedelta

import sys
import time

from testbench_client import TestBench, DeviceUnavailable


def run_the_actual_test(device) -> tuple[bool, dict]:
    """Stand-in for whatever your framework does with the hardware."""
    started = time.monotonic()
    time.sleep(0.1)
    return True, {"duration_s": round(time.monotonic() - started, 3)}


def main(software: str) -> int:
    with TestBench.from_env() as tb:
        # Fail early and loudly if the key cannot write, rather than after the
        # hardware has been tied up for an hour.
        if not tb.identity.can_write:
            print(f"This key is {tb.identity.role}; recording results needs tester or admin.")
            return 2

        for candidate in tb.devices.list(status="available"):
            try:
                # `reserved` gives the device back even if the block raises,
                # so a crashed run does not strand it as checked out.
                with tb.devices.reserved(candidate.unique_id, purpose="Automated suite",
                                         due=date.today() + timedelta(days=1)) as device:
                    print(f"Running {software} against {device}")
                    passed, metrics = run_the_actual_test(device)
                    result = tb.tests.record(
                        device, software, passed,
                        tag="acceptance",
                        misc_data=metrics,
                        notes=f"automated run from {sys.argv[0]}",
                    )
                    print(f"Recorded {result.outcome} "
                          f"({result.software_name} {result.software_version})")
                    return 0 if passed else 1
            except DeviceUnavailable:
                # Someone claimed it between the listing and the checkout.
                print(f"{candidate.unique_id} was taken; trying the next one")
                continue

        print("No available device to run against.")
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "curl-smoke"))
