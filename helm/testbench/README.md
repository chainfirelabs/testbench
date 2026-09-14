# TestBench Helm chart

This is the supported installation path for TestBench. It deploys the frontend
and backend, can optionally deploy the read-only MCP server and plugins, and
requires an existing PostgreSQL database and application login. PostgreSQL is
not included, and the chart does not create the database or login.

Create Secrets in the release namespace containing PostgreSQL `username` and
`password`, the normal `TB_*` application secrets, and (when plugins are
enabled) `shared-secret`:

```bash
kubectl create namespace testbench --dry-run=client -o yaml | kubectl apply -f -

kubectl --namespace testbench create secret generic testbench-application \
  --from-literal=TB_JWT_SECRET='replace-with-a-long-random-value'
```

Device automation passwords are stored as plaintext in PostgreSQL; protect
database and backup access. User-login passwords remain one-way bcrypt hashes.

```bash
kubectl --namespace testbench create secret generic testbench-database \
  --from-literal=username='testbench' \
  --from-literal=password='replace-with-the-postgres-password'

helm upgrade --install testbench ./helm/testbench \
  --namespace testbench --create-namespace \
  --set database.host=postgres.example.com \
  --set database.name=testbench \
  --set database.credentials.existingSecret=testbench-database \
  --set applicationSecret.existingSecret=testbench-application \
  --set config.frontendUrl=https://testbench.example.com \
  --set config.corsOrigins=https://testbench.example.com
```

`database.port` defaults to `5432` and `database.sslmode` defaults to
`require`. Use `verify-ca` or `verify-full` when the PostgreSQL trust material
is available; reserve `disable` for an isolated local environment such as the
disposable Minikube harness.

The database named by `database.name` and the login stored in
`database.credentials.existingSecret` must exist before installation. The login
must be able to connect to that database and create or alter application schema
objects. The pre-install/pre-upgrade database-schema Job uses that same
application login to create the tables on first install and apply schema updates
on later upgrades.

Before schema setup, an init container retries the database connection for up
to `database.waitTimeoutSeconds` (default `120`) at
`database.waitIntervalSeconds` intervals (default `5`). This accommodates a
database service that is still starting. A missing database, invalid login, or
unreachable service still fails after the bounded timeout; the chart never
creates the database or application login.

### Private container registry

Application images use non-root distroless runtimes. The frontend container
listens on 8080; its Service keeps the configured service port. Custom frontend
container port mappings/templates must use 8080. Runtime images have no shell;
use `/usr/bin/python3 -m uvicorn` or `/usr/bin/python3 -m alembic` for backend
command overrides. See [runtime details](../../docs/container-publishing.md#distroless-runtime-images).

Set `global.imageRegistry` once to choose the default registry for chart images,
including MCP, database-schema Jobs, and plugin worker/research Jobs:

```yaml
global:
  imageRegistry: ghcr.io
  imagePullPolicy: IfNotPresent
```

Override it for an internal registry with
`--set global.imageRegistry=registry.internal.example:5000`.
The default is `ghcr.io`. Use a host with an optional port/path,
without an `https://` scheme; a trailing slash is accepted. Each component's
`image.repository` (and `researchImage.repository`) is a relative path:
for example, `chainfirelabs/testbench/backend` becomes
`ghcr.io/chainfirelabs/testbench/backend:1.8.8`. Tags and digest overrides
continue to work as before.

`global.imagePullPolicy` applies to all application containers, schema Jobs,
plugin controllers, and dynamically created plugin worker/research Jobs. Its
default is `IfNotPresent`. Set it to `Always` when tags may be republished so
every node resolves the tag again when a Pod starts:

```yaml
global:
  imagePullPolicy: Always
```

Each `image` and `researchImage` may set `pullPolicy` to override the global
value for that image. Leave it empty to inherit the global policy. This includes
the Reboot research image.

Every `image` and `researchImage` accepts an optional `registry` override.
Omit it to inherit `global.imageRegistry`; set it to another registry to replace
the global prefix, or set `registry: ""` to use `repository` exactly as written.
This also applies to images passed to dynamically created plugin worker Jobs
and the backend image used by database-schema Jobs.

For example, keep TestBench images on GHCR and fetch the browser agent from
your own registry:

```yaml
global:
  imageRegistry: ghcr.io
plugins:
  deviceInfo:
    researchImage:
      registry: registry.example.com
      repository: my-team/oh-my-pi
      tag: latest
  reboot:
    researchImage:
      registry: ""
      repository: registry.example.com/my-team/oh-my-pi
      tag: latest
```

Both research images resolve to `registry.example.com/my-team/oh-my-pi:latest`.
Registry overrides do not configure authentication; supply image pull Secrets
for private registries as described below.

When upgrading existing values containing fully qualified repositories, remove
the registry prefix from those repositories, or set `global.imageRegistry: ""`
to use them as written. An empty registry also supports locally loaded images.

For a registry that requires authentication, create a Kubernetes
`docker-registry` Secret in the release namespace:

```bash
kubectl --namespace testbench create secret docker-registry registry-credentials \
  --docker-server=registry.example.com \
  --docker-username="$REGISTRY_USERNAME" \
  --docker-password="$REGISTRY_PASSWORD"
```

Reference the Secret globally so it is attached to frontend, backend,
database-schema, MCP, and plugin Pods:

```yaml
global:
  imageRegistry: registry.example.com
  imagePullSecrets:
    - name: registry-credentials

frontend:
  image:
    repository: testbench/frontend
backend:
  image:
    repository: testbench/backend
```

The chart references existing Secrets and does not store registry credentials in
Helm values. When a plugin worker/research namespace differs from the release
namespace, create the same Secret in that namespace. Component-specific worker
Secret names can additionally be set with
`plugins.networkScan.worker.imagePullSecrets`,
`plugins.deviceInfo.research.imagePullSecrets`, and
`plugins.reboot.worker.imagePullSecrets`.

## MCP server

The MCP server is disabled by default. It is deployed as an internal ClusterIP
Service and talks directly to this Helm release's backend Service at
`http://<release>-testbench-backend:8000/api/v1`; there is no external API-base
setting in the chart.

Enable it as part of the initial install or any upgrade:

```bash
helm upgrade --install testbench ./helm/testbench \
  --namespace testbench --reuse-values \
  --set mcp.enabled=true
```

Helm generates an internal credential, preserves it across upgrades, and mounts
it into both the backend and MCP Pods. The backend maps it to a fixed readonly
service identity without requiring a database user or API-key row, which makes
first-install bootstrapping automatic. This identity cannot perform writes,
search the admin-only audit log, or manage credentials.

The MCP endpoint is available inside the cluster at
`http://<release>-testbench-mcp:8003/mcp`. By default callers on the cluster
network do not authenticate to that endpoint. To require a caller bearer token,
add an `auth-token` key to a Secret and set
`mcp.authToken.existingSecret`; expose the Service through your own Ingress or
gateway if clients outside the cluster need access.

All plugins are disabled by default, in two independent senses. A plugin is not
deployed unless its `plugins.*.enabled` value turns it on — and a deployed
plugin is still unavailable to every device type until an administrator enables
it for that type under **Schema → Plugins**, or a `DeviceSchema` document
does. Both are deliberate: the second is what lets Reboot be enabled for routers
and remain impossible to invoke for a phone. See
[device-schema.md](../../docs/device-schema.md).

Enable network scanning with
`plugins.networkScan.enabled=true`. Enable device research with
`plugins.deviceInfo.enabled=true`, configure its OpenAI-compatible URL/model,
and create its AI Secret in the configured research namespace (the TestBench
release namespace by default). Plugin worker Pods run in that same namespace by
default; set the plugin's worker/research `namespace` value to opt into
isolation. LiteLLM proxies use this same interface. The Info icon asks for
confirmation before it starts an agent. See [values.yaml](values.yaml) for
plugin configuration options.

Enable reboot actions with `plugins.reboot.enabled=true`. Each device must have
its own `username` and `password`; there is no global SSH credential fallback.
Create `plugins.reboot.ai.existingSecret` in the worker namespace. Configure
`apiKeySecretKey` with the Secret key containing the bearer token; the chart
exposes only that value to the research image as `OPENAI_API_KEY`.

Set `plugins.reboot.method` to `ai` (default) or `ssh`, and
`plugins.reboot.sshCommand` to the global SSH command (default `reboot`). The
global SSH port is `plugins.reboot.sshPort` (default `22`). Each device type,
matching rule, or individual device can override `method`, `ssh_command`, and
`ssh_port` in its reboot plugin
configuration through Device Schema → Plugins or YAML `config`. Omitted/null
values inherit the globals. Vendor matching is no longer used; on upgrade,
explicitly select SSH for types that previously relied on MikroTik matching.
The SSH worker streams connection, command, outage, and recovery progress. It
watches the resolved SSH endpoint, requires two consecutive successful probes
after observing an outage, and reports an explicit failure if SSH never goes
offline within 60 seconds. Browser reboot verifies the device is reachable
again before reporting success. The action only appears for online devices with
a configured WAN or LAN scan-address role.

Device Info uses `plugins.deviceInfo.httpPort` and `httpsPort` as its global
web-interface ports (defaults `80` and `443`). The corresponding plugin
configuration keys are `http_port` and `https_port`, with the same type, rule,
and device override precedence. The agent tries HTTPS followed by HTTP.

## Plugin prompt variables

`plugins.deviceInfo.prompt` and `plugins.reboot.prompt` support exactly two
literal substitutions:

| Placeholder | Runtime value |
|---|---|
| `{device_url}` | The first web-interface candidate built from `scan_address_lan`, falling back to `scan_address_wan`. Device Info also appends its full ordered HTTPS/HTTP candidate list. Explicit paths and queries are preserved. |
| `{device_json}` | A JSON snapshot of the device described below. |

There is no generic `{field_name}` expansion. Unknown brace expressions remain
unchanged in the prompt.

`{device_json}` includes the device ID, its `unique_id` and device type, online
state, `_scan_addresses`, and `_plugin_roles` for the semantic roles the invoked
action declared. An action that sets `include_document: non_sensitive` — the
research agent does, because it identifies hardware from its inventory record —
also receives that device type's visible, non-sensitive field values. Sensitive
values reach a plugin only through a role the action asked for, and the
`username` and `password` keys are removed from the top level, from the document
and from `_plugin_roles` before this JSON is rendered.

The controller separately appends the decrypted per-device username/password
as an authentication instruction. It also appends a mandatory output contract;
Info additionally appends the last successful discovery recipe when one exists.
Therefore the configured prompt describes the task but does not need to include
credentials, result JSON formatting, retry steps, or recipe reuse. For Reboot,
the prompt is used only when the resolved method is `ai`; the `ssh` method uses
the Paramiko worker directly.

### Device-info ACP worker

Each Info Job runs a small TestBench adapter against the research image's
`omp acp` server using ACP v1 over stdio. The adapter starts OMP with only its
`browser` tool available, selects the discovery or repeat model through OMP's
default model role, and emits normalized tool, assistant-output, usage, result,
completion, and cancellation events. The controller turns those events into the
live output shown in the plugin dialog.

The final `session/prompt` response is the authoritative agent completion
boundary. When a valid `TESTBENCH_RESULT_JSON` payload arrives earlier, the
controller can save it immediately and terminate the Job rather than waiting
for unrelated agent cleanup. Stopping the Job sends SIGTERM to the adapter; it
first sends ACP `session/cancel`, with Kubernetes termination remaining the
bounded fallback. Unexpected interactive permission requests are denied.

Consequently, `plugins.deviceInfo.researchImage` must contain Python 3,
`/usr/local/bin/entrypoint.sh`, and an OMP release implementing ACP v1. The
adapter itself is supplied by the controller in the per-run Secret, so the OMP
image does not need to be rebuilt with TestBench code.

AI Reboot Jobs use the same ACP event and cancellation model with a
reboot-specific result contract. SSH-configured devices use the direct Paramiko
worker. Successful device-info and reboot steps are stored as
versioned device artifacts and can be viewed, edited, or completely cleared
from the device detail page's **Plugin Steps** tab. Clearing an artifact removes
all of its versions, so the next plugin run receives no saved recipe.

The single `schema` section configures the Device, Software, and Test catalogs
on first start. Protected identity and relationship fields are automatic;
additional Software/Test fields and the Device catalog are managed from the
**Schema** admin area after the default one-time bootstrap.

Predefined `wan_mac` and `lan_mac` carry the `discovery_wan_mac` and
`discovery_lan_mac` semantic roles. Any field may
declare a role when an installation needs a different mapping; plugins resolve
roles rather than hard-coded column names, so a field called `mgmt_ip` with the
`scan_address_wan` role is the address a scan reaches.

```yaml
schema:
  software:
    optionalFields: [version]
    additionalFields:
      - {key: license_owner, label: License Owner, type: text}
  tests:
    optionalFields: [tag, run_at, notes]
    additionalFields:
      - {key: lab, label: Lab, type: select, options: [East, West]}
  devices:
    source: configMap        # opt in to external bootstrap; default is database
    reconciliation: bootstrap  # bootstrap | merge | authoritative
    existingConfigMap: testbench-device-schema # required for configMap source
    key: device-schema.yaml
```

With `source: configMap`, the document is mounted read-only at
`/etc/testbench/device-schema` and reconciled at start. A ConfigMap update
reaches the pod as a changed file rather than as an event, so
`POST /api/v1/device-schema/reconcile` re-reads it without a restart, and
`GET /api/v1/device-schema/reconciliation` reports what the last run did.
The chart does not create this ConfigMap. Apply and customize
[`examples/device-schema-configmap.yaml`](examples/device-schema-configmap.yaml)
before installing the release. It bootstraps Routers, Mobile Phones, and
Servers, then hands them to the GUI. The default, `source: database`, starts with
no YAML-provided device types and does not require an external ConfigMap.

To move an existing deployment's configuration, choose **Download bootstrap
YAML** on its Schema page, apply that ConfigMap to the destination
namespace, and set `existingConfigMap: testbench-device-schema` there.

The same Schema page can choose **Import bootstrap YAML** and accept either the
downloaded ConfigMap or its raw `DeviceSchema` document. A preview lists the
proposed additions. Applying it creates only missing fields, device types,
layouts, and plugin assignments; existing configuration is never changed or
deleted.
