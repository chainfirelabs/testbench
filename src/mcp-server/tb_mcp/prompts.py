"""Workflow starters (§10).

Deliberately few. Each one encodes a question this data model answers well but
which takes several correctly-ordered tool calls to get right.
"""

from __future__ import annotations

from .server import server


@server.prompt()
def triage_failing_tests(since: str = "", software: str = "") -> str:
    """Group recent test failures by device and software to find the pattern."""
    window = f" since {since}" if since else " (recent)"
    scope = f" for {software}" if software else ""
    return f"""
Triage the failing tests{scope}{window}.

1. Call `find_tests(outcome="fail"{f', software="{software}"' if software else ""}\
{f', since="{since}"' if since else ""})`.
2. Group the failures by device and by software, and say which grouping is the
   stronger signal — one device failing everything is a different problem from
   one piece of software failing everywhere.
3. For each device that shows up more than once, call `get_device` and report its
   status and whether it is online; a `broken` or offline device explains a lot
   of failures at once.
4. Report counts and the devices/software involved. Do not conclude anything is
   unsupported from a failure — a failure is one run, not a support verdict.
""".strip()


@server.prompt()
def device_checkout_report() -> str:
    """Who has what, and what is unavailable."""
    return """
Report on the fleet's availability.

1. Call `find_devices(status="checked_out")` and list who holds each device.
2. Call `find_due_devices()` and highlight devices that are overdue or due back
   within the next 3 days, most urgent first.
3. Call `find_devices(status="broken")` and `find_devices(status="missing")`.
4. Summarise: how many devices are available, checked out, broken or missing,
   and who is holding the most.
5. Note the `total` on each result and say so if any list was truncated.
""".strip()


@server.prompt()
def software_coverage_gap(software: str = "") -> str:
    """Vendor claims with no test evidence behind them."""
    target = software or "each piece of software in the catalog"
    return f"""
Find the coverage gap for {target}: hardware the vendor claims support for that
has never actually been tested here.

1. Read `testbench://software-catalog` if no specific software was named.
2. For the software in question, call BOTH:
   - `list_vendor_supported_devices` — the vendor's claims
   - `list_devices_tested_with` — what has actually been run
3. Compare on make/model. Report three groups, kept separate:
   - claimed and tested (and whether those tests passed)
   - claimed but never tested  <- the gap
   - tested but not on the vendor's list
4. Be explicit that "claimed but never tested" means no evidence either way. It
   is not a prediction of failure, and untested is not unsupported.
""".strip()
