# Software, test, and vendor-device schema fields

> **Devices have moved on.** Device fields are configurable at runtime and can
> differ per device type — see [device-schema.md](device-schema.md). What
> follows describes the storage and validation contract for software and test
> fields. They are now editable at runtime from **Schema → Software** and
> **Schema → Tests**. The Helm chart seeds those two catalogs under `schema`;
> device and vendor-device declarations can live in the external DeviceSchema
> ConfigMap selected by `schema.devices`.

Vendor Devices have a shared field catalog under **Schema → Vendor Devices**.
Each software version can override which catalog fields are shown or required
from its **Vendor Devices → Customize fields** action. A new software version
inherits those overrides and can diverge afterward.

Additional vendor-device values are stored in `misc_data`, but appear as normal
columns in forms, CSV templates, imports, and exports. API and MCP searches scan
the complete custom document, and MCP results expose it as `custom_fields`.
Deleting a field definition leaves previously stored JSON values intact.

TestBench persists the logical columns for software and tests in
`entity_fields`. `TB_ENTITY_FIELDS_JSON` can seed that catalog on first start;
afterwards administrators add and remove additional JSON-backed fields from
the Schema page, reorder fields, and hide optional fields from the main Software
and Tests lists without removing their stored values or hiding them from detail
views. The database is the source of truth.

The protected fields are part of the application contract. Software identity
and the test links to a device and software record cannot be deleted, made
optional, or have their key, type, or storage changed. Only their presentation
label and description may be adjusted.

Only row IDs, test relationships, audit ownership and timestamps remain typed
database columns. Every user-facing device, software and test attribute—including
the optional fields known to the application—is stored in the row's `data`
JSONB document. Core predefined fields have catalog rows whose visibility
controls whether users see them in grids, forms, details, templates and exports.
Plugin-owned device fields are created globally on demand from the manifest of
a plugin installed through Helm and are not part of a plugin-free installation's
catalog.

In configuration, `optional_fields` makes a predefined application capability
visible with its known label, type or behavior. Omitted predefined fields stay
in the catalog, but remain hidden and are not required by create requests.
For example, selecting device `status` enables the standard status choices,
while omitting it leaves no Status column in the inventory.

The Helm values use friendlier names for the same Software/Test bootstrap
contract: `schema.software.optionalFields`,
`schema.software.additionalFields`, `schema.tests.optionalFields`, and
`schema.tests.additionalFields`. The backend environment variable retains its
snake-case JSON format as an internal deployment detail.

`serial_number` is a predefined optional field and indexed for lookup. IMEI and
Architecture are available as predefined templates but are absent from a fresh
catalog unless explicitly selected in bootstrap configuration or created by an
administrator. `wan_mac` and `lan_mac` are created when Device Info is first
enabled and carry its distinct discovery roles.

## Minimal example

Put the JSON on one line in `.env`:

```json
{
  "devices": {"optional_fields": ["location"], "custom_fields": []},
  "software": {"optional_fields": ["version"], "custom_fields": []},
  "tests": {"optional_fields": ["tag", "run_at", "notes"], "custom_fields": []}
}
```

Required system fields are added automatically and must not be listed in the
variable: `unique_id` for devices, `name` for software, and
`device_unique_id`, `software_name` and `outcome` for tests. They appear first,
followed by `optional_fields` and then `custom_fields`, in the order each list
provides.

If checkout purpose and due date are not both selected as predefined device fields,
`checked_out` is removed from the status choices so the UI cannot start a
checkout it has nowhere to describe.

## Custom fields

`optional_fields` selects predefined application capabilities by name.
`custom_fields` contains field objects that are stored in JSON:

```json
{
  "devices": {
    "optional_fields": ["location"],
    "custom_fields": [
      {"key": "asset_owner", "label": "Asset Owner", "field_type": "text"},
      {"key": "purchase_year", "label": "Purchase Year", "field_type": "number"}
    ]
  },
  "software": {"optional_fields": ["version"], "custom_fields": []},
  "tests": {
    "optional_fields": ["tag", "run_at", "notes"],
    "custom_fields": [
      {"key": "lab", "label": "Lab", "field_type": "select", "options": ["East", "West"]}
    ]
  }
}
```

An unknown name under `optional_fields` stops startup and explains that it
belongs under `custom_fields`. Likewise, putting a predefined or automatically
required field under `custom_fields` is rejected rather than creating ambiguity.

Supported types are `text`, `textarea`, `number`, `boolean`, `date`, `select`
and `json`. Custom fields may also set `required`, `description`, `options`,
`indexed` and `unique`.

Device `username` and `password` are created when Device Info or Reboot is
first enabled. A bootstrap configuration may still select them explicitly
under `optional_fields` when operators should enter per-device credentials.
Password is shown as plaintext while editing and is readable by authenticated API clients
and the web application. Enabled Info and Reboot plugin jobs receive it through
the internal invocation payload. PostgreSQL stores the device password as
plaintext, so database and backup access must be restricted. Application-user
passwords are separate and remain one-way bcrypt hashes. An explicitly blank
device password is distinct from an omitted password. This supports devices
that authenticate with a username and no password while still allowing the UI
to report a genuinely missing credential.

## Query indexes

Fields marked `indexed` receive a typed PostgreSQL expression index over their
JSON value when the catalog is initialized. `unique` creates a unique
expression index and implies the same lookup performance. Identifiers and
frequently filtered default fields are indexed automatically.

```json
{
  "key": "rack",
  "label": "Rack",
  "field_type": "text",
  "indexed": true
}
```

ISO date values are indexed as text because `YYYY-MM-DD` sorts chronologically
and PostgreSQL does not permit its DateStyle-dependent date cast in an
expression index. Number and boolean indexes use typed expressions.

CSV headers not present in the catalog are still preserved in the entity's JSON
object. They do not become visible logical columns automatically; this keeps a
one-off measurement from silently changing the installation's inventory model.

## Database initialization

The pre-production schema is represented by a single baseline migration. On
the first application startup, an empty field catalog uses
`TB_ENTITY_FIELDS_JSON` when it is set, or receives the core built-in catalog
with only required fields visible when it is not. Plugin-owned definitions are
absent until first enablement. Runtime catalog changes are
made through the authenticated schema API. Changes to the bootstrap environment
configuration do not overwrite an initialized installation.
