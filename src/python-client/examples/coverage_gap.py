"""Hardware a vendor claims to support that has never actually been tested here.

    TB_API_URL=http://localhost:8001 TB_API_KEY=tb_... python examples/coverage_gap.py [software]

The claim/evidence split, put to work. A vendor's compatibility list says what
they believe works; the tests say what has been run. The interesting rows are
the ones in the first list and not the second — and note that this is a gap in
*evidence*, not a fault: nobody has looked, which is different from having
looked and found a problem.
"""

import sys
from collections import defaultdict

from testbench_client import TestBench


def main() -> None:
    wanted = sys.argv[1] if len(sys.argv) > 1 else None

    with TestBench.from_env() as tb:
        # Every claim, or one software's. A name covers all of its versions,
        # and a claim made by a superseded version is still a claim.
        claims = tb.vendor_devices.list(software=wanted)
        if not claims:
            print(f"No vendor claims for {wanted}." if wanted
                  else "No vendor compatibility claims recorded.")
            return

        # What has actually been run, keyed the way a claim identifies
        # hardware: by make and model. A test carries its device's make and
        # model, so this is one listing rather than a join against the fleet.
        #
        # Grouped by software NAME rather than by version: evidence gathered on
        # 2.0 says something about the software, and demanding it again for
        # every version would report a gap for each one the moment a version is
        # added. Narrow this to `(name, version)` if your suites are
        # version-specific.
        tested: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for test in tb.tests.list():
            if test.software_name:
                tested[test.software_name].add(
                    (test.device_make or "", test.device_model or "")
                )

        gaps: dict[str, list] = defaultdict(list)
        for claim in claims:
            # "The vendor says no" is not a gap in coverage; it is an answer.
            if claim.support_status == "unsupported":
                continue
            if (claim.make or "", claim.model or "") in tested[claim.software_name]:
                continue
            gaps[claim.software_name].append(claim)

        if not gaps:
            print("Every claim has been tested against at least once.")
            return

        for software, rows in sorted(gaps.items()):
            print(f"\n{software} — {len(rows)} untested claim(s):")
            for claim in sorted(rows, key=lambda c: (c.make or "", c.model or "")):
                version = f" (in {claim.software_version})" if claim.software_version else ""
                stale = "" if claim.software_is_latest else "  [superseded version]"
                print(f"  {claim.hardware:30} {claim.support_status:12}{version}{stale}")

        print("\nUntested means nobody has looked, not that anything is broken.")


if __name__ == "__main__":
    main()
