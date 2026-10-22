# Release notes

## 1.14.1

- **Edit a test's test run.** The test edit dialog has **Test Run** (pick a
  run, type a new name to start one, or clear it to take the test out) and
  **Test Run Files**. Bulk edit can move the selected tests into a run, or out
  of their runs.
- **Fixed: changing a test's component in the edit dialog did nothing.** It
  was two free-text boxes the API ignored; it is now a choice over the test's
  software components. Bulk edit no longer offers it, since selected tests can
  be of different software.
- **Edit a user's email.** **Change role** on Users is now **Edit user**: the
  email of any local account, including your own, and the role of others. SSO
  accounts take both from Authentik.

## 1.14.0

- **Test runs.** A test run is one execution of a pipeline or suite: the tests
  it recorded, and files it shares with all of them, such as a pytest report,
  stored once instead of uploaded to every test.
  - Tests has a **Test Run** column. Clicking a run opens its page: its shared
    files above the usual tests table, limited to the run. There is no
    separate list of runs; filter the column to find one. Each test's
    Artifacts dialog lists its run's shared files under its own
    (`Artifacts (1 + 1 shared)`).
  - API: `/test-runs`, with `/test-runs/{id}/artifacts` for shared files.
    Starting a run with an `external_id` already in use returns that run, so
    every job of one pipeline joins the same one. Tests take `test_run_id` on
    create and update, and `GET /tests?test_run_id=` filters by it.
  - The bucket keeps a run's files together under
    `test-runs/<run-name>_<run-id>/`: shared files in `artifacts/`, and the
    files of each test in the run under `tests/<test-id>/`. Tests outside a run
    are stored under `tests/` as before, and existing files are not moved.
  - **Upgrade note:** if your S3 credentials are limited to `tests/*`, also
    grant GetObject, PutObject and DeleteObject on `test-runs/*`, or uploads to
    runs, and to tests in runs, are refused.
  - Runs travel with their tests in replication; files still do not. Migration
    `0020_test_runs`.
  - Python client: `tb.test_runs` (`start`, `attach`, `finish`, `tests`, …),
    `test_run=` on `tb.tests.create()`, `update()` and `list()`, and
    `tb.tests.shared_artifacts()`. `tb.test_runs.start_from_ci()` starts or
    joins the run for the GitLab CI pipeline or GitHub Actions workflow run it
    is running in, taking its ID, name and link from the CI environment.
- **New test dialog: test run and its files.** **+ New test** on Tests has a
  **Test Run** field, suggesting existing runs; a new name starts a run. **Test
  Run Files** uploads files to the run, such as a report or test outline,
  shared with every test in it. On a run's page the run is filled in.
- Python client: `tb.tests.create(..., test_run=run, test_run_artifacts=[...])`
  shares files with the run as the test is recorded.

## 1.13.6

- The Python client (`src/python-client`) is versioned with TestBench, starting
  at 1.13.6 (it was 0.1.0), and covers what the API gained since it was last
  updated:
  - Recording, filtering and amending runs against a suite's components
    (`tests.create(component=...)`), and `Software.components`.
  - Required custom test fields: they go in `misc_data`, and
    `tb.entity_fields("tests")` lists them.
  - `tb.tests.file_storage()`. A run with files is no longer recorded when the
    server cannot store them.
  - `tb.devices.compatible_software()`, `tb.software.tested_devices()`,
    `tb.vendor_devices.inventory_matches()` and the claims'
    `inventory_devices`, and `tb.search()`.
- The backend, frontend, MCP server and replication controller packages now
  carry the TestBench version as well (they all said 0.1.0). The version shown
  in the UI and on the images still comes from the release tag.

## 1.13.5

- Fixed: **Artifacts** said object storage wasn't enabled, and artifact
  uploads and downloads failed, even with `backend.objectStorage` enabled and
  Output working. The frontend's nginx sent artifact requests nowhere: the
  block raising their upload limit lacked its own `proxy_pass`, so nginx
  answered them with a 404 page instead of passing them to the API. Broken
  since 1.12.0, in both the Helm chart and Docker Compose.
- The Artifacts dialog no longer claims storage is disabled when it simply
  couldn't load the list; it shows the error instead.
- Fixed: a device list showing **Checkout Due** (most visibly a device type
  with no devices yet) could fail with "Could not load rows: can't access
  property "data", row is undefined". The overdue colouring was worked out for
  the placeholder row drawn while the page loads, which has no device. Every
  table now skips a column's own cell colouring rules for placeholder rows.

## 1.13.4

- Fixed: recording a test from **Tests → New test** failed with "Field
  required" even with every field filled in. The dialog found the device but
  sent the test without it. Broken since 1.9.4.
- **New test** takes the test's **Output** (one plain-text file) and
  **Artifacts** (any number of files), both optional, uploaded once the test
  is recorded. Without object storage they are shown disabled, saying why. A
  file that fails to upload is reported and can be added from the test's row.
- Request errors name the field they are about ("device_id: Field required")
  instead of only "Field required".
- `GET /tests/file-storage` reports whether tests can hold output and
  artifacts, and the size limit for each.

## 1.13.3

- Test **Output** and **Artifacts** are built-in, read-only test fields. Hide
  or reorder them under **Schema → Tests** like any other column; the APIs and
  clients are unaffected. Existing installations get both shown, in the same
  place as before.
- A device's **Tests** tab follows Schema → Tests: its columns, their order,
  hidden fields and custom test fields now match the Tests page.
- A device's **Vendor Claims** tab draws each software version's claims with
  that version's layout: Schema → Vendor Claims plus the version's own
  **Customize fields** override, including custom claim fields and
  Inventory Devices.
- Software → Tested Devices: the tests dialog leaves out details hidden under
  Schema → Tests.
- `GET /devices/{id}/compatible-software` returns each version's claim
  `fields`, and its claims include `inventory_devices`.
- Fixed: a built-in field that the application always shows, if added by an
  upgrade, was created hidden until the next restart.
- A device's **Vendor Claims** tab summarises Components as "3 components" and
  Inventory Devices as "14 devices", each opening its list, like the vendor
  claim grids.
- **Inventory Devices** reads as a count ("14 devices") everywhere, with the
  first few device IDs in its tooltip and the full list a click away.
- A device's **Tests** tab shows 50 tests a page with Previous/Next; a link to
  one test opens the page that holds it.
- Fixed: the page did not scroll while the pointer was over a tall plain table,
  such as a device's Tests or Vendor Claims tab.
- Every list shows that it is loading: a **Loading…** overlay and grey
  placeholder bars while a page of rows is fetched, on first load and after
  paging, sorting, filtering or searching. On a phone the card list says
  "Loading…". Before, the grid showed one row of `N/A`s (or "Uncategorized",
  "(unversioned)") until the rows arrived, and the card list said "Nothing
  here yet".

## 1.13.2

- Replication is no longer a plugin. It is an optional part of the Helm chart,
  configured under a top-level `replication` key instead of
  `plugins.replication`, and is gone from the `plugins` section of
  `values.schema.json`. It no longer needs `plugins.sharedSecret` and no longer
  appears under **Schema → Plugins**.
- **Upgrade:** move `plugins.replication` to `replication`. The chart refuses
  to render with the old key rather than silently turning replication off.
- The backend calls the replication controller directly at
  `TB_REPLICATION_CONTROLLER_URL`, and the controller and backend authenticate
  to each other with the replication token. The controller's routes moved from
  `/plugin/v1/*` to `/v1/*`, and its source moved from `src/plugins/replication`
  to `src/replication`. The image name is unchanged.
- Migration `0019` removes any Schema → Plugins allowlist rows for
  replication. They never granted anything.

## 1.13.1

- Vendor claims have a built-in, read-only **Inventory Devices** field listing
  the inventory devices that match each claim: the first three unique IDs and
  how many more, with the full list a click away, or `N/A` when nothing
  matches. It appears on the Vendor Claims page and Software → Vendor Claims,
  replacing the latter's fixed Inventory column, and is shown, hidden and
  ordered from **Schema → Vendor Claims** or a version's **Customize fields**.
  Upgrades add it as visible. It is derived on read and never imported or
  exported.
- Claim listings return `inventory_devices` and `inventory_match_count`.
- Python client: `tb.tests` uploads, lists, downloads and deletes artifacts and
  sets, reads and deletes test output; `create()`/`record()` accept
  `artifacts=`, `output=` and `output_file=`. `Test` exposes
  `artifact_count`, `output_filename`, `output_size_bytes` and component fields.
- Go client and CLI: `Tests` artifact and output methods, and
  `testbench --test TEST_UUID` with `--artifacts`, `--upload-artifact`,
  `--download-artifact`, `--delete-artifact`, `--output`, `--set-output` and
  `--delete-output`. These are the Go client's only writes.
- MCP server (still read-only): `get_test_output` and `list_test_artifacts`;
  test rows now include their `id`, artifact count and output filename.

## 1.13.0

- Each recorded test can carry one ASCII text output file, stored beside its
  artifacts at `tests/<test-id>/output/<filename>`. **Output** on the Tests
  page, Device → Tests, and Software → Tested Devices opens the text in a
  dialog instead of downloading it. Users with `tests.edit` can upload,
  replace, or delete it there, or through `PUT`/`DELETE /tests/{id}/output`.
- Output is checked at upload: binary content and files over
  `backend.objectStorage.maxBytes` (1 MiB by default) are refused. Uploads and
  deletions are audited. Output is not replicated or exported, and like
  artifacts it is kept in S3 when a whole test is deleted.
- Migration `0018_test_outputs` adds the output metadata.
- Breaking: the device **Output** field and `GET /devices/{id}/output` are
  removed. On upgrade the system field is switched off; an `s3://` URL already
  entered on a device stays in its data and appears under Misc Data.

## 1.12.2

- Software → Tested Devices: the Tests count opens a dialog listing the tests
  behind that row, newest run first, with outcome, run date, tag, author,
  artifact count, notes, and test data. Each test links to its entry on the
  device's Tests tab, where it can be edited and its artifacts downloaded.
- The tests list API accepts `component_id` to narrow to one software
  component; `component_id=none` returns tests not tied to a component.
- The device Tests tab shows `N/A` for empty notes and test data.

## 1.12.1

- Empty fields in every list and table — devices, software, vendor claims,
  tests, users, and the audit log, on desktop and mobile — read a muted `N/A`
  instead of showing nothing. Columns with their own wording for an empty value
  (such as "Uncategorized" or "(unversioned)") keep it, and status dots are
  unchanged. Detail pages and hand-built tables use the same `N/A` in place of
  "—".
- Mobile card lists show schema fields stored under device `data` instead of
  treating them as empty.

## 1.12.0

- Recorded tests support multiple downloadable artifacts. The Tests page and
  Device → Tests tab offer artifact uploads, downloads, and deletion.
- Binary files use the configured private S3-compatible bucket under
  `tests/<test-id>/artifacts/<artifact-id>/<filename>`, keeping each run separate
  and allowing duplicate filenames without overwrites.
- Authenticated readers can download artifacts; uploads and deletions require
  `tests.edit` and are audited. Downloads stream through the backend.
- `backend.objectStorage.artifactMaxBytes` sets the per-file upload limit
  (100 MiB by default); Helm adjusts the frontend proxy limit accordingly.
  Storage credentials need read, write, and delete permissions on `tests/*`.
- Migration `0017_test_artifacts` adds artifact metadata. Individual artifact
  deletion removes its stored file; whole-test deletion retains objects for
  operator-managed retention. Artifact files are excluded from replication and
  test exports.

## 1.11.3

- Software → Vendor Claims shows matching inventory device counts for each
  selected firmware record, with a paginated list linking to those devices.
  Matches use the existing vendor compatibility rules and refresh after edits.
- Replication can sync selected entity types through `plugins.replication.entities`.
  Settings, run status, and audit records show the selected types.
- No database migration is required for this release.

## 1.11.2

- A single `global.tls` ConfigMap can supply a PEM CA bundle to the backend,
  replication controller, MCP, enabled plugin controllers, and Device Info and
  Reboot research Jobs for outbound HTTPS. The older backend-only
  `authentik.tls` setting remains supported.
- Replication can verify peers signed by a private CA from that bundle. Create
  the ConfigMap in each namespace used by plugin research workers.

## 1.11.1

- The Replication settings page shows its Helm-managed mode, schedule, and
  destination URL in read-only fields.
- Manual sync requests and completed manual or scheduled syncs are recorded in
  the audit log with their destination URL and outcome. Audit reporting retries
  transient failures and shows an error on the Replication page if it cannot
  record the result.

## 1.11.0

- Administrators can edit the shared Home dashboard: add or remove the Fleet
  summary widget, change its title, description, width, and breakdown bar, and
  save or cancel. Other users can view the saved layout but cannot change it.
- Dashboard edits are audited and use a revision check to prevent one admin
  from silently overwriting another's changes. Migration 0015 adds the shared
  dashboard layout table.
- Software details can delete a specific version, including an older version,
  and navigate to the remaining current version after deletion.
- Global search finds software suites by component name or version and shows
  matching component details in software, device, and test results.
- Optional external S3-compatible object storage lets a device's Output field
  reference an ASCII text file for on-demand viewing. TestBench does not deploy
  or manage the object store.
- Optional replication syncs devices, software, vendor claims, and tests between
  instances on a schedule or by manual trigger. Push-only and bidirectional
  modes are available; newer edits and deletion markers resolve conflicts.
  Migration 0016 adds replication deletion markers.

## 1.10.0

- Home now opens a dashboard with a live Fleet summary widget showing total,
  online, offline, and never-scanned device counts. The summary can be refreshed
  without reloading the page.
- All Devices has moved to `/devices`, with Home and Devices links in desktop and
  mobile navigation. Device detail and device-type links keep their existing paths.
- This is the dashboard layout proof of concept. Widget selection and editing
  remain planned; the dashboard is read-only for all users in this release.
- No database migration is required.

## 1.9.10

- Exports for devices, tests, software, vendor claims, and audit logs honor the
  current filters and selected rows instead of exporting the full dataset.
- Components summaries on Software, Software Details, and Vendor Claims show
  the component name when there is one, or only the count when there are
  multiple components. Long names fit within their cells.
- Software Details shows its Components field even when older field metadata
  omits it, and follows the field order set in Schema → Software. Components
  also appears in small-screen cards when enabled.
- A saved view can no longer hide a Components column that the schema shows.
- No database migration is required.

## 1.9.9

- Audit log retention is configurable in the web UI and defaults to `0` (keep
  logs indefinitely). Administrators can preview and delete every eligible log
  before a selected date. Manual deletion now runs as a tracked job with
  progress, retry, and recovery after a backend restart.
- Device changelog entries are protected from scheduled and manual cleanup by
  default. Only the Helm `backend.auditCleanup.deleteDeviceChangelogs` setting
  can allow their deletion.
- Audit, device, test, software, and vendor claim exports read database rows in
  bounded batches, reducing memory use for large downloads.
- Plugin run state and progress are saved in PostgreSQL. After a controller
  restart, a lost run is shown as interrupted. Worker output remains transient
  because it can contain device credentials.
- Global search uses PostgreSQL trigram indexes to speed substring matches.
  Migration 0014 creates `pg_trgm`; migrations 0012 and 0013 add cleanup jobs
  and plugin run status tables.

## 1.9.8

- Software and Vendor Claims component lists open in a searchable, scrollable modal.
- Components can be filtered by name from the Software and Vendor Claims columns.
- Components appear in both Software and Vendor Claims schemas, where their
  list visibility and order can be customized. Each software version can also
  reorder its Vendor Claims fields, including Components.
- Development seed scripts can generate a requested number of software
  components and link components to vendor device claims.
- Vendor Claims CSV and JSON imports accept one component per row with
  `component_name`, `component_version`, and optional `component_status`.
  Rows for the same software version and hardware merge their component claims.
- Vendor Claims import templates include the flat component columns; the
  existing `component_support` list format remains supported.
- The Python client can import Vendor Claims with `import_csv()` or
  `import_rows()`. The MCP server remains read-only.
- No database migration is required.

## 1.9.7

- Vendor Claims can filter by component name, version, and effective support
  status. The same filters work through the API, exports, Python client, and MCP
  tools; responses identify matching component versions and inherited statuses.
- Expanded component lists have an opaque, scrollable panel.
- Vendor claims for the same software version and hardware retain each explicit
  component version. Adding or importing another component updates that link
  without removing the earlier ones, even when both statuses are the same.
- Vendor Claims shows one suite/device row with an expandable Components section
  that lists each explicit component version and its support status, on both
  the global and software details pages.
- No database migration is required.

## 1.9.6

- Software suites now group components with the same name. On a suite's Details
  tab, expand a component name to see its versions. The Components dropdown
  on the Software list shows only the highest version for each component name.
- Vendor device support claims can optionally set a separate support status
  for each component version. Claims without component links and components
  without an override continue to use the general support status.
- Device Info preserves populated hardware versions and LAN/WAN MAC addresses,
  including when an AI result arrives after inventory changes. It fills missing
  values and continues to update firmware when a new version is discovered.
- Long text and wide tables wrap or scroll within frontend pages on narrow
  screens.
- Database migration 0010 adds optional component support links to vendor
  device claims.

## 1.9.5

- **Scan all** now updates each device's online state as the sweep reaches it.
  The scan worker reports results in batches during the run, and the Devices
  page repaints the online columns as progress advances instead of waiting for
  the whole scan to finish.
- The online-state poll no longer fails when the fleet includes devices that
  have never been scanned.
- No database migration is required.

## 1.9.4

- Column checklists preserve their include or exclude selection while rows and
  distinct values load. Saved views now restore filters after dynamic columns
  are ready and save the search text used by server-paged tables.
- Filtering **Online** by true and false no longer fails when blanks are also
  available. Blank predicates respect boolean and numeric column types, and
  failed table requests show an error with a Retry button.
- Column options now include blanks and page through the full set of distinct
  values. Large selections travel in a POST query body, avoiding URL limits.
  Unsupported column filters are hidden, and Tested Devices accepts inclusion
  filters as well as exclusions.
- No database migration is required.

## 1.9.3

- A device field can now be marked **Opens a web page** in Schema → Device
  Fields, which offers its value as a link to that address: the value itself on
  the device page, and a small icon beside it in the inventory grid, where the
  cell is editable. Fields carrying a scan address role get it on by default,
  including on upgrade; any other field can be opted in and any field can be
  opted out. See [Opening a web page](device-schema.md#opening-a-web-page).
- A linked field now chooses the **scheme and port** its link opens — `http`
  or `https`, and a port or the scheme's own — in Schema → Device Fields, and a
  single device that answers somewhere else overrides both under **Links** on
  its own page. Existing links are unchanged: every field starts on `http` with
  no port, which is what they did before.

  The override belongs there rather than in the address itself. A field
  carrying a scan address role has its value handed straight to a socket by the
  network scan and by the Reboot plugin, so a scheme stored in the value makes
  the device read as offline and stops its reboots, while the link still looks
  right. See [Choosing the scheme and
  port](device-schema.md#choosing-the-scheme-and-port).

- Creating or updating a device with `last_seen_online`, `last_scanned_at` or
  `checkout_due` no longer fails when that field is not on the device schema.
  The API declares all three as date types, so a request carrying one arrived
  as a Python date; a field the catalog does not account for is kept as it
  came, and a date object is not something a JSON column can store. The write
  failed at the flush — a 500 naming no field, which is why nothing about the
  request suggested what was wrong. Values entering the document are now
  converted the same way values entering an audit entry already were.

  It affected only the single-device create and update, since those are the
  paths with a model to parse the value. The bundled `seed_dev_data.py` sends
  `last_seen_online`, so a new installation hit this on the first script it ran.

- Suggestion lists render at most 100 options, with a count of what is left
  ("and 1,900 more — keep typing to narrow"). Each option is a real DOM node,
  and the fields offering every device or every software version matched all
  of them while the box was still empty — so focusing the field built a node
  per device in one tick. Typing was always how the list was meant to be used;
  now an untyped list costs the same as a typed one. The phone's picker sheet
  is capped the same way.

- **New test** looks up a typed device or software name through an index built
  once, rather than scanning every row on every keystroke.

- Collections loaded in full — the device and software lists behind the New
  test dialog — fetch their pages a few at a time instead of one after another.
  The first page still goes alone, since until it returns there is no total to
  work from; the rest overlap. A collection that took a dozen sequential round
  trips now takes three or four batches of them.

- List endpoints take **`?fields=`**, a comma-separated set of field keys, and
  return just those. Keys resolve against the device or software document by
  name, so a field an installation defined is projectable without the API
  knowing about it, and a field a particular row does not carry comes back null
  rather than failing the request. Sensitive fields are refused outright: a
  projection reads the document directly, which is the one path that would hand
  back a value an ordinary list response never carries.

  **New test** uses it. Opening the dialog on a fleet of 2,000 devices
  transferred 2.5 MB of documents to read four values from each; it now
  transfers 208 KB — **12x less**, and twice as fast even on a local network,
  where transfer is nearly free.

- Exports **stream** instead of being built in memory and sent in one piece.
  Total time is unchanged — the same rows are still read and rendered — but the
  whole file is no longer held: a 30,000-test JSON export peaked at 16.8 MB and
  now peaks at 0.8 MB, and the download starts as soon as the first rows are
  ready rather than after the last one.

  Streaming was tried once before and reverted, because Starlette iterates a
  sync file object line by line and a pretty-printed export became one
  threadpool hop per line. That finding stands; the mistake was the chunk size.
  Exports now go out a thousand rows at a time, and the bytes are identical to
  what the buffered writers produced — there are tests pinning that, including
  for an empty export, which is still `[]`.

- The Software list no longer has an **All versions** / **Latest only**
  toggle. It shows the current version of each piece of software, always. The
  other view was a changelog — four rows of the same name between one piece of
  software and the next — and the versions of one piece of software belong to
  that software: click its name to see them, and to switch between them.

- Exports and templates now say they are working, and say when they fail.
  Every one of them was started without waiting for it, so a slow export left
  a button that looked dead and a failed one said nothing at all — the error
  became an unhandled rejection. They now disable while preparing and report a
  failure to the page's toast. The Audit Log's toast was never wired up to
  anything; it is now.

- **New test** likewise. Opening the dialog loads every device and every
  software version first, which is not instant on a large fleet and had no
  indication that anything was happening; a failure left the button doing
  nothing at all, permanently. It now shows that it is loading, reports a
  failure, and does not re-fetch the fleet each time it is opened.

- Filtering the Tests list by **Device**, **Software** or **Created By** now
  works. Those three are rows of their own rather than columns on a test, so
  reaching them means a join — and the filter loop skipped them instead. That
  did not disable them: it ignored them, so narrowing the grid to one device
  and asking the server returned every test there was. Clearing such a column
  showed everything rather than nothing, which is how it was found.

- A column filter that keeps only a few values out of many now works. The
  checklist sent the values you *unticked*, so clearing a column and picking
  one asked the server for "everything except these 499" — a sixteen-kilobyte
  URL, which nginx rejects with 414 before the API ever sees it. The grid was
  handed a failed request and showed nothing, which looks exactly like a
  filter that matched nothing. It bit any column with more than roughly 250
  distinct values.

  The filter now sends whichever side of the selection is shorter: a few
  unticked values still travel as `exclude__<field>`, while clearing the
  column and picking one travels as `include__<field>` with that one value.
  The URL grows with what you picked instead of with how many values the
  column happens to hold — in one case, from 15,969 characters to 125.

  The two are not quite the same statement, and that is deliberate. Excluding
  hides what it names and lets anything else through, including a value the
  list has never seen; including shows only what it names. Which is what
  someone who cleared the column and ticked two things means.

- A column filter can always undo itself. The checklist takes its values from
  the rows on screen when a column has no server-side list of them — the
  software columns on Vendor Claims, and anything a page cannot enumerate — so
  a filter narrow enough to leave no rows left nothing to harvest. Reopened
  from there it offered an empty list while still hiding every row: no
  checkbox to untick, and no way back except clearing the filter. Whatever a
  filter is excluding is now always listed, so it can be let back.

- Exporting from the Devices page now exports **what the grid is showing**.
  The export endpoint has always taken the list endpoint's filters, but the
  page only ever sent the device type from the route — so narrowing the grid
  to three devices and exporting handed back the whole fleet, silently and
  plausibly. The search box, the column filters and the overdue toggle all
  reach the file now, built from the same parameters the grid's own loader
  uses so the two cannot read it differently.

- The **Customize fields** button is gone from Devices, Software, Tests and
  Vendor Claims. Field catalogs are edited in one place now — the **Schema**
  page, which has a tab for each and does strictly more than the dialogs did:
  the same endpoints, plus field creation, deletion and per-type layouts. Four
  buttons that each opened a different partial view of the same settings were
  four places to look for one thing.

  A software version's own **Vendor Claims → Customize fields** stays. It
  overrides which catalog fields that version shows, which is per version and
  has no equivalent on the Schema page.

- There is **one Export CSV** on the Devices page again. The second one existed
  because the ordinary export silently dropped per-device link overrides —
  they belong to no field, so they had no column — and restoring a fleet from
  it put every custom link back on `http` without a word. The workaround was an
  export carrying the whole device document as a JSON blob per row: lossless,
  unusable in a spreadsheet, and impossible to choose between without already
  knowing all of this.

  The ordinary export carries the overrides now, in a column that is blank for
  the devices that override nothing. Everything else already round-tripped
  exactly — the import coerces each value back to its field's type, so a
  number comes back a number and nested JSON comes back nested — so with that
  one gap closed there is nothing left to choose between. Exports and imports
  are covered by round-trip tests that compare a device with itself.

  `columns=data` remains on the API for callers that want the document as one
  object rather than spread across columns.

- **New vendor device** picks its software by typing rather than from a
  dropdown, the way New test and New software do. A select was workable with a
  dozen entries and unusable with several hundred — and this is the page where
  every software in the installation is a candidate. The Version field follows
  the software named above it, says so when a version does not exist, and fills
  itself in when there is only one. More than one is left for you to choose: a
  claim on the wrong version is a wrong claim.

- **Vendor Devices** is now called **Vendor Claims** everywhere it is named:
  the menu, the page title, the software page's tab, the count column on the
  Software list, and the **Schema** page's own tab. The rows are assertions a
  vendor makes, not hardware this fleet owns, and the old name read as a third
  device list sitting between Software and Tests. The search results and the
  device page's own tab already called them claims.

  The `/vendor-devices` URL and the API paths are unchanged, so links and
  integrations keep working.

- The **Tests** page now carries a one-line introduction saying its rows are
  results this team recorded, and linking to Vendor Claims for what a vendor
  says it supports. Vendor Claims has always said its rows carry no test
  evidence; Tests never said the opposite half.

- The **Vendor Claims** page is now editable like every other list: **New
  vendor device**, **Import**, **Template**, inline cell
  editing, a per-row Edit dialog, and multi-select **Delete** — alongside the
  export it already had.

  Because the page spans every software, a claim created here names the version
  making it: the dialog requires a software and a version, and an imported file
  carries `software_name` and `software_version` columns per row, which are the
  first two columns of both the template and the catalogue export, so an
  exported file imports straight back. A row naming a version that software
  does not have is reported as that row's error rather than filed against
  whichever version is current.

  The catalogue export, the template and the page's columns are all built from
  the vendor-device field catalog, so a field an installation added is a column
  in the file and in the grid. For the export this is not presentation: the
  export is also what the import reads, and an import writes the whole row, so
  a column left out of the file would come back as "no value" and clear what
  somebody had typed.

  Editing a row acts on the software version that row belongs to. The two
  software columns are the exception and stay read-only — moving a claim from
  one version to another is a different claim, not an edit to a value. A
  multi-select delete spans versions in one call, and is audited per row with
  the software each belonged to.

- **Bulk Edit** on the Devices page can now set the **Device Type**, so a batch
  of uncategorized devices can be filed in one go. The dialog previously offered
  only fields every selected device's schema agreed on, which excluded the type
  — and left the button disabled entirely when a mixed selection shared nothing.
  Moving a device to a type checks it against that type's schema, so one missing
  a value the new type requires is reported and left as it was.

## 1.9.2

- Roles are now defined by the installation. **Users & Roles → Roles** creates
  and edits roles and ticks what each one grants — view the audit log, edit
  devices, manage the schema, and so on — instead of choosing between three
  fixed roles. The built-in readonly, tester and admin roles are seeded with
  the permission sets that reproduce their previous behaviour exactly, so
  upgrading changes nobody's access. See [Roles and permissions](roles.md).
- Every device has a **Changelog** tab: who changed what, when, and what the
  value was before, read from the audit log. Visible to anyone who can see the
  device rather than to admins only; secrets are masked and the raw audit entry
  is shown only to users who can read the audit log.
- Vendor compatibility claims can be searched across every software version at
  once, from the new **Vendor Devices** page, `GET /api/v1/vendor-devices`,
  global search, the `find_vendor_devices` MCP tool, `tb.vendor_devices` in the
  Python client, `VendorDevices` in the Go client, and `testbench
  --vendor-devices` in the Go CLI. Each result names the software and version
  making the claim and flags claims on superseded versions.
- The clients read what a key may do from its permissions rather than from its
  role's name, which stopped being a reliable guess once installations could
  define roles: `tb.identity.can("tests.edit")` in Python, and `permissions` in
  the Go CLI's `--whoami` output. Both still answer correctly against a
  TestBench that predates definable roles.
- Column filters on the server-paged lists now offer every distinct value in the
  column rather than only those on the page being viewed, and the panel's
  "Select all" and "Clear" act on whatever the search box is showing.
- Creating, deleting or importing a row now updates the list immediately.
  Server-paged grids were refreshing the rows they already held without
  re-reading how many there were, so a new record did not appear until the
  browser was reloaded.
- Deleting selected rows now clears the selection. The count was carried over,
  so deleting 100 rows and selecting 100 more reported 200 selected.
- Selecting all rows on a page now selects the whole page at every page size.
  The fetch block size did not follow the page-size selector, so at 250 or 500
  rows a page only the first 100 were loaded and only those were selected.
- The Tested Devices list under a software no longer hangs on a large number of
  tested devices: its response had stopped reporting the total, so the grid kept
  requesting further pages past the end of the data.

## 1.9.1

- Large Devices, Software, Tests, Audit Log, Vendor Devices, and Tested Devices
  lists now page, search, filter, and sort through the API instead of downloading
  the complete collection before rendering.
- Vendor-device pagination preserves make/model/hardware grouping and retains all
  firmware versions in each collapsed row. Bulk deletion expands selected groups
  to their complete set of firmware records.
- Saved Views now sit consistently beside the Columns/Fields picker. Administrators
  can reach field customization directly from schema-backed list pages.
- Test component fields remain available on upgraded installations and honor the
  configured list visibility.
- Development seed scripts accept a TestBench URL and API key, support configurable
  record counts and component suites, create grouped and unique vendor devices,
  and generate component-aware tests. The combined seeder reuses existing records
  and no longer attempts to create local users.

## 1.9.0

- Administrators can manage AI provider endpoints, encrypted credentials, and
  discovered/manual models in the GUI. Device Info and AI Reboot support
  discovery and repeat-model selection globally and at device-type, ordered-rule,
  and individual-device scope. Helm URL/model settings remain authoritative and
  appear read-only in the UI.
- Authentik deployments can mount a private CA certificate through the Helm
  chart for OIDC connections using internally signed TLS certificates.
- Disabled or unavailable Network Scan plugins can no longer be invoked from a
  device detail page or through the legacy direct scan endpoint.

## 1.8.8

- TestBench source, packages, Helm metadata, and container images are now
  identified as MIT-licensed copyright ChainFire Labs. Distributed images
  retain applicable TestBench and third-party license notices.
- The Columns picker now enumerates the live grid columns, so fields from every
  device type on **All Devices** can always be shown or hidden.
- Device exports and templates now identify their schema scope in the filename,
  such as `all-devices.json`, `router-devices.csv`, and
  `router-devices-template.csv`. Raw CSV exports use `*-devices-raw.csv`.

## 1.8.7

- The table Columns picker once again hides and shows columns with AG Grid 36.
  Saved sort and quick-filter state now use the current grid APIs as well.
- Device Info's launch confirmation now explains that the AI agent inspects the
  device's web page to find device information.
- The main and Helm documentation now covers additive schema import, device-type
  editing, plugin port overrides, and SSH reboot recovery behavior.

## 1.8.6

- Gathering device information now asks for confirmation before starting an
  AI agent to inspect the device's web page and find device information.

## 1.8.5

- SSH reboot workers now stream connection and recovery progress to the GUI.
  Recovery watches the configured SSH endpoint directly, avoiding a multi-port
  polling race, and requires two successful probes before declaring success.
- A reboot command that never produces an observed SSH outage now fails
  clearly after 60 seconds instead of appearing idle for the full recovery
  timeout.

## 1.8.4

- Device detail editing now includes the device type and reloads the selected
  type's editable fields and validation before saving.
- Reboot SSH connections support global, device-type, ordered-rule, and
  per-device port overrides. Recovery checks use the resolved SSH port too.
- Device Info supports independent HTTP and HTTPS port overrides at the same
  configuration levels and gives the AI agent ordered HTTPS/HTTP candidate
  URLs while preserving explicit addresses, paths, and queries.

## 1.8.3

- Administrators can now import a raw `DeviceSchema` document or an exported
  bootstrap ConfigMap from the Schema page. A preview shows every proposed
  addition before it is applied.
- Bootstrap imports are strictly additive: missing fields, device types,
  layouts, and plugin assignments are created, while existing configuration is
  never changed, enabled, disabled, or deleted.

## 1.8.2

- Enabling Network Scan, Device Info, or Reboot for a GUI-managed device type
  now adds the plugin's recommended schema fields. Existing administrator
  visibility choices are preserved, repeated enablement is idempotent, and
  disabling a plugin never removes fields or device data.
- Reboot configuration now resolves per device: an explicit device override,
  the first matching ordered device-field rule, the device-type setting, then
  the global deployment default. This supports rules such as selecting SSH for
  MikroTik routers while other routers continue to use AI browser discovery.
- Ordered reboot rules are managed under **Schema → Plugins**. Administrators
  can manage a highest-priority device override from the device's
  **Plugin Steps** tab.
- YAML-owned schemas remain authoritative. Device-specific overrides are not
  included in portable schema exports. No database migration is required.

## 1.7.3

- Device Info and AI reboot research workers now disable OMP model-role
  discovery during ACP startup, preventing network discovery from delaying the
  ACP handshake.
- Those workers also disable OMP's separate LiteLLM MCP gateway because
  TestBench supplies the browser tool directly through the ACP command.
- Plugin controllers recover valid structured results from streamed assistant
  events when an ACP worker finishes before emitting its separate result line.
- Device Info treats hardware, firmware, LAN MAC, and WAN MAC as independent
  optional outputs. A device layout needs any one of these fields, and MAC
  findings are written only to the matching interface field.
- No database migration is required for this release.

## 1.7.2

- Device Layout field rows now include a **Show** checkbox, allowing an
  administrator to show or hide a column without opening the field editor.
  Changes remain part of the layout's normal **Save changes** workflow.
- The app displays `TestBench v<version>` at the bottom left, opposite the
  ChainFire Labs credit. The version is embedded during the frontend image
  build; local builds without a version display `TestBench dev`.

### Reboot configuration

- Device types can now select **SSH** or **AI browser discovery** under
  **Schema → Plugins → Device Reboot**, or inherit the global method. Each type
  can also override the SSH reboot command.
- Global Helm defaults are `plugins.reboot.method: ai` and
  `plugins.reboot.sshCommand: "reboot"`. Per-type YAML plugin settings
  use `config.method` and `config.ssh_command`; omitted or null values inherit
  the global defaults. Empty SSH commands are rejected.
- **Upgrade:** vendor matching (`vendorRole` / `mikrotikValues`) no longer
  selects the reboot method. Explicitly select SSH for types that previously
  relied on MikroTik matching, or set the global method to `ssh`. Reboot still
  requires explicit enablement for each device type; defaults do not grant
  permission to reboot.

### Go client and command-line interface

- Added a Go client covering the Python client's read operations: devices,
  software, test results, identity, device types, schemas, and plugin actions.
  It supports API-token authentication, pagination, cancellation, and access
  to installation-defined fields.
- Added the `testbench` CLI, with `--devices`, `--software`, `--tests`, and
  `--types`; device-type filters; general search; repeatable column filters;
  and test lookups by device or software name. Listings support sorting,
  limits, selected columns, and table, JSON, or CSV output.
- Configure `TB_API_URL` and `TB_API_KEY`, then run `./bin/testbench --devices`.
  See the [Go client guide](../src/go-client/README.md) for build instructions
  and examples. The CLI queries data and does not modify inventory or run
  plugins.

### Python client

- Fixed pagination so a zero or negative result limit returns no rows without
  fetching pages.
- Updated checkout examples to supply the required purpose and return date.
  Existing client models remain compatible with the API.

### Deployment and upgrades

- Default-branch image builds now publish the chart application-version tag
  (`1.7.2`) alongside `latest` and the commit SHA tag, and embed that version
  in the frontend label. Explicit version-tag builds still use the Git tag.

- Every Helm `image` and `researchImage` now supports a `registry` override.
  Omitted values inherit `global.imageRegistry`; an explicit empty string uses
  the repository as written. Browser-agent images can use a private registry
  independently of TestBench images, including in plugin worker Jobs.

- Helm now defaults `global.imageRegistry` to `ghcr.io`. Installations using
  a private registry should retain an explicit registry override.
- Image publishing supports private registry mirrors, an internal Python
  package index, and optional outbound proxies for dependency downloads.
  See [container publishing](container-publishing.md) for configuration.
- Removed legacy Compose, standalone k3s, and Minikube deployment assets. Helm
  is the supported deployment path; use the CI publishing scripts or direct
  Dockerfile builds to build images.
- No database migration is required specifically for this release.

## 1.7.0

- All six application images now use non-root distroless runtimes with Python
  3.13. The frontend retains nginx and listens internally on port 8080; Helm
  preserves the configured Service port. Custom Docker mappings must target 8080.
- Set `global.imageRegistry` once to choose the registry for all Helm images,
  including plugin workers. Repository values are now relative paths; remove
  registry prefixes from existing overrides or set `global.imageRegistry: ""`.
- GitHub Actions and GitLab CI build and publish all application images, with
  support for platform registries or a configured internal registry.
- Device Info correctly recognizes discovery roles configured on device types.
- Fixed account access checks, CSV round trips, type-specific inventory exports
  and actions, identifier URL handling, and network-scan worker authorization
  and cancellation.
- Local image builds use Dockerfiles directly and no longer require Compose.
- No database migration is required specifically for this release.

## 1.6.6

- The **Schema** admin area is simpler. Device Global and Device Types are one
  **Device Layouts** screen: the inherited set and every device type are entries
  in one list, so what inherits from what is something you can see rather than
  something you have to remember across two tabs.
- A layout lists only the fields the page actually has, rather than every
  definition that exists with the unused ones greyed out. **+ Add field**
  offers the rest, and can create a new definition and place it in one action
  instead of sending you to another tab to define it first.
- The per-row grid of tri-state dropdowns is gone. A field opens an editor that
  says whether each setting is inherited or overridden, and gives an override
  back to inheritance in one click.
- Fields reorder by dragging (Alt+↑ / Alt+↓ from the keyboard). The identity
  column is shown as anchored rather than offering a move the save would undo.
- A device type can drop an inherited field where the installation allows it,
  and the fields it has dropped are listed and restorable — previously the
  exclusion existed in the API with nothing in the GUI to reach it.
- Validate and Republish moved behind an **Advanced** menu, and saving a layout
  is one **Save changes** button. Leaving a layout with unsaved edits now asks
  first.
- **Edit type** sits on each device type's layout, for its display name,
  description, menu order and enabled state. The immutable key is shown beside
  the name so it is clear what a rename does and does not change.
- **Fixed:** an object a `DeviceSchema` document introduced by `bootstrap` and
  later dropped from that document was never retired by an `authoritative`
  pass — it stayed enabled indefinitely, because handing the object to the GUI
  also erased the record of where it came from. Editability and provenance are
  now tracked separately: a bootstrap still hands over who may edit an object,
  the document keeps the right to retire what it created, and configuration
  made in the admin GUI is still never retired by a reconciliation.
- Device exports give every key a layout does not account for a column of its
  own, with a heading, instead of one column of JSON — readable in a
  spreadsheet, and it re-imports unchanged. `?expand_misc=false` returns the
  single-column shape for a script reading fixed headers.
- **Fixed:** exports omitted `misc_data`, silently dropping the values a
  layout does not account for — the one part of a device no other column
  carries, so a restore from that file lost them. `type` exports now include it
  when the page shows it, `all` exports always do, and the import side already
  read the column back.
- **Fixed:** `misc_data` reported fields that have a column of their own.
  It subtracted a fixed list of the attribute names the device model happens to
  flatten — a list that predates installation-defined fields — so `username`,
  `password`, `serial_number` and every field an installation had defined were
  shown both in their own column and again as unexplained data. It is now the
  document minus whatever the device's type actually shows, resolved from the
  published schema on every read path (list, detail, search, export). Values
  whose field has left the layout still surface there, which is what it is for.

## 1.6.3

- Device actions and collection actions are now omitted when their plugin is
  not enabled for the applicable device type; legacy Scan/Info fallbacks no
  longer bypass device-type plugin policy.
- The Devices menu no longer includes a permanent Uncategorized entry. Untyped
  devices remain visible in All Devices.
- The admin area is now a unified **Schema** page for Devices, Software, and
  Tests. Administrators can add, edit, and delete additional Software/Test
  columns at runtime; identity and device/software/test relationship fields
  remain protected by the API and cannot be removed or made optional.
- Helm now exposes the same model under one `schema` values section, with
  `software`, `tests`, and `devices` children. The old `config.entityFields`
  and top-level `deviceSchema` values have been removed.
- AG Grid column filters now show a searchable checklist of distinct values.
  Values can be unchecked to hide matching rows, and the selection is retained
  in saved table views.

- **Dynamic device schema.** Device fields are now reusable definitions assigned
  globally or to individual device types, replacing the frozen per-installation
  device column list. A field is defined once — with its type, choices,
  validation rules, indexing and the semantic role plugins ask for — and then
  assigned wherever it is wanted. Global assignments are inherited by every
  device type, present and future. See [device-schema.md](device-schema.md).
- Administrators manage all of it from the new **Schema** area: Device Fields,
  Global Fields, Device Types and Plugins, with a preview of what each scope
  resolves to and a validation report before anything is published.
- Publishing checks the devices already stored. A newly required field that
  existing devices leave empty, a type change their values contradict, or a
  uniqueness rule two devices already break is reported and the change refused,
  rather than becoming a rule that breaks the next save.
- Removing a field from a layout — hiding it, taking it off a type, disabling
  the definition, or moving a device to another type — never deletes stored
  values. Deleting a definition outright is refused while devices hold values
  for it.
- Every device write path (API, bulk, CSV and JSON import, plugin callbacks)
  validates against the same published schema, and indexed or unique fields get
  managed PostgreSQL expression indexes whose applied state and failures are
  visible at `GET /device-schema/indexes`.
- The device API takes a structural envelope plus one `data` document. Flattened
  payloads and responses are still accepted and returned, so existing clients
  keep working.
- `unique_id` moved from the JSON document into a real indexed column: it is
  durable identity used by URLs, imports, test results and integrations.
- **Per-type plugin policy.** Plugins are deny-by-default and must be enabled
  for each device type. `GET /devices/{id}/actions` returns each action with the
  reason it can or cannot run, and every invocation repeats the check — so
  Reboot can be enabled for Routers and is impossible to invoke for a phone,
  including through a direct API call. Mixed multi-device selections are refused
  with every ineligible device named. Manifests now declare action risk,
  semantic requirements and whether an action may be assigned globally at all.
  Plugins receive only the roles their action declared, not the whole device
  document.
- **Optional GitOps ownership.** The schema can be declared in a versioned
  `DeviceSchema` YAML document mounted as a ConfigMap, with `bootstrap`, `merge`
  and `authoritative` reconciliation. Ownership is explicit and shown in the UI,
  conflicts are reported rather than guessed at, and an invalid document leaves
  the last published schema active. `GET /device-schema/reconciliation` reports
  status for Flux.
- Bootstrap reconciliation now hands imported schema objects to the admin GUI,
  as documented. Installations bootstrapped by an older release repair that
  ownership automatically on their next reconciliation.
- Helm no longer attempts to adopt the release namespace when an isolated
  plugin namespace is requested without a name. The Minikube lifecycle assigns
  network-scan workers their own `<release-namespace>-scan` namespace.
- Exports take a `columns` option (`type`, `all`, `data`); import templates can
  be generated per device type; saved table layouts are scoped per device type
  so a router layout no longer overwrites a phone one.
- The Python client gains `device_types()`, `device_schema()`,
  `devices.actions()` and `Device.data`; MCP exposes device types in
  `testbench://schema`, a `device_type` filter on `find_devices`, and a
  `list_device_actions` tool.

  Upgrading: existing device fields and per-type column lists are converted
  automatically during the dynamic device-schema transition. Plugins are the one
  thing that needs attention — they are deny-by-default now, so enable each one
  for the device types that should have it under Schema → Plugins.

## 1.5.4

- Added administrator-managed device types with seeded categories, dedicated
  inventory URLs, per-type columns and required fields, type-aware detail
  pages, filtering, import/export support, and Python-client metadata.
- Device automation passwords are now stored as plaintext in PostgreSQL and no
  longer require a Fernet key. Application-user passwords remain bcrypt-hashed.
- OIDC authorization and token exchange now derive their redirect URI from the
  configured public frontend URL, preserving HTTPS behind ingress proxies.
- Renamed the Helm migration Job to `database-schema` to clarify that it creates
  application tables on first install and updates them on later releases.
- The database-schema Job now waits for the external database with a configurable
  bounded retry period before running Alembic.

## 1.5.3

- The Helm chart can deploy the read-only MCP service, use existing private
  registry pull Secrets across controllers and worker Jobs, and connect with a
  pre-provisioned PostgreSQL application login without administrator bootstrap
  credentials.
- Helm values enumerate every predefined optional entity field so operators can
  remove unwanted columns before the field catalog is initialized.
- Renamed the tests API/catalog field `data` to `misc_data`, including dynamic
  CSV import measurements and the Python client, to match other entities.
- Added predefined, indexed optional device fields for IMEI, serial number, WAN
  MAC, and LAN MAC. WAN MAC is the device-info plugin's discovered MAC target.
- Added consistent ChainFire Labs attribution to the login and application UI.

## 1.5.1

Images changed: **backend**, **frontend**, **mcp**, **device-info**, and
**reboot**. The unchanged network-scan image is republished with the release
tag for a consistent stack version.

- Device Info no longer saves a blank discovery recipe when the first model
  cannot identify a device, and saved recipes cannot be overwritten by repeat
  runs.
- Device Info and Reboot now use one OpenAI-compatible endpoint/key contract,
  including LiteLLM-compatible environment aliases, with explicit Secret-key
  selection in the Helm chart.
- Plugin output retention is larger, complete event history remains available,
  and the run viewer stops auto-scrolling while an operator reads older output.
- The device detail compatibility tab is now named **Vendor Claims**.
- Frontend dependencies and the container build toolchain are updated, including
  AG Grid 36 and reproducible `npm ci` installs.

## 1.5.0

Images changed: **backend**, **frontend**, **mcp**, **network-scan**,
**device-info**, and **reboot**.

### Helm deployment and plugin architecture

- Helm is now the supported installation path for the frontend and backend,
  using a user-provided PostgreSQL database. Docker Compose remains under
  `docker/` as a legacy development stack.
- Network Scan, Device Info, and Reboot are independently enabled plugins.
  Kubernetes RBAC lets their controllers create bounded, one-shot worker Jobs
  in dedicated namespaces. Each plugin defaults to five concurrent workers.
- The disposable Minikube harness builds and loads all local images, creates a
  fresh external PostgreSQL container, installs the chart, and preloads a scan
  target. Its lifecycle and application configuration live in one editable
  `scripts/minikube/values.yaml` file.

### Device discovery and reboot automation

- Device Info can run a browser-capable OMP image through ACP, stream useful
  agent events to the UI, select separate discovery and repeat models, and POST
  normalized hardware, firmware, and MAC findings back to TestBench.
- Successful Device Info and Reboot procedures are stored as versioned plugin
  artifacts. Device details expose those steps for review, editing, clearing,
  and reuse by later runs.
- Reboot uses Paramiko for MikroTik devices and the ACP browser workflow for
  other devices. Runs can be watched, reopened, timed, and cancelled from the
  plugin dialog.
- Network Scan uses the same plugin/Job framework and reports its probe progress
  and results in the run output.

### Dynamic inventory and credentials

- Built-in inventory fields always exist in PostgreSQL. `optional_fields`
  controls which are visible; omitted fields stay hidden and are not required
  by create/update requests. Installation-specific `custom_fields` remain
  supported, with optional semantic roles used by plugins.
- Device username and password are per-device fields shared by Device Info and
  Reboot. Passwords are readable by authenticated application/API clients and
  displayed as plaintext in device forms, while remaining Fernet-encrypted in
  PostgreSQL. Intentionally blank passwords are supported.
- Plugin output is streamed without credential redaction. Operators should
  treat plugin-run output and Kubernetes worker logs as credential-bearing.

### Packaging

- Source is organized under `src/`, the supported chart under `helm/`, and the
  legacy Compose files under `docker/`; existing `k3s/` assets remain in place.
- `scripts/build-images.sh` builds and publishes versioned and `latest` tags for
  all six TestBench images. The Helm chart and application images are versioned
  `1.5.0`.
