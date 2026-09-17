# Release notes

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
