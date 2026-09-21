# Schema: devices, software, and tests

The **Schema** admin area also manages additional Software and Test columns.
Those entities each have one layout, while Devices additionally support global
assignments, per-device-type layouts, and plugin policy. Test relationship
fields remain protected so a schema edit cannot detach a test from its device
or software record.

What a device *is* in a TestBench installation — which fields it carries, which
kinds of device exist, and which plugins may act on each of them — is
configuration, not code. Administrators change it from **Schema → Device
Layouts** in the navigation, or declare it in a versioned YAML document.

All devices stay in one table. A device's structural identity is relational —
`unique_id`, its device type, who has it checked out, the timestamps — and
everything else lives in one JSONB document per device.

## The pieces

**Field definitions** are reusable. A field is defined once, with its type,
choices, validation rules, whether it is sensitive, whether it is indexed or
unique, and the semantic *role* plugins ask for. Defining a field does not put
it anywhere. A fresh database contains the core inventory catalog only.
Plugin-owned definitions—network addresses and scan state, credentials,
discovered firmware, hardware and MAC addresses—do not exist until a plugin
that declares them is enabled. An administrator may still define an equivalent
field first; enablement reuses it by role or key. IMEI and Architecture are
also on-demand rather than part of the fresh core catalog; administrators add
them only when their inventory needs them.

**Assignments** put a field somewhere. An assignment with no device type is
*global* and is inherited by every type, including types created later. An
assignment on a device type either adds a field only that type has, or
overrides properties of the global one — making serial number required for
servers, say, without touching any other type.

**Device types** are the categories: Routers, Mobile Phones, Servers. Each gets
its own inventory URL and its own columns. A type holds identity and
presentation only; its fields and plugins are the two resources above.

**Plugin assignments** are an allowlist. An installed plugin is unavailable to
a device type until a row says otherwise, and the API repeats that check on
every invocation.

The effective schema for a type is resolved in this order:

```
protected system fields
  + global field assignments
  + type-specific field assignments
  + type-specific overrides
  - explicit exclusions, where policy allows them
```

Every reader — the grid, the forms, imports, exports, search, plugin
availability and every write path — resolves through the same service. There is
no second implementation of the merge to disagree with the first.

## The admin screens

**Device Layouts** is where the work happens. The global set and each device
type are entries in one list, because they are the same job — deciding what a
page shows — and putting them side by side is what makes the inheritance
visible. The list shows only the fields the open page actually carries, in the
order it will show them; drag to reorder, and **+ Add field** offers the
definitions the page does not have yet, including a way to create one without
leaving the page you are building.

Opening a field shows what this page does with it: visible, included in device
lists, required, editable, and what it is called here. Turning off **Device
list** removes the column from that inventory grid while keeping the field on
individual device details, in edit forms, and available to plugins. Turning
off **Visible** retains the stronger existing behavior and hides the field
altogether. On a device type each state says whether it is inherited or
overridden, and an override can be handed back to inheritance in one click. A
type sends only what it has something of its own to say about, so everything
else keeps following the global set — including fields added globally later.

A device type's own identity — its display name, description, order in the
Devices menu, and whether it is enabled — is behind **Edit type** on that
type's layout. The display name is presentation and is safe to change at any
time; the key beside it is shown read-only, because that is what URLs, imports
and plugins address. Deleting the type is the quiet link under the heading, and
is refused while devices are assigned to it.

An existing device can be moved between types from **Edit** on its detail page.
Selecting a type reloads that type's editable fields and validation before the
device is saved, so values can be reviewed against the destination schema.

**Device Fields** is the catalog: every definition, its type, choices, rules,
the role plugins ask for, and where it is in use. It is where a field is
defined, disabled or deleted; placing one is Layouts' job.

**Plugins** is the per-type allowlist described above. **Software** and
**Tests** each have one layout, defined and placed in a single table.

Ordering a device type's page pins that order by giving each of its fields an
assignment. Only the position is pinned — visibility, requirement and
editability stay inherited — so arranging a page does not quietly freeze
everything else about it.

## Misc data

`misc_data` is not a field. It is the part of a device's document that its
type's page does not account for: a column from an import that was never
defined, or a value whose field has since been taken off the type. Everything
the layout defines is shown in its own field and is not repeated there —
including a field that is on the layout but hidden, because routing a hidden
value into a spillover bucket would undo the hiding, and for a sensitive field
would leak it.

Every read path resolves this the same way, from the published schema. Writes
still accept `misc_data` as a bucket of extra keys and merge it into the
document, which is how older clients and the legacy import columns speak.

Exports carry it as a column, because it is the one part of a device no other
column can: a `type` export includes it when the page shows it, an `all` export
always does, and `columns=data` carries the whole document regardless. An
import reads the column back, so an exported file restores the values with it.
Import *templates* leave it out — a device being created from a blank template
has no unexplained values yet.

Each stray key gets a column of its own rather than one column of JSON, which
is what makes an export readable in a spreadsheet. The columns are the union
across the exported devices, sorted, so a device without a given key leaves the
cell blank and the same fleet exports the same way twice; in JSON each device
simply carries its own keys flat. It round-trips, because a column an
installation does not define lands in the document on import — which is where
these values came from.

`?expand_misc=false` returns the single `misc_data` column instead. That is for
a script reading fixed headers: the column set then follows the schema, which
is knowable, rather than whatever keys the exported devices happen to carry.

A stray key that collides with the request envelope — `id`, `created_at`,
`device_type` and the rest — cannot be given a column, because an import would
read it back as part of the envelope instead of as a value. Those stay in a
residual `misc_data` column, which appears only if some device has one.

## Stable keys

A field key and a device type key are immutable and are what URLs, imports,
exports, the API, the Python client, MCP and plugin contracts use. Labels are
presentation and can be changed at any time without breaking anything.

Renaming "WAN IP" to "Management address" changes what people read. Renaming
the key would change what every integration is addressing, so it is not
offered.

## Protected system fields

Some fields the application itself depends on: `unique_id`, `status`, the scan
columns, and the checkout columns. They cannot be deleted, and their key, type
and semantic role cannot be changed. Their labels, order and — except for
`unique_id`, which every layout needs — their visibility are still yours.

## Removing a field never deletes data

Hiding a field, taking it off a type, or disabling the definition changes what
is *displayed and validated*. Stored values stay exactly where they are, and
reappear if the field is shown again. Deleting a definition outright is refused
while any device still holds a value for it; disable it instead.

The same is true of changing a device's type: the values belonging to its
previous type are kept, hidden rather than destroyed.

## Validation

Every write path — the API, bulk edits, CSV and JSON imports, plugin callbacks
and internal discovery updates — passes through one validator. It resolves the
device's type, coerces values into their canonical form, and enforces required
fields, choices, format, range and uniqueness.

A field definition's `validation` document currently understands:

| Rule | Applies to | Meaning |
|---|---|---|
| `min` / `max` | number | Inclusive bounds |
| `min_length` / `max_length` | text | Length bounds |
| `pattern` | text | A regular expression the whole value must match |

Values whose field is not in the catalog are preserved rather than refused, so
a field removed from a layout keeps its data. Set
`TB_DEVICE_SCHEMA_REJECT_UNKNOWN_FIELDS=true` to refuse them instead.

## Indexes

A field marked *indexed* or *unique* gets a managed PostgreSQL expression
index, typed to match the field. Index creation runs outside the request that
asked for it, and the desired and applied state of every managed index is
visible at `GET /device-schema/indexes` — including a failure, with its reason,
so it can be fixed and retried rather than disappearing into a log.

Publishing checks the stored devices first. A newly required field that two
hundred devices leave empty, a type change the stored values contradict, or a
uniqueness rule two devices already break are reported, and the publish is
refused, before any of it becomes the rule every save is measured against.

## Opening a web page

A field marked **Opens a web page** has its value offered as a link to that
address — on the device page the value itself is the link; in the inventory
grid a small icon sits beside it, because the cell is editable and a link
spanning it would mean the first click of a double-click opened a browser tab.

The link defaults to `http://` on the scheme's own port. A device serving only
https almost always still listens on 80 and redirects, so http lands correctly
either way, while https on a device with a self-signed certificate lands on a
browser warning even when the device is fine. A value that carries its own
scheme is honoured as written, so storing `https://10.0.0.1` in the field gets
exactly that.

### Choosing the scheme and port

Two settings sit under the checkbox, and they are the installation's default
*for that field*: **Link scheme** (`http` or `https`) and **Link port** (blank
for 80 or 443). Per field rather than per installation, because the right
answer differs between fields — a LAN address may be https on 8443 while a
vendor support page is plain http.

One device that answers somewhere else overrides both on its own page, under
**Links** in the edit form. Each linked field gets a scheme picker that starts
at *Inherit*, naming what the field does, and a port box that starts blank.
Setting one and not the other keeps the field's answer for the other, and
clearing both puts the device back on the field's default.

**The override is not stored in the address.** It cannot be: a field carrying
`scan_address_wan` or `scan_address_lan` has its value read raw by the network
scan and by the Reboot plugin, which hand it straight to a socket. A value of
`https://10.0.0.5:8443` is not an address, so the scan reports the device
offline and reboots stop working — while the link, which parses the value,
looks fine. Keeping the scheme and the port beside the address instead of
inside it is the whole reason these settings exist. A device's overrides live
in its own `link_overrides`, which is not a field and never reaches a plugin.

Values that are not web addresses are left as plain text rather than guessed
at, and only `http`/`https` results ever become links — a field holding
`javascript:` or `file:` cannot be turned into one. A port, a path and a bare
IPv6 address all work: `10.0.0.5:8443/ui`, `fd00::1`.

Fields carrying `scan_address_wan` or `scan_address_lan` get the option turned
on when they are created, since a management address is usually a web page.
That is only the default — any field can be opted in, including one that is not
an address at all (a vendor's support page), and any field can be opted out.

In a DeviceSchema document the three settings are `opensWebPage`, `linkScheme`
and `linkPort`, and a field on the defaults carries none of them. Per-device
overrides are device data, not schema, and so are not in the document at all.

## Semantic roles

Plugins ask for meaning, not for one installation's field names. A field
carrying the `scan_address_wan` role is the address a scan reaches, whatever it
is called; `device_username` and `device_password` are what an automation
plugin authenticates with.

| Role | What it means |
|---|---|
| `identifier` | The device's durable name |
| `status` | Workflow state |
| `scan_address_wan` / `scan_address_lan` | An address the device answers on |
| `scan_state` / `last_seen` | Written by scanning |
| `device_username` / `device_password` | Credentials automation plugins use |
| `discovery_firmware` / `discovery_hardware` | Firmware and hardware written by the research agent |
| `discovery_lan_mac` / `discovery_wan_mac` | Interface-specific MAC addresses written by the research agent |
| `checkout_started` / `checkout_due` / `checkout_purpose` | The checkout workflow |

Device Info treats its four discovery roles as optional outputs. A device type
needs at least one visible hardware, firmware, LAN MAC, or WAN MAC role to use
the action; it does not need to define all four.

## Plugin policy

Deny by default. A plugin is unavailable to a device type until it is enabled
for that type, and a device with no type gets no plugins at all.

`GET /devices/{id}/actions` returns each action with `available` and, when it is
not, the reason. The frontend renders exactly that and decides nothing itself,
because whether Reboot applies to a phone is a question about the
installation's policy rather than about the row.

Availability is the intersection of: installed and healthy plugins, the device
type's allowlist, the actions that plugin offers, the semantic roles the type's
schema provides, the values that device carries, and what the caller may do.

Every invocation repeats the whole check. A direct API request to reboot a
phone is refused with a message naming the device type, even though no
interface would have sent it. A mixed multi-device selection is refused
outright and every ineligible device is named — processing the eligible half
silently would leave somebody who selected forty devices with no idea which
ones actually rebooted.

A manifest may declare `risk: disruptive` and `allow_global_assignment: false`,
which is how Reboot can be enabled for Routers and remain impossible to enable
for everything at once.

A plugin may also declare `recommended_fields`. Installing it through the Helm
release creates any missing field definitions and adds the fields to the global
layout, so every current and future device type inherits them. Discovery is
retried after startup, so enabling a plugin in a later Helm upgrade has the
same effect without a manual backend restart. The operation is idempotent and
never creates duplicates. Existing definitions are reused by semantic role
before key. Explicitly GUI-hidden or disabled fields remain untouched, and
removing a plugin never removes fields or stored device data.

Authoritative YAML schemas are not mutated by this behavior. Their plugin
status continues to report missing semantic roles; add the corresponding
fields to the YAML document and reconcile it instead.

Plugin configuration is resolved independently for each device in this order:
an explicit device override, the first matching ordered field rule, the device
type's configuration, and finally the plugin deployment's global defaults.
Rules compare a field from the type's effective schema using `equals`,
`contains`, or `starts_with`; comparisons are case-insensitive unless the rule
says otherwise. This permits a `make = MikroTik` rule to select SSH while the
rest of the Router type continues to use browser discovery. Device overrides
are edited on the device's **Plugin Steps** tab. Removing an override exposes
the matching rule or inherited type setting again.

Reboot supports `ssh_port` alongside `method` and `ssh_command`. Device Info
supports independent `http_port` and `https_port` settings and tries the
resulting HTTPS and HTTP URLs in that order. All three ports accept integers
from 1 through 65535; an omitted or null value inherits the next configuration
level.

## Imports, exports and templates

Imports accept a `device_type` column holding a stable type key, and validate
every row against that type's effective schema. `GET /devices/template` returns
the global columns; add `?device_type=router` for one type's own.

Exports carry the type key and take a `columns` option:

| `columns` | Shape |
|---|---|
| `type` (default) | The selected type’s effective columns; without `device_type`, the union of the fleet’s fields |
| `all` | The union of every type's fields, so a mixed fleet loses nothing |
| `data` | Structural columns plus the whole JSON document in one cell — lossless, and the only shape that survives a schema change between export and re-import |

CSV exports neutralize formula-like column headers as well as values. Re-import
restores the original miscellaneous field keys, including leading apostrophes.

Names containing slashes can be resolved with
`GET /devices/lookup/by-unique-id?unique_id=...` and
`GET /software/lookup/by-name?name=...`. Encode the query value; use the returned
UUID for later actions or updates. Other names can be percent-encoded as single
path components. The Python client and MCP resolver handle this automatically.

## Configuration by YAML

The database is the normal source of truth. An installation can instead declare
its schema in a versioned document, mounted as a ConfigMap:

```yaml
apiVersion: testbench.chainfirelabs.com/v1
kind: DeviceSchema
metadata:
  name: default
spec:
  vendorDeviceFields:
    - {key: make, label: Make, type: text}
    - {key: model, label: Model, type: text}
    - {key: firmware_version, label: Firmware Version, type: text}
    - {key: hardware_version, label: Hardware Version, type: text}
    - {key: license_tier, label: License Tier, type: select, options: [standard, enterprise]}
  fields:
    - key: location
      label: Location
      type: text
    - key: imei
      label: IMEI
      type: text
      indexed: true
      unique: true
    - key: wan_ip
      label: WAN IP
      type: text
      role: scan_address_wan
    - key: carrier
      label: Carrier
      type: select
      options: [AT&T, T-Mobile, Verizon]

  globalFields:
    - key: location
      position: 10

  deviceTypes:
    - key: router
      label: Routers
      fields:
        - key: wan_ip
          required: true
      plugins:
        - id: network-scan
          enabled: true
        - id: device-reboot
          enabled: true
          config:
            timeoutSeconds: 60

    - key: mobile
      label: Mobile Phones
      fields:
        - key: imei
          required: true
        - key: carrier

    - key: server
      label: Servers
      overrides:
        - key: serial_number
          required: true
```

`fields` adds a type's own fields; `overrides` changes inherited global ones.
They are the same operation, named for what you are doing.

### Ownership modes

| Mode | Behaviour |
|---|---|
| `bootstrap` | Import once, into an installation with no GUI-owned configuration. The database and admin GUI own it afterwards, and later edits to the document do not silently change the live schema. **Default.** |
| `merge` | Add what the document introduces; never delete GUI-created configuration. A disagreement is reported as a conflict rather than resolved by guessing. Useful for rolling a new standard field out to existing installations. |
| `authoritative` | The document is the source of truth and is reconciled on every start. YAML-owned objects are read-only in the GUI, and an object dropped from the document is *disabled* rather than deleted unless the document sets `spec.prune: true`. |

Ownership is always explicit. Merge and authoritative objects are marked as
YAML-owned, so the admin API refuses to edit them and the GUI shows them as
owned elsewhere. Bootstrap objects transfer to GUI ownership after the initial
import.

Two separate questions are recorded about every object: **who may change it**,
and **where it came from**. A bootstrap hands over the first — the object
becomes editable — and never the second. So an object a document introduced
stays the document's to retire, even after a bootstrap made it editable, and
switching an installation from `bootstrap` to `authoritative` picks up where
the document left off rather than leaving its earlier objects stranded.

The limit on that is the point of recording provenance at all: an object
created in the admin GUI came from nobody's document, and no reconciliation
retires it. "The document is the source of truth" is a claim over the
document's own objects, not a licence to switch off somebody's work because a
file does not mention it.

An invalid document is rejected before a single row is written, and the last
published schema stays active. `GET /device-schema/reconciliation` reports what
the last run did — the generation seen, the revision produced, and any conflict
that stopped part of it applying — which is what a Flux operator needs.

A ConfigMap update reaches the pod as a changed file rather than as an event, so
`POST /device-schema/reconcile` re-reads it without a restart.

### Helm

```yaml
schema:
  software:
    optionalFields: [version]
    additionalFields: []
  tests:
    optionalFields: [component_name, component_version, tag, run_at, notes]
    additionalFields: []
  devices:
    source: configMap        # opt in to external bootstrap; default is database
    reconciliation: bootstrap
    existingConfigMap: testbench-device-schema # required for configMap source
    key: device-schema.yaml
```

The ConfigMap is managed outside the Helm release. Start with the chart's
[`examples/device-schema-configmap.yaml`](../helm/testbench/examples/device-schema-configmap.yaml),
customize the embedded `DeviceSchema`, and apply it before installing or
upgrading TestBench. In bootstrap mode the imported objects are handed to the
database and admin GUI after the first successful reconciliation.

An administrator can also choose **Download bootstrap YAML** on the Schema
page. The download is a complete external ConfigMap generated from the
live device field catalog, global and per-type layouts, semantic plugin roles,
and per-type plugin assignments. It contains configuration only—never device
values or credentials—and can be applied before installing another deployment.

**Import bootstrap YAML** accepts that downloaded ConfigMap or a raw
`DeviceSchema` document. The GUI previews every addition before applying it.
Import is strictly additive: fields, types, layout assignments, and plugin
assignments are created only when their stable identity does not already
exist. Existing objects are not changed, enabled, disabled, or deleted, and
the imported additions are owned by the GUI afterwards.

## API

```
GET    /device-fields                     the reusable definitions, with usage
POST   /device-fields
PATCH  /device-fields/{id}
DELETE /device-fields/{id}

GET    /device-types
POST   /device-types
PATCH  /device-types/{id}
DELETE /device-types/{id}

GET    /device-schema                     everything, for one page load
GET    /device-schema/global
PUT    /device-schema/global
GET    /device-schema/types/{type_key}
PUT    /device-schema/types/{type_id}
GET    /device-schema/types/{type_id}/plugins
PUT    /device-schema/types/{type_id}/plugins
POST   /device-schema/validate
POST   /device-schema/publish
POST   /device-schema/reconcile
POST   /device-schema/import?dry_run=true preview an additive YAML import
POST   /device-schema/import              apply an additive YAML import
GET    /device-schema/reconciliation
GET    /device-schema/indexes
GET    /device-schema/revisions
GET    /device-schema/export              downloadable bootstrap ConfigMap

GET    /devices?device_type=mobile
GET    /devices/{id}/actions
```

A `PUT` to a scope replaces that scope's whole assignment set: the editor shows
a layout and saves a layout. Reading a scope, changing it and putting it back is
the intended flow, and is what the admin GUI does.

Only administrators may change any of this. Read-only and tester roles can read
published schemas. Every change is audited with what the configuration was
before and what it became, and rejected plugin invocations are audited too.

## Device API shape

Writes take a structural envelope plus one document:

```json
{
  "unique_id": "phone-001",
  "device_type": "mobile",
  "data": {
    "make": "Samsung",
    "model": "Galaxy S26",
    "imei": "..."
  }
}
```

`device_type_id` is accepted in place of `device_type`. Responses carry `data`
and also flatten the fields that ship with the product, so existing clients
keep working. Flattened *input* is accepted too, which is what lets a CSV
column become a field value.

A `PATCH` is a partial write: fields it does not mention keep their values,
including fields that are not on the current layout at all. Sending `null`
clears a value; sending `""` clears it too, except on a sensitive field, where
an explicitly blank value is a configured credential (a username with no
password is an ordinary appliance) and is stored as written.

## Backup, restore and rollback

The schema is ordinary rows in the application database: `device_field_definitions`,
`device_field_assignments`, `device_types`, `device_type_plugins`,
`device_schema_revisions` and `device_field_indexes`. A normal `pg_dump` of the
TestBench database captures all of it along with the devices, and restoring that
dump restores the exact configuration the devices were written under.

`GET /device-schema/revisions` lists what has been published, by whom and when,
and each entry's `summary` says what changed. Rolling *back* a configuration
change is done by making the inverse change and publishing it — there is no
"revert to revision N" button, because the devices written in between are real
and a blind revert could invalidate them. The audit log records every schema
change with its before and after, which is what an inverse change is written
from.

`device_field_indexes` is desired state, not data: after a restore, the
application re-plans and re-applies the managed indexes at startup, so a dump
taken without them is not a problem.

Two things to know about restoring into a different environment:

- If that environment mounts a `DeviceSchema` document in `authoritative` mode,
  the document is reconciled over the restored rows on the next start. That is
  the point of the mode, but it means a restore is only faithful where the
  document matches.
- In `bootstrap` mode a restored database already carries configuration, so the
  document will not be imported over it.
