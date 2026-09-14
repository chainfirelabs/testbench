"""Print the fleet, grouped by status.

    TB_API_URL=http://localhost:8001 TB_API_KEY=tb_... python examples/inventory.py
"""

from collections import Counter

from testbench_client import TestBench


def main() -> None:
    with TestBench.from_env() as tb:
        print(f"Connected as {tb.identity.username} ({tb.identity.role})\n")

        devices = tb.devices.list()
        counts = Counter(d.status for d in devices)
        for status, count in counts.most_common():
            print(f"{count:4}  {status}")
        print(f"{len(devices):4}  total\n")

        held = [d for d in devices if d.is_checked_out]
        if held:
            print("Checked out:")
            for device in held:
                who = device.checked_out_by_username or "unknown"
                since = device.checked_out_at.date() if device.checked_out_at else "?"
                print(f"  {device.unique_id:12} {who:12} since {since}")

        offline = [d for d in devices if d.is_available and not d.online]
        if offline:
            print(f"\n{len(offline)} available but not answering a scan: "
                  f"{', '.join(d.unique_id for d in offline[:10])}"
                  f"{'…' if len(offline) > 10 else ''}")


if __name__ == "__main__":
    main()
