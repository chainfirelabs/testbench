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
  canWrite: boolean,
): string | null {
  if (!canWrite) return 'Unavailable: write permission is required'
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
