/**
 * Schema-driven columns for plain tables — the ones that are not grids.
 *
 * The device page lists its tests and the vendor claims that match it in
 * ordinary tables. They follow the same schema as the grids: a field hidden
 * from lists under Schema (or, for claims, a software version's own Customize
 * fields) is hidden here too, the order is the schema's, and custom fields get
 * a column. The page draws the cells; this decides which, and what they say.
 */
import type { EntityField } from './entityFields'

type ListField = Pick<EntityField, 'key' | 'label' | 'type' | 'storage' | 'list_visible'> & { visible?: boolean }

/** The fields a list shows, in order, less the ones the page already says. */
export function listFields<T extends ListField>(fields: T[], omit: string[] = []): T[] {
  return fields.filter((field) => field.visible !== false && field.list_visible && !omit.includes(field.key))
}

/** A field's value on a row; custom fields are stored in its misc data. */
export function fieldValue(row: any, field: ListField): any {
  return field.storage === 'data' ? row?.misc_data?.[field.key] : row?.[field.key]
}

/** Values too long for a cell, which a View button opens instead. */
export function opensDetail(field: ListField): boolean {
  return field.type === 'json' || field.type === 'textarea'
}

/** Whether a value counts as empty, for the N/A placeholder. */
export function isBlank(value: any): boolean {
  if (value == null || value === '') return true
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return false
}

/** A cell's text. Blank values come back empty for the caller's placeholder. */
export function fieldText(value: any, field: ListField, formatDay: (value: string) => string = String): string {
  if (isBlank(value)) return ''
  if (field.type === 'boolean') return value === true || value === 'true' ? 'Yes' : 'No'
  if (field.type === 'date') return formatDay(String(value))
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
