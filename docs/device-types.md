# Device types

Device types give each hardware category its own inventory URL, columns, form
and required-field rules while all devices stay in the same database table.
The Helm chart bootstraps Routers, Mobile Phones, and Servers on a fresh
installation and then hands them to the admin GUI. An administrator can change
or replace those categories, and non-Helm installations can start empty or
bootstrap their own versioned DeviceSchema document.

Types are one part of the [device schema](device-schema.md), which is where
fields, assignments and plugin policy are described in full. This page covers
the types themselves.

## Managing types

Administrators manage types from **Schema → Device Types**. Each type has
an immutable URL key, an editable label, an enabled state and an order.
Clicking Devices opens a submenu listing All Devices, every enabled type, and
Uncategorized; the mobile drawer shows the same hierarchy.

An enabled type gets a page at `/devices/type/<key>` — `/devices/type/router`,
`/devices/type/mobile`. Those pages share one implementation and one component:
they differ because their schemas differ. The `/` page shows all devices with
the global fields, and offers each type's own fields through the column picker.

A new type inherits every global field immediately, including fields added long
after it was created. It needs its own assignment rows only for fields it alone
carries, or to override something about an inherited one.

## Fields and rules

**Schema → Device Types** also edits a type's layout: which fields it
shows, in what order, which are required, and which are read-only. A type can
require a field the global set leaves optional — serial number for servers —
without affecting any other type.

Values in fields a type does not show remain stored. Hiding a column never
deletes device data, and neither does moving a device to another type: the
values belonging to its previous type are kept, hidden rather than destroyed.
Changing a device's type does validate the target type's required fields.

## Plugins

**Schema → Plugins** decides which installed plugins may act on each
type. Nothing is available until it is enabled, and the API repeats the check
on every invocation — so Reboot can be enabled for Routers and remain
impossible to invoke for a phone, including through a direct API call.

## Deleting and disabling

A type assigned to devices cannot be deleted; disable it instead, which takes
its page out of the navigation and leaves its devices addressable on the All
Devices page. Existing untyped devices remain available there and through the
`device_type=uncategorized` API filter.

## Imports, exports and the API

CSV and JSON imports accept a `device_type` column holding the stable type key,
and validate each row against that type's schema. Exports include the same key.
API create and update requests accept either `device_type` (the key) or
`device_type_id`.
