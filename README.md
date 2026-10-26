# TestBench

TestBench helps teams manage shared test devices, track the software used on them,
and record test results. It brings inventory, device availability, software
compatibility, and testing history into one web app.

![TestBench device inventory showing the All Devices view](screenshots/testbench_main.png)

- **Device inventory:** organize devices by type, with configurable fields for
  locations, hardware, firmware, network addresses, and other details. Change a
  device's type from its detail-page editor when it moves to another schema.
- **Shared equipment:** check devices out, track return dates, and receive
  upcoming and overdue reminders.
- **Software and tests:** track software versions, vendor compatibility claims,
  optional version-specific [software bundles](docs/software-bundles.md), and
  actual test outcomes against individual devices. Search every vendor's
  compatibility list at once from **Vendor Claims**, to answer "does anything
  claim to support this hardware?".
- **Team access:** [roles you define](docs/roles.md), each granting a set of
  permissions, with an audit trail of changes and a per-device changelog.
- **Data access:** spreadsheet-style editing, JSON/CSV import and export,
  additive DeviceSchema YAML import/export, a REST API, and an optional
  read-only MCP server for AI assistants.

## Images

The chart defaults to registry `ghcr.io`, with TestBench images under
`chainfirelabs/testbench` and tag `1.15.0`.

| Image repository | Purpose |
|---|---|
| `chainfirelabs/testbench/frontend` | The web interface. |
| `chainfirelabs/testbench/backend` | The application API, authentication, inventory, and test records. Also used by the chart's database schema setup job. |
| `chainfirelabs/testbench/mcp` | Optional read-only access for AI assistants through the Model Context Protocol. |
| `chainfirelabs/testbench/network-scan` | Network Scan plugin controller and scan workers. |
| `chainfirelabs/testbench/device-info` | Device Info plugin controller, which starts device discovery jobs. |
| `chainfirelabs/testbench/reboot` | Reboot plugin controller and SSH reboot workers. |
| `chainfirelabs/testbench/test-summary` | AI Test Summary plugin, which writes AI reports on test runs. |
| `oh-my-pi` | Separate browser-agent image used by Device Info and browser-based reboot jobs. Defaults to tag `latest`; configure a compatible image available in your registry. |

Set `global.imageRegistry` to change the default registry for images. Repositories
are relative to that registry; set it to `""` to use fully qualified repository
names. Each image has a `repository`, `tag`, and optional `digest`; a digest
takes precedence over the tag. Browser-agent images use the plugin's
`researchImage` settings and must provide Python 3, `/usr/local/bin/entrypoint.sh`,
and `omp acp` with ACP v1 support.

Individual Helm `image` and `researchImage` settings accept `registry` to
override `global.imageRegistry`. Omit it to inherit the global default, or set
`registry: ""` to use a fully qualified repository as written. See the
[per-image registry example](helm/testbench/README.md#private-container-registry).

## Plugins

Plugins add optional actions to the device inventory and to test runs. All of
them are disabled by default. Enable a plugin in Helm; device plugins must then
be allowed for the relevant device types in **Schema → Plugins**. Device fields use semantic roles, such as
`scan_address_lan` or `device_vendor`, so plugins can work with your own column names.

| Plugin | What it does | Helm switch |
|---|---|---|
| Network Scan | Checks device reachability through individual scans, Scan all, or scheduled scans. | `plugins.networkScan.enabled: true` |
| Device Info | Uses a browser agent to identify hardware, firmware, and MAC details on eligible online devices. Confirms before starting the agent and saves successful discovery steps for reuse. | `plugins.deviceInfo.enabled: true` |
| Reboot | Restarts eligible online devices and checks that they return online. Uses per-type SSH or AI browser settings with global defaults. | `plugins.reboot.enabled: true` |
| AI Test Summary | Adds **Summarize with AI** to every test run: an AI-written report on the run's devices, results, outputs and files, saved to the run's shared files. Global; not enabled per device type. | `plugins.testSummary.enabled: true` |

Device Info and Reboot use per-device credentials. Browser-agent actions require
an AI endpoint and model from either Helm or the admin GUI; Helm-managed AI also
uses an API-key Secret. SSH reboot does not. Saved steps can
be viewed, edited, or cleared in the device's **Plugin Steps** tab. Reboot must
be allowed explicitly per device type.

### Network Scan

Network Scan reports each address and successful probe while it checks device
reachability.

![Completed network scan output for a device](screenshots/testbench_scan.png)

### Device Info

Device Info streams the browser agent's progress and presents the structured
hardware and firmware findings when research completes.

![Completed AI-assisted Device Info output](screenshots/testbench_device_info.png)

### Reboot

Reboot streams the selected SSH or browser-agent workflow and verifies that the
device returns online.

![Completed browser-assisted device reboot output](screenshots/testbench_reboot.png)

### AI Test Summary

**Summarize with AI** on a test run's page sends the whole run to an AI model:
every test with all of its fields, notes and output, each tested device (across
every device type in the run, without sensitive fields), and the text of the
run's shared files and each test's artifacts. Binary files are listed by name,
type and size. Text that looks like a credential (passwords, tokens,
authorization headers, private keys, credentials in URLs) is removed first.

The plugin computes the result tables itself (outcomes, and results by device,
software, component and tag), so the numbers are always exact; the model writes
the summary, failures and warnings, device and software differences, notable
artifacts and recommended follow-ups. Large runs are read in parts, failures
first, and combined.

Each report is saved as a new `ai-summary-<UTC time>.md` file in the run's
shared files, so every version is kept; earlier reports are not fed back into
new ones. Saving needs object storage; without it the report is shown but not
kept. Anyone who can record tests (testers and admins) can run it.

Choose the provider and model under **AI Providers** (AI Test Summary), or set
`plugins.testSummary.ai.url` and `model` to manage them from Helm. Replace the
default instructions with `plugins.testSummary.prompt`; rules that keep the
model to the run's data are always added.

## Install with Helm

The [TestBench chart](helm/testbench/) supports Kubernetes 1.27 or newer. It
installs the frontend and backend, plus any enabled plugins and MCP server.
Provide an existing PostgreSQL database and login; the chart creates and updates
application tables using that login.

Create these Kubernetes Secrets in the release namespace before installation:

| Secret in the example | Required keys |
|---|---|
| `testbench-database` | `username` and `password` for the existing PostgreSQL database. |
| `testbench-application` | `TB_JWT_SECRET` with a long random signing secret. Set `TB_ADMIN_USERNAME` and `TB_ADMIN_PASSWORD` for the initial admin account. |
| `testbench-plugins` (when enabling plugins) | `shared-secret` for communication between the backend and plugins. |

Save your settings in `my-values.yaml`, for example:

```yaml
database:
  host: postgres.example.com
  name: testbench
  sslmode: require
  credentials:
    existingSecret: testbench-database

applicationSecret:
  existingSecret: testbench-application

config:
  frontendUrl: https://testbench.example.com
  corsOrigins: https://testbench.example.com
  timezone: America/New_York

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: testbench.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: testbench-tls
      hosts: [testbench.example.com]
```

This example assumes an installed ingress controller and an existing TLS Secret
named `testbench-tls`.

```bash
helm upgrade --install testbench ./helm/testbench \
  --namespace testbench --create-namespace \
  --values my-values.yaml
```

## Helm values

The tables below cover the main settings. The commented
[values.yaml](helm/testbench/values.yaml) contains every value and the complete
starter device catalog.

### Application and networking

| Value | Default / options |
|---|---|
| `global.imageRegistry` | `ghcr.io`; registry host with optional port/path, without a URL scheme. |
| `global.imagePullSecrets` | `[]`; existing registry Secret references, e.g. `[{name: registry-credentials}]`. |
| `frontend.replicaCount` / `backend.replicaCount` | `2` each. |
| `frontend.image.*` / `backend.image.*` | Image repository, tag, digest, and `pullPolicy`: `IfNotPresent`, `Always`, or `Never`. |
| `frontend.resources` / `backend.resources` | CPU and memory requests and limits; see values.yaml for defaults. |
| `frontend.service.type` | `ClusterIP`, `NodePort`, or `LoadBalancer`; default `ClusterIP`. |
| `frontend.service.port` / `nodePort` | `80` / unset; optional fixed node port for NodePort or LoadBalancer services. |
| `applicationSecret.existingSecret` | Required existing Secret containing application `TB_*` secrets. |
| `TB_CREDENTIAL_ENCRYPTION_KEY` | Application Secret value used to encrypt API keys entered under **AI Providers**. Required only for GUI-managed credentials. |
| `backend.objectStorage.enabled` / `url` / `bucket` / `maxBytes` | External S3-compatible storage for test output and test artifacts. Disabled by default; test output defaults to a 1 MiB limit. The chart creates no bucket or S3 service. |
| `TB_OBJECT_STORAGE_ACCESS_KEY` / `TB_OBJECT_STORAGE_SECRET_KEY` | Put these in `applicationSecret.existingSecret` when object storage is enabled. The backend reads, writes, and deletes test output and artifacts under `tests/`. |
| `authentik.tls.existingConfigMap` / `key` | Optional ConfigMap and PEM key for trusting a private Authentik CA; empty by default. |
| `config.frontendUrl` | Required public URL, such as `https://testbench.example.com`. |
| `config.corsOrigins` | Required comma-separated browser origins. Usually the public URL. |
| `config.timezone` | `UTC`; an IANA timezone such as `America/New_York`. |
| `config.checkoutSweepMinutes` | `60`; reminder check interval in minutes. `0` disables checks. |
| `config.checkoutWarnDays` | `3`; days before a return date that warnings begin. |
| `ingress.enabled` / `className` | `false` / `""`; enable ingress and choose a controller class, or leave the class empty for the cluster default. |
| `ingress.hosts` / `tls` / `annotations` | `[]` / `[]` / `{}`; host paths, TLS Secret references, and controller annotations. |

### PostgreSQL

| Value | Default / options |
|---|---|
| `database.host` | Required PostgreSQL hostname. |
| `database.port` / `name` | `5432` / `testbench`. |
| `database.sslmode` | `require`; also accepts `disable`, `allow`, `prefer`, `verify-ca`, or `verify-full`. |
| `database.credentials.existingSecret` | Required Secret containing the database login. |
| `database.credentials.usernameKey` / `passwordKey` | `username` / `password`; keys within that Secret. |
| `database.waitTimeoutSeconds` / `waitIntervalSeconds` | `120` / `5`; how long schema setup waits for the database and how often it retries. |

### Object storage for test output and artifacts

Set `backend.objectStorage.enabled: true`, `backend.objectStorage.url` to the
S3-compatible service endpoint (for example, `https://s3.example.com`), and
`backend.objectStorage.bucket` to the dedicated bucket name. Put
`TB_OBJECT_STORAGE_ACCESS_KEY` and `TB_OBJECT_STORAGE_SECRET_KEY` in the existing
application Secret. Outside Helm, set `TB_OBJECT_STORAGE_ENABLED=true`,
`TB_OBJECT_STORAGE_URL`, `TB_OBJECT_STORAGE_BUCKET`, and the same two credential
variables on the backend.
The default `TB_OBJECT_STORAGE_MAX_BYTES` is 1048576. TestBench does not create
or manage buckets and never receives the credentials in the browser.

Keep the bucket private. TestBench writes only under the `tests/` and
`test-runs/` prefixes.

### Test output

Each recorded test can carry one text output file, such as a run log. Use
**Output** on a Tests row, a Device → Tests row, or a test in Software → Tested
Devices: **View output** opens the text in a dialog, with **Download output** to save it as a file,
and users with `tests.edit` can upload, replace, or delete it there (**Add
output** when a test has none). The file lives beside the test's artifacts:

```text
tests/<test-id>/output/<filename>
```

Output must be ASCII text with ordinary line breaks and tabs, up to
`backend.objectStorage.maxBytes` (`TB_OBJECT_STORAGE_MAX_BYTES`, default 1 MiB);
binary or oversized files are refused at upload. Uploading again replaces the
previous output. Automation can attach output after recording a test:

```sh
curl --fail-with-body -H "Authorization: Bearer $TB_TOKEN" \
  -X PUT -F 'file=@run.log' \
  "$TB_API_URL/tests/$TEST_ID/output"
```

`GET /tests/{test_id}/output` returns the file name, size, and text;
`DELETE /tests/{test_id}/output` removes it. Uploads and deletions are audited.
Migration `0018_test_outputs` adds the metadata; like artifacts, output is kept
in S3 when a whole test is deleted and is not replicated or exported.

Devices no longer have an Output field. On upgrade the system field is switched
off; an `s3://` URL already entered on a device remains in its data and appears
under Misc Data.

### Downloadable test artifacts

With object storage enabled as above, use **Artifacts** on a Tests row or on a
Device → Tests row to upload one or more files and download associated reports,
logs, screenshots, ZIPs, or other binary files. Uploading and deleting requires
`tests.edit`; authenticated readers can list and download. Files remain private
in S3 and downloads pass through the authenticated backend. Storage credentials
and object keys are never returned to the browser.

Each upload gets its own object key in the configured bucket:

```text
tests/<test-id>/artifacts/<artifact-id>/<filename>
```

The test ID is the recorded test's immutable ID, so repeated runs are separate.
Duplicate filenames are allowed because artifact IDs prevent overwrites.
Migration `0017_test_artifacts` adds metadata; file bytes stay in S3. Allow the
configured storage identity `s3:GetObject`, `s3:PutObject`, and
`s3:DeleteObject` on `<bucket>/tests/*` and `<bucket>/test-runs/*`; test output
uses the same prefixes. A test in a [test run](#test-runs) stores its files in
the run's folder under `test-runs/`.
No bucket listing permission or public bucket access is required by the app.

`backend.objectStorage.artifactMaxBytes` (or `TB_ARTIFACT_MAX_BYTES` outside Helm)
defaults to 104857600 bytes (100 MiB) per file, independently of the test output
limit. Helm adjusts the frontend proxy's request limit with multipart overhead.
Any external ingress or proxy must also permit that request size. The standalone
frontend proxy permits 101 MiB requests; customize its Nginx config if you raise
the backend limit beyond 100 MiB. Uploads use disk spooling and downloads stream
from S3; browser downloads are buffered by the existing download helper.

Automation can upload files after recording a test using its login or API token:

```sh
curl --fail-with-body -H "Authorization: Bearer $TB_TOKEN" \
  -F 'file=@report.zip' \
  "$TB_API_URL/tests/$TEST_ID/artifacts"
```

Here `TB_API_URL` includes `/api/v1`. Repeat the request for additional files.
`GET /tests/{test_id}/artifacts` lists metadata and the upload limit;
`GET /tests/{test_id}/artifacts/{artifact_id}/download` downloads a file;
`DELETE /tests/{test_id}/artifacts/{artifact_id}` deletes the file and metadata.
Uploads and explicit artifact deletions are audited against the test.

Deleting an individual artifact removes its S3 object. Deleting a whole test
(including through device/software deletion) removes its artifact metadata but
retains the S3 objects for operator-managed retention. Configure an S3 lifecycle
policy on `tests/` if retention must be bounded; such policies also expire files
for retained tests. Artifact metadata and bytes are not included in replication
or test CSV/JSON exports. Back up the database and bucket together.

### Test runs

A test run is one execution of a pipeline or test suite: the tests it recorded,
and files it shares with all of them, such as a pytest report, stored once
instead of uploaded to every test. The Tests list has a **Test Run** column,
which filters by run; clicking a run opens its page, with its pass, fail and
warn counts, its shared files (upload, download and delete them there), and the
usual tests table, limited to the run's tests. **+ New test** on that page
records a test in the run. The **+ New test**
dialog on Tests has a **Test Run** field too: pick an existing run, or type a new
name to start one; its **Test Run Files** field uploads files such as a report or
test outline to the run, shared with every test in it. Each test's
**Artifacts** dialog lists the run's shared
files under its own, with the button counting both (`Artifacts (1 + 1 shared)`).

A pipeline starts a run, attaches its report, and records its tests in it:

```sh
RUN_ID=$(curl --fail-with-body -s -H "Authorization: Bearer $TB_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"name\": \"Nightly #$CI_PIPELINE_ID\", \"external_id\": \"$CI_PIPELINE_ID\", \"url\": \"$CI_PIPELINE_URL\"}" \
  "$TB_API_URL/test-runs" | jq -r .id)
curl --fail-with-body -H "Authorization: Bearer $TB_TOKEN" \
  -F 'file=@report.html' "$TB_API_URL/test-runs/$RUN_ID/artifacts"
# then "test_run_id": "$RUN_ID" in each POST /tests body
```

`external_id` is optional and unique. Starting a run with an `external_id` that
is already in use returns that run (200 instead of 201), so every job of one
pipeline can start "its" run and they all join the same one. `PATCH
/test-runs/{id}` renames a run or sets `finished_at`; `PATCH /tests/{id}` with
`test_run_id` moves a test into a run, and `null` takes it out. `GET
/tests?test_run_id={id}` lists a run's tests (`none` lists tests outside any
run). Links must be `http://` or `https://` addresses.

The bucket keeps a run's files together, named so they can be found by eye:

```text
test-runs/<run-name>_<run-id>/artifacts/<artifact-id>/<filename>         shared by the run
test-runs/<run-name>_<run-id>/tests/<test-id>/artifacts/<artifact-id>/…  a test's own
test-runs/<run-name>_<run-id>/tests/<test-id>/output/<filename>
```

The folder is named when the run is created, so renaming the run does not split
its files. A test's files go in its run's folder when they are uploaded while
the test is in the run; files uploaded before, or while it was in another run,
stay where they were. Nothing is moved.

Deleting a run keeps its tests, which are then in no run. Like deleting a test,
it removes the record of the run's shared files but leaves the files in the
bucket for your retention policy; deleting a shared file on its own removes it
from the bucket. Runs travel with their tests in replication; their files, like
test artifacts, do not. Migration `0020_test_runs` adds runs.

### Replication between TestBench instances

Replication is an optional part of the Helm chart, not a plugin: it is
configured under the top-level `replication` key, needs no plugin shared
secret, and does not appear under **Schema → Plugins**.
Enable `replication.enabled` in the Helm values on both instances.
Give both instances the same random token through
`replication.token.existingSecret`. Set `replication.peerUrl` on the sending instance
to the receiving instance's HTTPS API base URL, such as
`https://peer.example.com/api/v1`. Choose `mode: push` for one-way delivery or
`mode: push_pull` to reconcile both instances using the newest record edit.
Set the destination to `mode: receive` when it should accept data without
starting its own outbound sync; it does not need a `peerUrl`.
`intervalMinutes` defaults to 60; set it to 0 for manual-only syncing. A user
with `settings.manage` can start a sync and see its result on the
**Replication** page. The page shows the Helm-configured destination URL.
Manual sync requests and completed manual or scheduled syncs appear in the
audit log with the destination URL and outcome.

Replication exchanges devices, software versions and their components, vendor
claims, and tests. It preserves record IDs and propagates deletes using durable deletion
markers. User accounts, roles, API keys, local settings, and device fields
marked sensitive are not copied. Configure matching device types and field
schemas on both instances before syncing; a missing device type or a duplicate
natural identifier with a different record ID stops that batch with an error
instead of silently overwriting data. Keep both clocks synchronized: newest
edit resolution uses record timestamps.
To sync only selected types, set `replication.entities` on the sender
to a list such as `[devices, software]`; the default syncs all four types.
Tests require their referenced devices and software to exist on the destination;
vendor claims require their referenced software.

### Inventory fields

The example external catalog starts with Routers, Mobile Phones, and Servers.
Administrators can manage device types and the Device, Software, and Test fields
in **Schema**.

| Value | Default / options |
|---|---|
| `schema.devices.source` | `database` (default) leaves configuration to the admin GUI; `configMap` loads an external YAML catalog. |
| `schema.devices.reconciliation` | `bootstrap` imports once, then hands ownership to the GUI. `merge` adds configuration without deleting GUI-created entries and reports conflicts. `authoritative` keeps YAML in control. |
| `schema.devices.existingConfigMap` / `key` | Required ConfigMap name / `device-schema.yaml`; the chart mounts this external bootstrap document. |
| `schema.devices.allowGlobalExclusions` | `false`; whether device types may exclude global fields. |
| `schema.devices.rejectUnknownFields` | `false`; set `true` to reject writes containing undefined field keys. |
| `schema.software.optionalFields` / `schema.tests.optionalFields` | Lists of optional built-in fields to include in the initial catalog. |
| `schema.software.additionalFields` / `schema.tests.additionalFields` | `[]`; custom field definitions, e.g. `[{key: lab, label: Lab, type: select, options: [East, West]}]`. Vendor-device fields belong in `spec.vendorDeviceFields` of the external DeviceSchema ConfigMap. |

Required identity and relationship fields are always included. See
[device schema configuration](docs/device-schema.md) for field types and semantic roles.

The Schema page can download the live DeviceSchema as bootstrap YAML and import
either that ConfigMap or a raw `DeviceSchema` document. Import shows a preview
and is strictly additive: it creates only missing fields, types, layouts, and
plugin assignments; it never changes or deletes existing configuration.

### Plugin settings

All plugins require `plugins.sharedSecret.existingSecret`; its key defaults to
`shared-secret`. Each plugin has image settings. The device plugins have
`maxConcurrentPods` (default `5`) to limit how many of their jobs run at once;
AI Test Summary runs no jobs and uses `maxConcurrentRuns` instead.

| Values under `plugins.` | Default / options |
|---|---|
| `networkScan.timeoutSeconds` / `concurrency` | `2` seconds per connection / `10` devices probed concurrently per worker. |
| `networkScan.intervalMinutes` | `15`; set `0` to disable scheduled scans. |
| `deviceInfo.ai.url` / `reboot.ai.url` | Optional OpenAI-compatible API base URL. Setting URL and model locks that plugin's AI configuration to Helm; leaving both empty enables admin GUI management. |
| `deviceInfo.ai.model` / `reboot.ai.model` | Helm-managed discovery model. Must be set together with the corresponding URL. |
| `deviceInfo.ai.repeatModel` / `reboot.ai.repeatModel` | Optional model used when a saved successful recipe exists. Empty uses `ai.model`. |
| `deviceInfo.ai.existingSecret` / `reboot.ai.existingSecret` | Required only for Helm-managed AI settings; API-key Secret in the job namespace. `ai.apiKeySecretKey` defaults to `OPENAI_API_KEY`. |
| `deviceInfo.prompt` / `reboot.prompt` | Task instructions supporting `{device_url}` and `{device_json}`. Credentials and required result instructions are appended automatically. |
| `deviceInfo.researchImage.*` / `reboot.researchImage.*` | Browser-agent repository, tag, and optional digest. |
| `deviceInfo.httpPort` / `deviceInfo.httpsPort` | `80` / `443`; global web-interface ports. Type, ordered-rule, and device `http_port` / `https_port` settings override them independently. Device Info gives the agent HTTPS then HTTP candidates. |
| `reboot.method` | `ai`; global method (`ssh` or `ai`). Per-type plugin `config.method` overrides it. Replaces vendor matching. |
| `reboot.sshCommand` | `reboot`; global nonempty SSH command. Type, ordered-rule, and device `ssh_command` settings override it; null/omitted inherits. |
| `reboot.sshPort` | `22`; global SSH port. Type, ordered-rule, and device `ssh_port` settings use the same override precedence. |
| `reboot.recoveryTimeoutSeconds` | `300`; time allowed for a device to return online. |
| `testSummary.ai.url` / `model` / `existingSecret` | Optional OpenAI-compatible endpoint, model and API-key Secret (release namespace, key `ai.apiKeySecretKey`, default `OPENAI_API_KEY`). Setting URL and model locks the AI settings to Helm; leave both empty to choose on the AI Providers page. |
| `testSummary.prompt` | Empty uses the built-in report instructions. Rules keeping the model to the run's data, and the exact result tables, are always added. |
| `testSummary.maxConcurrentRuns` | `3`; reports written at once. |
| `testSummary.maxInputChars` / `maxAiCalls` | `100000` characters per model request / `12` requests per report. Larger runs are read in parts. |
| `testSummary.requestTimeoutSeconds` | `300`; time allowed for one model answer. |

Job settings live under `networkScan.worker`, `deviceInfo.research`, and
`reboot.worker`. Each supports `namespace` (empty uses the release namespace),
`createNamespace` (default `false`), and additional `imagePullSecrets`. Registry
Secrets must exist in each namespace that uses them.

| Job setting | Network Scan | Device Info | Reboot |
|---|---|---|---|
| `ttlSecondsAfterFinished` — completed job retention | `300` | `900` | `600` |
| `activeDeadlineSeconds` — maximum job runtime | `1800` | `1800` | `900` |

The `allowedEgressCidrs` setting is reserved and currently has no effect.

SSH reboot streams connection, command, outage, and recovery progress into the
run dialog. It watches the resolved SSH host and port, requires two consecutive
successful probes after observing an outage, and fails clearly if SSH never
goes offline within 60 seconds.

### MCP server

| Value | Default / options |
|---|---|
| `mcp.enabled` / `replicaCount` | `false` / `1`. |
| `mcp.image.*` / `resources` | Image selection, pull policy, and CPU/memory requests and limits. |
| `mcp.service.port` / `mountPath` | `8003` / `/mcp`; exposed through an internal ClusterIP service. |
| `mcp.authToken.existingSecret` / `key` | `""` / `auth-token`; optional Secret requiring a bearer token from MCP clients. Without it, callers inside the cluster do not authenticate. |
| `mcp.defaultLimit` / `maxLimit` | `50` / `500` records; maximum limit may be set up to `1000`. |
| `mcp.maxResponseBytes` | `131072`; response size limit in bytes. |

The chart configures read-only access from MCP to the backend automatically.
External MCP clients need a separately configured ingress or gateway.

## API client libraries

- [Python client](src/python-client/README.md): queries, device checkout, and test result recording.
- [Go client](src/go-client/README.md): API-token queries for inventory, software, vendor claims, tests, schemas, and device actions.

### TestBench Go command

Build the CLI into `bin/` from the repository root, then configure your API token:

```bash
mkdir -p bin
go -C src/go-client build -o ../../bin/testbench ./cmd/testbench
export TB_API_URL=https://testbench.example.com
export TB_API_KEY=tb_your_prefix_your_secret
./bin/testbench --devices
./bin/testbench --devices --type router
./bin/testbench --devices --filter 'make=MikroTik'
./bin/testbench --software --search curl --latest
./bin/testbench --vendor-devices --search 'ISR 4331'
./bin/testbench --tests --for-device dev-0042 --outcome fail
./bin/testbench --help
```

If the binary is already in `bin/`, skip the build. Mint a token under
**Profile → API keys**. To run `testbench` without a relative path, run
`export PATH="$PWD/bin:$PATH"` from the repository root.
See the [Go CLI guide](src/go-client/README.md#command-line-executable) for
column discovery, JSON/CSV output, sorting, limits, and permanent PATH setup.

## License

TestBench is copyright 2026 ChainFire Labs and released under the
[MIT License](LICENSE). Third-party dependencies and container components keep
their own licenses; see [Third-party software](THIRD_PARTY_NOTICES.md).
