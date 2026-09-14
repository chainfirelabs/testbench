# Reboot plugin

This optional TestBench plugin contributes a power-icon row action. It is
packaged as its own controller/worker image and can be removed from `src/plugins`
without changing the core backend or frontend.

Global defaults are `plugins.reboot.method` (`ai` or `ssh`, default `ai`),
`plugins.reboot.sshCommand` (default `reboot`), and `plugins.reboot.sshPort`
(default `22`). Administrators can override them under
**Device Schema → Plugins → Device Reboot** for each type. An omitted/null
`method`, `ssh_command`, or `ssh_port` inherits its global default. SSH commands
must be nonempty and run without interactive prompts. Settings are captured per
run; changing a type does not change jobs already running.

Each type may also have ordered field rules. The first matching rule overrides
the type—for example, `Make equals MikroTik` can select SSH while other routers
continue to use AI browser discovery. An administrator can set a final,
highest-priority override on an individual device's **Plugin Steps** tab. The
resolution order is device, first matching rule, type, then global defaults.

DeviceSchema YAML supports the same settings in a type's plugin entry:

```yaml
plugins:
  - id: device-reboot
    enabled: true
    config:
      method: ssh
      ssh_command: "sudo -n reboot"
      ssh_port: 2222
```

Upgrade note: vendor matching (`vendorRole` / `mikrotikValues`) no longer selects
the worker. Set `method: ssh` for types that previously used SSH, or select SSH
as the global default. Global defaults do not enable reboot permissions.

SSH runs use a short-lived Paramiko worker Job. AI runs use the
configured browser-agent image through ACP v1. The ACP adapter exposes only the
browser tool, streams assistant and tool progress into the shared run dialog,
suppresses noisy generic session updates, and converts Pod termination into an
ACP cancellation. The SSH worker streams its own connection, command, outage,
and recovery progress. It watches the resolved SSH endpoint, must observe an
outage, and requires two consecutive successful probes before completing. If
SSH never goes offline, it fails explicitly after 60 seconds. AI reboot verifies
that the device is reachable again before it completes.

`plugins.reboot.captureImages` defaults to `false`, which instructs the browser
agent to use DOM, accessibility, and page text without screenshots or other
image captures. Set it to `true` only when the configured model supports image
input and visual inspection is useful. This is an agent instruction rather than
a browser sandbox restriction, so model and browser-tool behavior still matter.

Verified steps from either path are POSTed back to TestBench as a versioned
`reboot_recipe`. AI runs receive the latest recipe as a starting
point. Users with write access can edit or clear it from the device detail
page's **Plugin Steps** tab. New recipes use the same detailed step structure
as Device Info: each chronological step records its number, action, target,
outcome, and an empty `yielded_fields` array.

Both paths use each device's `username` and plaintext-stored `password`; there
is no global SSH credential. Restrict access to PostgreSQL and its backups.

The manifest declares this action `disruptive` and sets
`allow_global_assignment: false`, so it can never be turned on for every device
type at once. Deploying the plugin does not make it available anywhere: an
administrator enables it per device type under **Device Schema → Plugins**, and
the backend repeats that check on every invocation — a direct API request to
reboot a device of an unauthorised type is refused, naming the type. A mixed
multi-device selection is refused outright, with every ineligible device named.

The worker receives only what the action declared: the `device_username`,
`device_password` and scan-address roles, plus the device's identity. It is not
sent the rest of the device document.

See `../../../helm/testbench/README.md` and the `plugins.reboot` values in
`../../../helm/testbench/values.yaml` for deployment configuration.
