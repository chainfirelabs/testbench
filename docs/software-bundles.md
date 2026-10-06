# Software suites and components

A software version can optionally define named components. Components belong to
that specific suite/version; they are not standalone Software records and do not
appear separately in the software catalogue. For example, Microsoft Outlook and
Microsoft Word can exist only beneath Microsoft 365 `2026.1`.

In the UI, use **Add component** on a Software create or edit form. The API uses
the existing `bundle_components` property for compatibility:

```json
[
  {"name": "Microsoft Outlook", "version": "16.2"},
  {"name": "Microsoft Word", "version": "16.4"}
]
```

The standard Software CSV template contains only ordinary software fields.
Components are normally added with **New component** or **Edit** in the UI. The
software import API still accepts a manually added `bundle_components` column
for bulk migrations; do not add separate Software rows for the components:

```csv
name,version,bundle_components
Microsoft 365,2026.1,"[{""name"":""Microsoft Outlook"",""version"":""16.2""},{""name"":""Microsoft Word"",""version"":""16.4""}]"
```

Tests use one row per suite/component/device result. `software_name` and
`software_version` always identify the parent suite. `component_name` and
`component_version` optionally identify the component tested beneath it:

```csv
device_unique_id,software_name,software_version,component_name,component_version,outcome,tag,run_at,notes
0001,Microsoft 365,2026.1,Microsoft Outlook,16.2,pass,acceptance,2026-09-17,Mail test
0001,Microsoft 365,2026.1,Microsoft Word,16.4,fail,acceptance,2026-09-17,Document test
```

A test import reuses a matching component under the selected suite, or creates
it there if it does not exist. Both results remain tests of Microsoft 365 and
appear only on Microsoft 365's **Tested Devices** view, separated by component.

## Component-specific vendor support

A vendor device claim still belongs to one software version and has a general
`support_status`. It can also carry optional support statuses for individual
component versions. The same claimed hardware may therefore be supported by
one component version and unsupported by another. A claim with no component
links works exactly as before; a component without an override uses the general
claim status.

On the software version's **Vendor Claims** tab, or in **Vendor Claims** across
all software, open a claim and choose the component versions whose support
differs. Each selected version has its own status. The API uses
`component_support`:

```json
{
  "make": "Acme",
  "model": "R1",
  "support_status": "supported",
  "component_support": [
    {"component_name": "Microsoft Outlook", "component_version": "16.2", "support_status": "unsupported"}
  ]
}
```

An API write may use `component_id` instead of name and version. The component
must belong to the claim's software version. Exports include names and versions
so CSV and JSON imports can restore the links on another installation.
