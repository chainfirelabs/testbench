import type { SchemaField } from './deviceSchema'
import type { PluginAction } from './pluginActions'

interface DevicePage<T> {
  items: T[]
  total: number
}

/** Stable, descriptive filenames for downloads from a device-schema scope. */
export function deviceDownloadFilename(
  typeKey: string | undefined,
  format: 'json' | 'csv',
  variant?: 'raw' | 'template',
): string {
  const scope = typeKey || 'all'
  return `${scope}-devices${variant ? `-${variant}` : ''}.${format}`
}

/**
 * Whether this field's value should be offered as a link to a web page.
 *
 * The administrator's choice, per field, from the schema editor — not a
 * property of the field's role. The scan-address fields are seeded with it on
 * because a management address is a web page often enough to be worth linking
 * by default, but any field can be opted in (a "Vendor Page" URL) and any
 * field can be opted out.
 */
export function isDeviceAddressField(field: Pick<SchemaField, 'opens_web_page'>): boolean {
  return field.opens_web_page === true
}

/** How one device opens one linked field, where it differs from the schema. */
export interface DeviceLinkOverride {
  scheme?: string | null
  port?: number | string | null
}

/** Every such override a device carries, keyed by field key. */
export type DeviceLinkOverrides = Record<string, DeviceLinkOverride>

type LinkField = Pick<SchemaField, 'key'> & Partial<Pick<SchemaField, 'link_scheme' | 'link_port'>>

/**
 * The scheme and port a field's link should use on this device.
 *
 * Two layers, most specific last: the field's own default from the schema,
 * then this device's override of it. A device that overrides only the port
 * keeps the field's scheme, and vice versa, because the two are configured
 * separately and an operator who set one did not thereby decide the other.
 */
export function deviceLinkTarget(
  field?: LinkField | null,
  overrides?: DeviceLinkOverrides | null,
): { scheme: string; port: string | null } {
  const scheme = field?.link_scheme === 'https' ? 'https' : 'http'
  const port = field?.link_port != null ? String(field.link_port) : null
  const override = field?.key ? overrides?.[field.key] : null
  if (!override) return { scheme, port }
  return {
    scheme: override.scheme === 'http' || override.scheme === 'https' ? override.scheme : scheme,
    port: override.port != null && override.port !== '' ? String(override.port) : port,
  }
}

/**
 * The web page an address field points at, or null when it does not point at one.
 *
 * Three sources, and the most specific wins. The value's own scheme and port
 * beat everything, because they are the most specific thing there is and an
 * operator typed them; then this device's override; then the field's default
 * from the schema; and failing all of those, `http://` on the scheme's own
 * port. Plain http is the floor because a device serving only https almost
 * always still listens on 80 and redirects, so http lands correctly either
 * way, while https against a self-signed certificate lands on a browser
 * warning even when the device is fine.
 *
 * The override exists because the value is *not* a safe place to say this. A
 * field carrying a scan-address role has its value read raw by the scanner and
 * by the reboot plugin, which hand it to `socket.create_connection` — a scheme
 * or a path stored in it takes the device offline. Scheme and port therefore
 * live beside the address rather than inside it, and the value stays an
 * address.
 *
 * Returns null rather than guessing for anything that is not a web address.
 * The value is free text an operator typed, so it is parsed rather than
 * concatenated: `new URL` rejects the malformed ones, and restricting the
 * result to http/https means a stored `javascript:` or `file:` value can never
 * become a link.
 */
export function deviceAddressUrl(
  value: unknown,
  field?: LinkField | null,
  overrides?: DeviceLinkOverrides | null,
): string | null {
  const raw = String(value ?? '').trim()
  if (!raw) return null
  const hasScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw)
  const target = deviceLinkTarget(field, overrides)
  try {
    const url = new URL(hasScheme ? raw : `${target.scheme}://${bracketBareIpv6(raw)}`)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    // Only when the value did not name one itself. `url.port` cannot answer
    // this — it is blank for a port that happens to be the scheme's default,
    // so `http://10.0.0.5:80` would read as portless and get overridden.
    if (target.port && !hasExplicitPort(raw)) url.port = target.port
    return url.href
  } catch {
    return null
  }
}

/** Whether the operator wrote a port into the value, as `host:8080`. */
function hasExplicitPort(raw: string): boolean {
  const host = raw.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '').split('/')[0]
  // Bracketed IPv6 keeps its own colons inside the brackets, so the only port
  // is the one after them; anything else has a port when it has exactly one
  // colon, which is the same rule `bracketBareIpv6` reads the other way.
  if (host.startsWith('[')) return /\]:\d+$/.test(host)
  return host.split(':').length === 2 && /:\d+$/.test(host)
}

/**
 * `::1` is a host; `http://::1` is not a URL. A bare IPv6 address has to be
 * bracketed before it can be one, and the giveaway is a second colon — one
 * colon is the `host:port` everything else uses.
 */
function bracketBareIpv6(raw: string): string {
  if (raw.startsWith('[') || raw.split(':').length < 3) return raw
  return `[${raw}]`
}

/** A fully visible field may still be intentionally absent from inventory grids. */
export function fieldAppearsInDeviceList(field: Pick<SchemaField, 'visible' | 'list_visible'>): boolean {
  return field.visible && field.list_visible !== false
}

/** Load the whole scope before handing it to the grid's local filters. */
export async function loadDevicePages<T>(
  fetchPage: (offset: number) => Promise<DevicePage<T>>,
  progress: (loaded: number, total: number) => void = () => {},
): Promise<T[]> {
  const rows: T[] = []
  while (true) {
    const page = await fetchPage(rows.length)
    rows.push(...page.items)
    progress(rows.length, page.total)
    if (rows.length >= page.total) return rows
    if (!page.items.length) throw new Error('The device list changed while loading. Please reload it.')
  }
}

/** Resolve each row against its own schema, including visibility overrides. */
export function deviceActionUnavailableReason(
  action: PluginAction,
  row: any,
  schemas: ReadonlyMap<string, SchemaField[]>,
  /** Whether the caller holds `devices.edit` — running a plugin writes to the device. */
  canEditDevices: boolean,
): string | null {
  // Worded as the API words it, so the tooltip and the 403 name the same thing.
  if (!canEditDevices) return 'Unavailable: permission to edit devices is required'
  if (action.device_types && !action.device_types.includes(row.device_type_key)) {
    const where = row.device_type_label || 'devices without a device type'
    return `Unavailable: ${action.label} is not enabled for ${where}`
  }
  const reason = action.unavailable_reasons_by_type?.[row.device_type_key] || action.unavailable_reason
  if (reason) return `Unavailable: ${reason}`
  const fields = schemas.get(row.device_type_key || '')
  if (!fields) return 'Unavailable: device schema is loading'
  const roleField = (role: string) => fields.find((field) => field.visible && field.role === role)
  const hasRole = (role: string) => {
    const field = roleField(role)
    if (!field) return false
    if (field.sensitive && field.storage === 'data') {
      return Object.prototype.hasOwnProperty.call(row?.data || {}, field.key)
    }
    const value = field.storage === 'data' ? row.data?.[field.key] : row[field.key]
    return value !== null && value !== undefined && value !== ''
  }
  const missing = (action.required_roles || []).filter((role) => !hasRole(role))
  if (missing.length) {
    return `Unavailable: missing ${missing.map((role) => roleField(role)?.label || role).join(', ')}`
  }
  const groups = [...(action.required_role_groups || [])]
  if (action.required_any_roles?.length) groups.push(action.required_any_roles)
  for (const group of groups) {
    if (!group.some(hasRole)) {
      return `Unavailable: add a value for ${group.map((role) => roleField(role)?.label || role).join(' or ')}`
    }
  }
  if (action.requires_online && row.data?.online_status !== true) {
    return 'Unavailable: this device is offline; run Scan first'
  }
  return null
}
