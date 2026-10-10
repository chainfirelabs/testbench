# Home dashboard

Home (`/`) is a shared dashboard for signed-in users. Everyone sees the saved
layout. Administrators can select **Edit dashboard** to add, remove, reorder,
resize, and configure widgets. **Save layout** publishes the change for everyone;
**Cancel** discards it. A second administrator's intervening save causes a
conflict and requires a reload. Layout writes require the `admin` role and are
audited. The data refresh button reads current records without changing them.
Each widget has a **Remove widget** button at the top while editing. Removing a
widget changes only the draft until **Save layout** is selected.

The widget library contains:

| Widget | What it shows |
| --- | --- |
| Fleet summary | Device totals and latest online, offline, and never-scanned counts. |
| Devices by make | A doughnut chart of the largest makes, with Unknown and Other groups. |
| Online by device type | Online, offline, and never-scanned counts by type, including Uncategorized. |
| Inventory status | Device counts by status. |
| Checkouts due | Overdue and next-seven-day counts, plus a short device list. |
| Recently changed devices | Most recently created or updated devices. |
| Test activity | Test counts and outcomes over a selected recent period. |
| Most tested software | Software entries ranked by test-record count. |
| Most tested devices | Devices ranked by test-record count. |
| Announcement | Admin-authored plain-text notice. |
| Untested devices | Count and short list of devices with no test records. |
| Recent failed or warning tests | Bounded list of recent problem results. |
| Scan freshness | Recently scanned, stale, and never-scanned device counts. |
| Test outcomes by software | Pass, fail, and warning counts for software with at least three tests. |
| Firmware coverage | Top firmware versions for all devices or a selected type. |

Each widget type can appear once. The editor offers top-item limits, date ranges,
scan-staleness thresholds, and a device-type choice where those settings apply.
Widths scale with the dashboard; a fixed height scrolls content when needed.
Click a widget in edit mode to reveal resize grips on every edge and corner;
they also respond to arrow keys. Drag a widget from any non-interactive area
onto another widget to reorder it. The up and down arrow buttons also work.
The new layout is published when you save it.

A device is **online** only if it has a scan timestamp and the last scan result
is true. A scanned device with a false or missing result is **offline**. A device
without a scan timestamp is **never scanned**. Scan freshness measures the age of
the last scan, not uptime. Most-tested rankings count test records separately by
software entry/version or device ID. All-time rankings include records without a
run date; a selected period uses the test's `run_at` date. The dashboard does
not poll automatically; use **Refresh** for current counts.

Device, software, and type links open their existing pages. Fleet scan-state,
make, and inventory-status links open the matching filtered Devices list. Chart
labels and numeric lists accompany the visual bars and doughnuts.
