# TODO

## Delete a specific software version in the GUI

- [x] Add a Delete action for the selected version on the software detail page, including older versions listed under **All Versions**.
- [x] Show the software name and exact version in the confirmation, along with the number of associated test results, vendor claims, and components that deletion will remove. These records are currently deleted by database cascades.
- [x] Call `DELETE /api/v1/software/{id}` with the selected version's ID. Do not use its name, which resolves to the current version.
- [x] After deletion, refresh the version list and navigate to the new current version, or back to the Software list if the last version was deleted. Show success and API errors.
- [x] Show the action only to users with `software.edit`, consistent with the existing Software list Delete action.
- [x] Add a UI test covering deletion of an older version and the resulting navigation.

## Find suite components in global search

- [x] Match component names and component versions in `GET /api/v1/search` so a component query returns its parent software suite under **Software**. Keep one result per matching software version even when several components match.
- [x] Show the matching component name alongside the suite name and software version in the search result, so the reason for the hit is clear.
- [x] Link each result to the exact software version containing the component, including older unversioned records.
- [x] Cover component-only matches, multiple matching components in one suite version, and an older-version-only match in API and UI tests.
- [x] When a device search also returns tests for that device, show each test's software suite/version and tested component name/version in the **Tests** search results.
- [x] Show a concise tested-component summary on the matching **Device** result itself, with component name/version and a way to see the device's full test history. Bound the preview so devices with many tests do not make search results unwieldy.
- [x] Make a test search result open the device's tests view with that test highlighted, instead of navigating to the unfiltered Tests list. Cover device searches with component-linked tests and tests without a component.

## Permission finding

Creating software (`POST /api/v1/software`), adding a version (`POST /api/v1/software/{id}/versions`), and editing an existing version (`PATCH /api/v1/software/{id}`) all require `software.edit`. The version Delete action uses the same permission. The built-in **Tester** and **Administrator** roles initially grant it; roles can be edited under **Users & Roles → Roles**. `schema.manage` is needed to define software fields, not to enter values in existing fields.

## View device output from external object storage

- [x] Make S3-compatible object storage optional and disabled by default. Configure an externally managed endpoint, bucket, access key, and secret key; do not deploy an object store with TestBench.
- [x] Provide an assignable device **Output** field for an `s3://bucket/key` reference when object storage is enabled.
- [x] Fetch output on demand through an authenticated device endpoint, restricted to the configured bucket and a size limit. Reject non-ASCII and binary content.
- [x] Display the fetched text in a scrollable device-detail view and report missing objects and fetch errors.
- [x] Test output URL validation, bucket and size restrictions, ASCII validation, and access control.

## Replicate TestBench instances

- [x] Add an optional Helm-enabled replication plugin with push-only and push/pull modes.
- [x] Sync devices, software, and tests between instances on a configurable interval or by manual trigger.
- [x] Resolve push/pull conflicts by newest edit and propagate deletions; keep users, roles, credentials, and local plugin settings separate.
- [x] Show replication status and provide a manual **Sync now** action.
- [x] Test replication authentication, record exchange, and conflict handling.

## View large test output from object storage

- [ ] Add a private S3-compatible object store for full test output. Keep the object key and metadata (size, encoding, checksum, and a short preview) associated with the test in PostgreSQL; keep full stdout/stderr out of `notes`, `misc_data`, normal test list responses, exports, and audit payloads.
- [ ] Accept output uploads with a size limit, preserving stdout/stderr identity and line order. Compress stored output. Mark the output available only after the upload succeeds, and clean up failed uploads and objects belonging to deleted tests according to a defined retention policy.
- [ ] Add an authenticated test-output read endpoint that checks access to the test and streams a requested chunk or supplies a short-lived download URL for the private object.
- [ ] Add a **Test output** action on test rows. Open a scrollable modal that fetches output on demand, shows an initial chunk or tail, supports loading more and downloading the full output, and handles missing or failed fetches.
- [ ] Render output as plain text, preserve line breaks, and avoid loading an entire large log into the browser at once. Decide how credentials and other secrets in captured output will be redacted before storage or display.
- [ ] Test access control, upload limits, chunked viewing, missing objects, and deletion cleanup.
