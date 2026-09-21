/**
 * The published device schema, as the pages render themselves from.
 *
 * Every device page — the fleet grid, a type's inventory page, the detail page,
 * the create and edit dialogs, the import template — is generated from what
 * this module fetches. There is no Router page component and no Mobile page
 * component: there is one implementation, and a schema that says what a router
 * or a phone looks like.
 *
 * Schemas are cached per device type and thrown away when the backend
 * publishes a new revision, which is what makes a schema change appear
 * everywhere without a reload.
 */
import { ref, type Ref } from 'vue'
import { api } from './api/client'
import type { FormField } from './components/FormModal.vue'

export interface SchemaField {
  key: string
  label: string
  type: 'text' | 'password' | 'textarea' | 'number' | 'boolean' | 'date' | 'select' | 'json'
  required: boolean
  visible: boolean
  /** Whether inventory grids include this column. */
  list_visible: boolean
  sensitive: boolean
  writable: boolean
  /** Where the value lives: the JSON document, a real column, or neither. */
  storage: 'data' | 'column' | 'derived' | 'virtual'
  options: string[]
  description?: string | null
  /** The semantic name a plugin asks for, independent of this field's key. */
  role?: string | null
  indexed: boolean
  unique: boolean
  /** The value is a web address, so the UI offers it as a link. */
  opens_web_page: boolean
  /** The scheme that link uses unless the device overrides it. */
  link_scheme: 'http' | 'https'
  /** The port it opens on, or null for the scheme's own. */
  link_port: number | null
  validation: Record<string, any>
  default?: any
  position: number
  /** Inherited from the global set, or added/overridden by this device type. */
  scope: 'global' | 'type'
  protected: boolean
  configuration_source: 'system' | 'gui' | 'yaml'
}

export interface DeviceTypeRef {
  id: string | null
  key: string
  label: string
  icon?: string | null
  description?: string | null
  configuration_source?: string
}

export interface DeviceSchema {
  revision: number
  device_type: DeviceTypeRef | null
  fields: SchemaField[]
  plugins: string[]
  allow_global_exclusions?: boolean
  assignments?: FieldAssignment[]
}

export interface FieldAssignment {
  id?: string
  field_key: string
  visible: boolean | null
  list_visible: boolean | null
  required: boolean | null
  writable: boolean | null
  position: number | null
  label_override: string | null
  description_override: string | null
  validation_override: Record<string, any> | null
  excluded: boolean
  configuration_source?: string
}

export interface FieldDefinition {
  id: string
  key: string
  label: string
  field_type: SchemaField['type']
  description: string | null
  options: string[]
  validation: Record<string, any>
  default_value: any
  sensitive: boolean
  indexed: boolean
  unique_value: boolean
  opens_web_page: boolean
  link_scheme: 'http' | 'https'
  link_port: number | null
  plugin_role: string | null
  protected_system_field: boolean
  enabled: boolean
  configuration_source: 'system' | 'gui' | 'yaml'
  storage: SchemaField['storage']
  usage: { global: boolean; device_types: string[]; devices_with_values: number }
}

/** The URL segment for devices that have no type at all. */
export const UNCATEGORIZED = 'uncategorized'

const cache = new Map<string, Promise<DeviceSchema>>()
let cachedRevision = -1

function path(typeKey?: string): string {
  return typeKey && typeKey !== '' ? `/device-schema/types/${encodeURIComponent(typeKey)}` : '/device-schema/global'
}

/**
 * The effective schema for one device type, or the global one with no type.
 *
 * A response carrying a newer revision empties the cache: the schema just
 * changed, so every other page's copy is stale too.
 */
export async function loadDeviceSchema(typeKey?: string): Promise<DeviceSchema> {
  const key = typeKey || ''
  const hit = cache.get(key)
  if (hit) return hit
  const request = api<DeviceSchema>(path(typeKey)).then((schema) => {
    if (schema.revision !== cachedRevision) {
      cachedRevision = schema.revision
      for (const other of [...cache.keys()]) if (other !== key) cache.delete(other)
    }
    return schema
  })
  cache.set(key, request)
  request.catch(() => cache.delete(key))
  return request
}

/** Called after any schema edit, and whenever a page wants a guaranteed re-read. */
export function invalidateDeviceSchemas(): void {
  cache.clear()
  cachedRevision = -1
}

export function useDeviceSchema(): {
  schema: Ref<DeviceSchema | null>
  fields: Ref<SchemaField[]>
  loadSchema: (typeKey?: string) => Promise<void>
} {
  const schema = ref<DeviceSchema | null>(null)
  const fields = ref<SchemaField[]>([])
  return {
    schema,
    fields,
    loadSchema: async (typeKey?: string) => {
      const loaded = await loadDeviceSchema(typeKey === UNCATEGORIZED ? undefined : typeKey)
      schema.value = loaded
      fields.value = loaded.fields
    },
  }
}

/* ---------- reading and writing one field on a device row ---------- */

/**
 * Devices carry their installation-defined values in `data` and their
 * structural ones as ordinary properties. Everything reads through here so no
 * caller has to remember which is which.
 */
export function fieldValue(row: any, field: SchemaField): any {
  if (!row) return undefined
  if (field.storage === 'data') return row.data?.[field.key]
  if (field.key === 'misc_data') return row.misc_data
  return row[field.key]
}

export function setFieldValue(row: any, field: SchemaField, value: any): void {
  if (field.storage === 'data') row.data = { ...(row.data || {}), [field.key]: value }
  else row[field.key] = value
}

/** Fields a person can type into: visible, writable, and a real value. */
export function editableFields(fields: SchemaField[]): SchemaField[] {
  return fields.filter(
    (field) => field.visible && field.writable && field.storage !== 'derived' && field.key !== 'misc_data',
  )
}

export function fieldByRole(fields: SchemaField[], role: string): SchemaField | undefined {
  return fields.find((field) => field.role === role)
}

/**
 * Turn a form's flat values into the request envelope the API documents.
 *
 * Structural keys travel at the top level and everything else goes into
 * `data`, so a field an installation invented is written exactly like one that
 * ships with the product.
 */
export function toDevicePayload(
  values: Record<string, any>,
  fields: SchemaField[],
): Record<string, any> {
  const payload: Record<string, any> = {}
  const data: Record<string, any> = { ...(values.data || {}) }
  if ('unique_id' in values) payload.unique_id = values.unique_id
  if ('device_type_id' in values) payload.device_type_id = values.device_type_id || null
  for (const field of fields) {
    if (field.storage !== 'data' || !(field.key in values)) continue
    const value = values[field.key]
    // An explicitly blank sensitive value is meaningful — many appliances use
    // a username with no password — so it is sent rather than dropped.
    if (field.sensitive && value === '') data[field.key] = ''
    else if (value === '' || value == null) data[field.key] = null
    else data[field.key] = value
  }
  if (values.misc_data && typeof values.misc_data === 'object') Object.assign(data, values.misc_data)
  payload.data = data
  return payload
}

/** The form field for one schema field, before a page adds its own behaviour. */
export function schemaFormField(field: SchemaField): FormField {
  const type: FormField['type'] =
    field.type === 'select' ? 'select'
    : field.type === 'textarea' ? 'textarea'
    : field.type === 'json' ? 'json'
    : field.type === 'date' ? 'date'
    : field.type === 'number' ? 'number'
    : field.type === 'boolean' ? 'boolean'
    : field.type === 'password' ? 'password'
    : 'text'
  return {
    key: field.key,
    label: field.label,
    type,
    required: field.required,
    disabled: !field.writable,
    options: (field.options || []).map((value) => ({ value, label: value })),
    hint: field.description || validationHint(field) || undefined,
  }
}

/** A one-line summary of a field's rules, so a form says them before a save does. */
export function validationHint(field: SchemaField): string {
  const rules = field.validation || {}
  const parts: string[] = []
  if (rules.min != null && rules.max != null) parts.push(`between ${rules.min} and ${rules.max}`)
  else if (rules.min != null) parts.push(`at least ${rules.min}`)
  else if (rules.max != null) parts.push(`at most ${rules.max}`)
  if (rules.min_length != null && rules.max_length != null) {
    parts.push(`${rules.min_length}–${rules.max_length} characters`)
  } else if (rules.max_length != null) parts.push(`up to ${rules.max_length} characters`)
  else if (rules.min_length != null) parts.push(`at least ${rules.min_length} characters`)
  if (rules.pattern) parts.push(`matching ${rules.pattern}`)
  if (field.unique) parts.push('unique across the fleet')
  return parts.length ? `Must be ${parts.join(', ')}.` : ''
}
